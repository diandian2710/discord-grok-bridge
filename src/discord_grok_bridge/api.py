from __future__ import annotations

import hmac
import re
import time

from aiohttp import web

from .config import Settings, digest
from .store import Rejected, Store


def build_app(store: Store, settings: Settings, health) -> web.Application:
    async def handle(request: web.Request):
        route = (request.method, request.path)
        admin = route in {("GET", "/v1/status"), ("POST", "/v1/recover")}
        if route not in {
            ("GET", "/v1/health"),
            ("GET", "/v1/status"),
            ("POST", "/v1/claim"),
            ("POST", "/v1/reply"),
            ("POST", "/v1/recover"),
        }:
            raise web.HTTPNotFound()
        header = request.headers.get("Authorization", "")
        if len(header) > 256 or not header.startswith("Bearer "):
            raise web.HTTPUnauthorized()
        token = header[7:]
        expected = digest(settings.admin_token) if admin else settings.agent_token_hash
        if not hmac.compare_digest(digest(token), expected):
            raise web.HTTPUnauthorized()
        if route == ("GET", "/v1/health"):
            return web.json_response(health())
        if route == ("GET", "/v1/status"):
            return web.json_response({**store.status(), **health()})
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="invalid JSON") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="expected JSON object")
        try:
            if request.path == "/v1/claim":
                if body:
                    raise Rejected("claim body must be empty", 422)
                store.revoke_unallowed(
                    settings.guild_id, settings.channel_id, settings.allowed_authors
                )
                return web.json_response({"job": store.claim()})
            if request.path == "/v1/reply":
                required = {"event_id", "generation", "reply_capability", "text", "no_reply"}
                if set(body) != required:
                    raise Rejected("invalid reply fields", 422)
                if not isinstance(body["event_id"], str) or not re.fullmatch(
                    r"[a-f0-9]{32}", body["event_id"]
                ):
                    raise Rejected("invalid event ID", 422)
                return web.json_response(
                    store.submit(
                        body["event_id"],
                        body["generation"],
                        body["reply_capability"],
                        body["text"],
                        body["no_reply"],
                    )
                )
            if set(body) != {"event_id", "action"} or not all(
                isinstance(v, str) and len(v) < 80 for v in body.values()
            ):
                raise Rejected("invalid recovery fields", 422)
            store.recover(body["event_id"], body["action"])
            return web.json_response({"ok": True})
        except Rejected as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)

    @web.middleware
    async def boundary(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({"error": exc.reason}, status=exc.status)
        except Exception:
            # Do not expose request content, credentials, exception URLs, or filesystem paths.
            response = web.json_response({"error": "internal storage error"}, status=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app = web.Application(client_max_size=100_000, middlewares=[boundary])
    app.router.add_route("*", "/{path:.*}", handle)
    return app


def worker_health(ready: bool, last_tick: float, catchup_error: bool):
    return {
        "discord_ready": ready,
        "worker_alive": time.time() - last_tick < 60,
        "catchup_ok": not catchup_error,
    }

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ConfigError, private_json, read_private


def profile():
    configured = os.environ.get("BRIDGE_AGENT_CONFIG")
    return (
        Path(configured) if configured else Path.home() / ".config/discord-grok-bridge/agent.json"
    )


async def agent_command(args, transport):
    cfg = read_private(args.agent_config or profile())
    if set(cfg) != {"schema", "port", "token"} or cfg["schema"] != "discord-grok.agent.v1":
        raise ConfigError("invalid helper profile")
    jobs_dir = (args.agent_config or profile()).resolve().parent / "jobs"
    if jobs_dir.is_dir() and not jobs_dir.is_symlink():
        for old_handle in jobs_dir.glob("*.json"):
            if old_handle.lstat().st_mtime < time.time() - 7 * 86400:
                old_handle.unlink()
    if args.command == "doctor":
        result = await transport(cfg["port"], cfg["token"], "GET", "/v1/health")
        print(json.dumps(result))
        return (
            0
            if all(result.get(k) is True for k in ("discord_ready", "worker_alive", "catchup_ok"))
            else 1
        )
    if args.command == "claim":
        result = await transport(cfg["port"], cfg["token"], "POST", "/v1/claim", {})
        job = result["job"]
        if job is None:
            print('{"job":null}')
            return 0
        # Persist capability privately; do not expose it in model output or command arguments.
        handle = (
            (args.agent_config or profile()).resolve().parent / "jobs" / (f"{job['event_id']}.json")
        )
        private_json(handle, job)
        visible = {k: v for k, v in job.items() if k != "reply_capability"}
        visible["handle"] = str(handle)
        print(json.dumps({"job": visible}, ensure_ascii=False))
        return 0
    handle = read_private(args.job)
    if handle.get("schema") != "discord-grok.job.v1":
        raise ConfigError("invalid job handle")
    text = "" if args.no_reply else sys.stdin.read(20001)
    if len(text) > 20000:
        raise ConfigError("reply exceeds 20000 characters")
    body = {k: handle[k] for k in ("event_id", "generation", "reply_capability")}
    body.update(text=text, no_reply=args.no_reply)
    receipt = await transport(cfg["port"], cfg["token"], "POST", "/v1/reply", body)
    # Keep only retry authorization; erase the input/history after accepted completion.
    private_json(
        args.job,
        {
            k: handle[k]
            for k in ("schema", "event_id", "generation", "reply_capability", "expires_at")
        },
    )
    print(json.dumps(receipt))
    return 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


def local_request(port, token, method, path, body=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ConfigError("invalid local API port")
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise ConfigError("invalid local API credential")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise ConfigError("local API did not return success")
            result = response.read(4_000_001)
            if len(result) > 4_000_000:
                raise ConfigError("local API response too large")
            return json.loads(result)
    except urllib.error.HTTPError as exc:
        raise ConfigError(f"local API returned HTTP {exc.code}; check bridge status") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ConfigError("cannot reach a valid local bridge API") from None


async def request(*args, **kwargs):
    return await asyncio.to_thread(local_request, *args, **kwargs)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="bridge-helper")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("claim", "reply", "doctor"):
        sub = commands.add_parser(command)
        sub.add_argument("--agent-config", type=Path)
        if command == "reply":
            sub.add_argument("--job", type=Path, required=True)
            sub.add_argument("--no-reply", action="store_true")
    args = parser.parse_args()
    try:
        code = asyncio.run(agent_command(args, request))
    except ConfigError as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        code = 130
    except Exception:
        print("bridge: helper operation failed", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()

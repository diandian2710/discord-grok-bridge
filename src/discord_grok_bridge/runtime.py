from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
import time
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from discord.http import Route

from .api import build_app, worker_health
from .config import Settings, private_dir
from .store import Rejected, Store

log = logging.getLogger("bridge")
CATCHUP_BATCH = 1000
CATCHUP_PAGES = 20


class InstanceLock:
    def __init__(self, directory: Path):
        private_dir(directory)
        self.fd = os.open(directory / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise RuntimeError("another bridge instance owns this state directory") from None

    def close(self):
        os.close(self.fd)


async def wake(session: aiohttp.ClientSession, settings: Settings, event: str):
    # Wake contains no user content, destination, callback URL, or reply capability.
    try:
        async with session.post(
            settings.webhook_url,
            json={"schema": "discord-grok.wake.v1", "event_id": event},
            headers={
                "Authorization": f"Bearer {settings.webhook_key}",
                "X-Automation-Key": settings.webhook_key,
            },
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if 200 <= response.status < 300:
                return "accepted"
            if response.status == 429:
                return "retry"  # Explicit rejection; no routine accepted this request.
            if response.status in (400, 401, 403, 404, 413, 422) or 300 <= response.status < 400:
                return "rejected"
            return "uncertain"
    except (aiohttp.ClientError, TimeoutError):
        return "uncertain"


class Bridge(discord.Client):
    def __init__(self, settings: Settings, store: Store, *, api_bind="127.0.0.1"):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            max_ratelimit_timeout=30,
        )
        self.api_bind = api_bind
        self.settings = settings
        self.store = store
        self.ready_for_work = False
        self.catchup_error = False
        self.last_tick = 0.0
        self.receive_lock = asyncio.Lock()
        self.worker_task = None
        self.session = None
        self.runner = None

    def health(self):
        return worker_health(
            self.ready_for_work and self.is_ready(), self.last_tick, self.catchup_error
        )

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(trust_env=False)
        self.runner = web.AppRunner(
            build_app(self.store, self.settings, self.health), access_log=None
        )
        await self.runner.setup()
        await web.TCPSite(self.runner, self.api_bind, self.settings.port).start()
        self.worker_task = asyncio.create_task(self.work())

    async def close(self):
        if self.worker_task:
            self.worker_task.cancel()
            await asyncio.gather(self.worker_task, return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()
        await super().close()

    async def on_disconnect(self):
        self.ready_for_work = False

    async def on_resumed(self):
        await self.on_ready()

    async def on_ready(self):
        async with self.receive_lock:
            self.ready_for_work = False
            try:
                channel = await self.fetch_channel(int(self.settings.channel_id))
                if (
                    not isinstance(channel, discord.TextChannel)
                    or str(channel.guild.id) != self.settings.guild_id
                ):
                    raise ValueError(
                        "configured channel is not a text channel in the allowed guild"
                    )
                permissions = channel.permissions_for(channel.guild.me)
                if not (
                    permissions.view_channel
                    and permissions.send_messages
                    and permissions.read_message_history
                ):
                    raise ValueError("missing channel permissions")
                marker = self.store.watermark()
                if marker is None:
                    # First run starts at the present rather than executing historic requests.
                    self.store.advance(str(channel.last_message_id or 1))
                else:
                    for _ in range(CATCHUP_PAGES):
                        messages = [
                            m
                            async for m in channel.history(
                                after=discord.Object(id=int(self.store.watermark())),
                                oldest_first=True,
                                limit=CATCHUP_BATCH,
                            )
                        ]
                        for message in messages:
                            if await self.receive(message) is False:
                                raise ValueError("catch-up admission failed")
                        if len(messages) < CATCHUP_BATCH:
                            break
                    else:
                        raise ValueError("catch-up batch limit reached; restart to continue")
                self.catchup_error = False
                self.ready_for_work = True
                log.info("gateway_ready catchup_complete")
            except Exception:
                self.catchup_error = True
                log.error("catchup_failed run_status_and_restart")

    async def on_message(self, message: discord.Message):
        async with self.receive_lock:
            if self.ready_for_work:
                await self.receive(message)

    async def receive(self, message: discord.Message):
        cfg = self.settings
        if not message.guild or str(message.guild.id) != cfg.guild_id:
            return
        if str(message.channel.id) != cfg.channel_id:
            return
        if (
            message.author.bot
            or str(message.author.id) != cfg.author_id
            or not self.user
            or self.user.id not in {u.id for u in message.mentions}
        ):
            self.store.advance(str(message.id))
            return
        text = re.sub(rf"<@!?{self.user.id}>", "", message.content, count=1)
        if message.attachments:
            rejection = "Text only: please send the request without attachments."
        else:
            rejection = None
        try:
            if rejection:
                raise Rejected(rejection, 422)
            event, fresh = self.store.add(
                str(message.id),
                cfg.guild_id,
                cfg.channel_id,
                cfg.author_id,
                text,
                timeout=cfg.timeout_seconds,
                limit=cfg.queue_limit,
                rate=cfg.rate_per_minute,
                ack=cfg.ack_message,
            )
            if fresh:
                log.info("accepted event=%s", event)
        except Rejected as exc:
            # Rejected work is never acknowledged as accepted or forwarded to Grok.
            self.store.advance(str(message.id))
            try:
                await message.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                    suppress_embeds=True,
                )
            except discord.DiscordException:
                log.warning("rejection_notice_failed")
        except Exception:
            # Stop admission until catch-up can recover from the persisted watermark.
            self.ready_for_work = False
            self.catchup_error = True
            log.error("admission_storage_failed restart_required")
            return False

    async def deliver(self, chunk):
        cfg = self.settings
        if (
            chunk["guild"] != cfg.guild_id
            or chunk["channel"] != cfg.channel_id
            or chunk["author"] != cfg.author_id
        ):
            self.store.send_error(chunk["id"], permanent=True)
            return
        try:
            result = await self.http.request(
                Route("POST", "/channels/{channel_id}/messages", channel_id=int(chunk["channel"])),
                json={
                    "content": chunk["text"],
                    "nonce": chunk["nonce"],
                    "enforce_nonce": True,
                    "allowed_mentions": {"parse": [], "replied_user": False},
                    "flags": 4,
                    "message_reference": {
                        "message_id": chunk["message"],
                        "channel_id": chunk["channel"],
                        "fail_if_not_exists": False,
                    },
                },
            )
            self.store.sent(chunk["id"], str(result["id"]))
        except (discord.Forbidden, discord.NotFound):
            self.store.send_error(chunk["id"], permanent=True)
        except discord.HTTPException as exc:
            self.store.send_error(chunk["id"], permanent=400 <= exc.status < 500)
        except Exception:
            self.store.send_error(chunk["id"])
            log.warning("delivery_retry_or_uncertain event=%s", chunk["event"])

    async def work(self):
        last_prune = 0
        while True:
            self.last_tick = time.time()
            try:
                self.store.expire()
                if time.time() - last_prune > 3600:
                    self.store.prune(self.settings.retention_days)
                    last_prune = time.time()
                if self.ready_for_work and self.is_ready():
                    # Do not block the local API or delivery while awaiting a webhook response.
                    event = self.store.prepare_wake()
                    if event:
                        result = await wake(self.session, self.settings, event)
                        self.store.wake_result(event, result)
                        log.info("wake event=%s result=%s", event, result)
                    chunk = self.store.prepare_send()
                    if chunk:
                        await self.deliver(chunk)
            except Exception:
                log.error("worker_storage_or_transport_error")
            await asyncio.sleep(0.5)


async def run(settings: Settings, directory: Path, *, container=False):
    if container and not Path("/.dockerenv").is_file():
        raise RuntimeError("container networking requires Docker")
    os.umask(0o077)
    lock = InstanceLock(directory)
    store = Store(directory / "inbox.db", settings.capability_secret)
    try:
        store.recover_restart()
        store.revoke_unallowed(settings.guild_id, settings.channel_id, settings.author_id)
        async with Bridge(
            settings, store, api_bind="0.0.0.0" if container else "127.0.0.1"
        ) as client:
            await client.start(settings.discord_token)
    finally:
        store.close()
        lock.close()

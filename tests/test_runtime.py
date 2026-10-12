from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import discord
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from discord_grok_bridge.runtime import Bridge, InstanceLock, wake

from .conftest import add


@pytest.fixture
async def bridge(store, settings):
    client = Bridge(settings, store)
    client._connection.user = SimpleNamespace(id=98765)
    yield client
    await client.close()


def message(
    *,
    guild="12345",
    channel="23456",
    author="34567",
    bot=False,
    mentioned=True,
    content="<@98765> hello",
    attachments=False,
    message_id="123456",
):
    return SimpleNamespace(
        id=int(message_id),
        guild=SimpleNamespace(id=int(guild)) if guild else None,
        channel=SimpleNamespace(id=int(channel)),
        author=SimpleNamespace(id=int(author), bot=bot),
        mentions=[SimpleNamespace(id=98765)] if mentioned else [],
        content=content,
        attachments=["file"] if attachments else [],
        reply=AsyncMock(),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"guild": None},
        {"guild": "45678"},
        {"channel": "45678"},
        {"author": "45678"},
        {"bot": True},
        {"mentioned": False},
    ],
)
async def test_unauthorized_input_never_reaches_inbox(bridge, store, change):
    await bridge.receive(message(**change))
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


async def test_persist_before_ack_and_no_inbound_token_dependency(bridge, store):
    incoming = message()
    await bridge.receive(incoming)
    assert not incoming.reply.called
    assert store.db.execute("SELECT text FROM jobs").fetchone()[0] == " hello"
    ack = store.prepare_send()
    assert ack["text"] == "Queued."


async def test_attachments_rejected_with_notice(bridge, store):
    incoming = message(attachments=True)
    await bridge.receive(incoming)
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    incoming.reply.assert_awaited_once()


async def test_disk_failure_stops_admission_and_does_not_ack(bridge, store):
    bridge.ready_for_work = True
    incoming = message()
    store.db.execute(
        "CREATE TRIGGER fail_add BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'disk full'); END"
    )
    await bridge.receive(incoming)
    assert not incoming.reply.called
    assert store.watermark() is None
    assert bridge.ready_for_work is False


async def test_delivery_destination_nonce_mentions_and_deleted_reference(bridge, store):
    add(store)
    chunk = store.prepare_send()
    bridge.http.request = AsyncMock(return_value={"id": "88888"})
    await bridge.deliver(chunk)
    payload = bridge.http.request.await_args.kwargs["json"]
    assert payload["allowed_mentions"] == {"parse": [], "replied_user": False}
    assert payload["nonce"] == chunk["nonce"] and payload["enforce_nonce"]
    assert payload["flags"] == 4
    assert payload["message_reference"]["fail_if_not_exists"] is False
    assert store.db.execute("SELECT discord_id FROM outbox").fetchone()[0] == "88888"


async def test_revoked_destination_never_sent(bridge, store):
    store.add("123456", "12345", "wrong-channel", "34567", "hi")
    chunk = store.prepare_send()
    bridge.http.request = AsyncMock()
    await bridge.deliver(chunk)
    bridge.http.request.assert_not_called()
    assert store.job(chunk["event"])["state"] == "failed"


async def test_transport_failure_reuses_nonce(bridge, store, clock):
    add(store)
    chunk = store.prepare_send()
    bridge.http.request = AsyncMock(side_effect=TimeoutError())
    await bridge.deliver(chunk)
    clock[0] += 5
    assert store.prepare_send()["nonce"] == chunk["nonce"]


async def test_permanent_discord_error_is_recorded(bridge, store):
    add(store)
    chunk = store.prepare_send()
    error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "blocked")
    bridge.http.request = AsyncMock(side_effect=error)
    await bridge.deliver(chunk)
    assert store.job(chunk["event"])["error"] == "discord_rejected"


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, "accepted"),
        (204, "accepted"),
        (401, "rejected"),
        (403, "rejected"),
        (302, "rejected"),
        (429, "retry"),
        (503, "uncertain"),
    ],
)
async def test_wake_status_and_body_contract(status, expected):
    seen = []

    async def handler(request):
        seen.append(await request.json())
        return web.Response(status=status)

    app = web.Application()
    app.router.add_post("/", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        cfg = SimpleNamespace(webhook_url=str(server.make_url("/")), webhook_key="fake-key")
        assert await wake(session, cfg, "event") == expected
    assert seen == [{"schema": "discord-grok.wake.v1", "event_id": "event"}]


async def test_redirect_cannot_forward_webhook_credentials():
    seen = []

    async def target(request):
        seen.append(request.headers.get("Authorization"))
        return web.Response()

    destination = web.Application()
    destination.router.add_route("*", "/", target)
    async with TestServer(destination) as target_server:

        async def redirect(request):
            raise web.HTTPTemporaryRedirect(str(target_server.make_url("/")))

        source = web.Application()
        source.router.add_post("/", redirect)
        async with TestServer(source) as source_server, aiohttp.ClientSession() as session:
            cfg = SimpleNamespace(
                webhook_url=str(source_server.make_url("/")), webhook_key="fake-key"
            )
            assert await wake(session, cfg, "event") == "rejected"
    assert seen == []


def test_single_instance_lock(tmp_path):
    lock = InstanceLock(tmp_path / "state")
    try:
        with pytest.raises(RuntimeError, match="another bridge"):
            InstanceLock(tmp_path / "state")
    finally:
        lock.close()


class HistoryChannel:
    def __init__(self, messages, last=123455):
        self.messages = messages
        self.last_message_id = last
        self.guild = SimpleNamespace(id=12345, me=SimpleNamespace())

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=True, send_messages=True, read_message_history=True)

    async def history(self, *, after, oldest_first, limit):
        assert oldest_first
        for value in [m for m in self.messages if m.id > after.id][:limit]:
            yield value


async def test_first_start_does_not_execute_old_messages(bridge, store, monkeypatch):
    from discord_grok_bridge import runtime

    channel = HistoryChannel([message()], last=123456)
    monkeypatch.setattr(runtime.discord, "TextChannel", HistoryChannel)
    bridge.fetch_channel = AsyncMock(return_value=channel)
    await bridge.on_ready()
    assert bridge.ready_for_work
    assert store.watermark() == "123456"
    assert store.claim() is None


async def test_ordered_offline_catchup_deduplicates_live_replay(bridge, store, monkeypatch):
    from discord_grok_bridge import runtime

    store.advance("123455")
    messages = [message(message_id=str(i)) for i in (123456, 123457)]
    channel = HistoryChannel(messages)
    monkeypatch.setattr(runtime.discord, "TextChannel", HistoryChannel)
    bridge.fetch_channel = AsyncMock(return_value=channel)
    await bridge.on_ready()
    assert bridge.ready_for_work
    await bridge.on_message(messages[0])
    assert store.db.execute("SELECT message FROM jobs ORDER BY seq").fetchall()[0][0] == "123456"
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    assert store.watermark() == "123457"


async def test_catchup_storage_failure_does_not_advance_past_lost_work(bridge, store, monkeypatch):
    from discord_grok_bridge import runtime

    store.advance("123455")
    channel = HistoryChannel([message(), message(message_id="123457", mentioned=False)])
    monkeypatch.setattr(runtime.discord, "TextChannel", HistoryChannel)
    bridge.fetch_channel = AsyncMock(return_value=channel)
    store.db.execute(
        "CREATE TRIGGER fail_add BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'disk full'); END"
    )
    await bridge.on_ready()
    assert not bridge.ready_for_work and bridge.catchup_error
    assert store.watermark() == "123455"


async def test_bounded_catchup_leaves_service_paused_until_continued(bridge, store, monkeypatch):
    from discord_grok_bridge import runtime

    store.advance("123455")
    channel = HistoryChannel([message(message_id=str(i)) for i in range(123456, 123459)])
    monkeypatch.setattr(runtime.discord, "TextChannel", HistoryChannel)
    monkeypatch.setattr(runtime, "CATCHUP_BATCH", 2)
    monkeypatch.setattr(runtime, "CATCHUP_PAGES", 1)
    bridge.fetch_channel = AsyncMock(return_value=channel)
    await bridge.on_ready()
    assert not bridge.ready_for_work and bridge.catchup_error
    assert store.watermark() == "123457"
    await bridge.on_ready()
    assert bridge.ready_for_work
    assert store.watermark() == "123458"

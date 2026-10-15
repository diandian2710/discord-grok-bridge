import pytest
from aiohttp.test_utils import TestClient, TestServer

from discord_grok_bridge.api import build_app

from .conftest import add


@pytest.fixture
async def client(store, settings):
    async with TestClient(
        TestServer(build_app(store, settings, lambda: {"discord_ready": True}))
    ) as c:
        yield c


def auth():
    return {"Authorization": "Bearer " + "a" * 40}


@pytest.mark.parametrize("header", ["", "Bearer wrong", "Basic xyz", "Bearer " + "a" * 300])
async def test_auth_required_even_locally(client, header):
    response = await client.post("/v1/claim", json={}, headers={"Authorization": header})
    assert response.status == 401


async def test_helper_cannot_access_admin_recovery_or_status(client, store):
    event = add(store)
    response = await client.post(
        "/v1/recover", json={"event_id": event, "action": "skip"}, headers=auth()
    )
    assert response.status == 401
    assert (await client.get("/v1/status", headers=auth())).status == 401
    assert (await client.get("/v1/health", headers=auth())).status == 200


async def test_exact_reply_fields_reject_destination(client, store):
    add(store)
    response = await client.post("/v1/claim", json={}, headers=auth())
    job = (await response.json())["job"]
    body = {k: job[k] for k in ("event_id", "generation", "reply_capability")}
    body.update(text="answer", no_reply=False, channel_id="arbitrary")
    assert (await client.post("/v1/reply", json=body, headers=auth())).status == 422
    del body["channel_id"]
    assert (await client.post("/v1/reply", json=body, headers=auth())).status == 200
    assert (await client.post("/v1/reply", json=body, headers=auth())).status == 200
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2


@pytest.mark.parametrize("body", [[], None, "text", {"generation": 1}])
async def test_bad_payload(client, body):
    assert (await client.post("/v1/reply", json=body, headers=auth())).status in (400, 422)


async def test_body_limit(client):
    response = await client.post("/v1/reply", data="x" * 100001, headers=auth())
    assert response.status == 413
    assert response.headers["Cache-Control"] == "no-store"


async def test_errors_do_not_expose_storage_detail(client, store):
    store.db.execute(
        "CREATE TRIGGER fail_claim BEFORE UPDATE ON jobs BEGIN "
        "SELECT RAISE(ABORT,'secret in exception'); END"
    )
    add(store)
    response = await client.post("/v1/claim", json={}, headers=auth())
    assert response.status == 500
    assert "secret" not in await response.text()


async def test_mock_http_roundtrip_through_real_helper(
    client, settings, store, tmp_path, monkeypatch, capsys
):
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock

    from discord_grok_bridge import cli
    from discord_grok_bridge.config import private_json, read_private
    from discord_grok_bridge.runtime import Bridge

    profile = tmp_path / "agent/agent.json"
    private_json(profile, {"schema": "discord-grok.agent.v1", "port": 18792, "token": "a" * 40})

    async def local_request(port, token, method, path, body=None):
        response = await client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status == 200
        return await response.json()

    monkeypatch.setattr(cli, "request", local_request)
    event = add(store)
    await cli.agent_command(cli.parser().parse_args(["claim", "--agent-config", str(profile)]))
    claimed = json.loads(capsys.readouterr().out)["job"]
    monkeypatch.setattr(cli.sys, "stdin", __import__("io").StringIO("hello from mock Grok"))
    await cli.agent_command(
        cli.parser().parse_args(
            ["reply", "--agent-config", str(profile), "--job", claimed["handle"]]
        )
    )
    assert json.loads(capsys.readouterr().out)["ok"]
    assert "text" not in read_private(Path(claimed["handle"]))
    bridge = Bridge(settings, store)
    bridge.http.request = AsyncMock(return_value={"id": "98765"})
    try:
        while chunk := store.prepare_send():
            await bridge.deliver(chunk)
    finally:
        await bridge.close()
    assert store.job(event)["state"] == "delivered"
    assert bridge.http.request.await_count == 2


async def test_claim_revokes_queued_work_outside_current_policy(client, store):
    event = store.add("123456", "12345", "wrong-channel", "34567", "private text")[0]
    response = await client.post("/v1/claim", json={}, headers=auth())
    assert (await response.json())["job"] is None
    assert store.job(event)["error"] == "policy_changed"
    assert store.prepare_send() is None


async def test_stdlib_host_helper_accepts_maximum_unicode_reply(client, store):
    from discord_grok_bridge import helper

    add(store)
    port = client.server.port
    job = (await helper.request(port, "a" * 40, "POST", "/v1/claim", {}))["job"]
    body = {k: job[k] for k in ("event_id", "generation", "reply_capability")}
    body.update(text="🦉" * 20000, no_reply=False)
    receipt = await helper.request(port, "a" * 40, "POST", "/v1/reply", body)
    assert receipt["ok"]
    assert store.job(job["event_id"])["outcome"] == body["text"]


async def test_stdlib_helper_reads_bounded_large_unicode_history(client, store):
    from discord_grok_bridge import helper

    for index in range(6):
        add(store, message=str(123456 + index))
        job = store.claim()
        store.submit(
            job["event_id"], job["generation"], job["reply_capability"], "🦉" * 20000, False
        )
        while chunk := store.prepare_send():
            store.sent(chunk["id"], "98765")
    add(store, message="123499")
    job = (await helper.request(client.server.port, "a" * 40, "POST", "/v1/claim", {}))["job"]
    assert len(job["history"]) == 6
    assert job["history"][0]["assistant"] == "🦉" * 20000


async def test_claim_includes_author_and_owner_role(client, store):
    add(store)
    job = (await (await client.post("/v1/claim", json={}, headers=auth())).json())["job"]
    assert job["author_id"] == "34567" and job["role"] == "owner"

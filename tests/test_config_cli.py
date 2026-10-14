import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from discord_grok_bridge import cli
from discord_grok_bridge.config import ConfigError, private_json, read_private, webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "http://api2.cursor.sh/automations/webhook/x",
        "https://evil.test/automations/webhook/x",
        "https://api2.cursor.sh.evil.test/automations/webhook/x",
        "https://user:pass@api2.cursor.sh/automations/webhook/x",
        "https://api2.cursor.sh/automations/webhook/x?secret=x",
        "https://api2.cursor.sh:8443/automations/webhook/x",
    ],
)
def test_webhook_destination_restricted(url):
    with pytest.raises(ConfigError):
        webhook_url(url)


@pytest.mark.parametrize(
    "change",
    [
        {"guild_id": ""},
        {"author_id": "name"},
        {"discord_token": ""},
        {"webhook_key": ""},
        {"agent_token_hash": ""},
        {"port": True},
        {"timeout_seconds": 0},
    ],
)
def test_configuration_fails_closed(settings, change):
    with pytest.raises(ConfigError):
        replace(settings, **change).validate()


def test_private_files_and_symlinks(tmp_path):
    file = tmp_path / "secrets/profile.json"
    private_json(file, {"hello": "world"})
    assert file.stat().st_mode & 0o777 == 0o600
    assert file.parent.stat().st_mode & 0o777 == 0o700
    file.chmod(0o644)
    with pytest.raises(ConfigError):
        read_private(file)
    link = tmp_path / "link"
    link.symlink_to(file)
    with pytest.raises(ConfigError):
        read_private(link)


async def test_claim_stdout_does_not_disclose_capability_or_token(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "agent/agent.json"
    private_json(profile, {"schema": "discord-grok.agent.v1", "port": 18792, "token": "secret" * 8})
    job = {
        "schema": "discord-grok.job.v1",
        "event_id": "a" * 32,
        "generation": 1,
        "reply_capability": "capability-secret",
        "text": "hello",
    }
    monkeypatch.setattr(cli, "request", AsyncMock(return_value={"job": job}))
    args = cli.parser().parse_args(["claim", "--agent-config", str(profile)])
    assert await cli.agent_command(args) == 0
    output = capsys.readouterr().out
    assert "capability-secret" not in output and "secretsecret" not in output
    handle = json.loads(output)["job"]["handle"]
    assert (
        read_private(__import__("pathlib").Path(handle))["reply_capability"] == "capability-secret"
    )


def test_mock_pipeline_runs_without_live_credentials(tmp_path, capsys):
    assert cli.mock_roundtrip(tmp_path / "state") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["messages"] == ["Mock Grok reply"]


async def test_recovery_requires_explicit_duplicate_risk(settings, tmp_path, monkeypatch):
    path = tmp_path / "service/service.json"
    settings.save(path)
    fake_request = AsyncMock()
    monkeypatch.setattr(cli, "request", fake_request)
    args = cli.parser().parse_args(
        ["recover", "--config", str(path), "--event", "a" * 32, "--action", "retry-wake"]
    )
    with pytest.raises(ConfigError, match="accept-duplicate-risk"):
        await cli.admin_command(args)
    fake_request.assert_not_called()


def test_ack_message_defaults_off_and_must_be_bool(settings):
    assert settings.validate().ack_message is False
    with pytest.raises(ConfigError, match="ack_message"):
        replace(settings, ack_message="yes").validate()


def test_debug_defaults_off_and_must_be_bool(settings):
    assert settings.validate().debug is False
    with pytest.raises(ConfigError, match="debug"):
        replace(settings, debug="1").validate()


def test_authorized_user_ids_default_and_validation(settings):
    assert settings.validate().allowed_authors == {"34567"}
    extra = replace(settings, authorized_user_ids=["45678"]).validate()
    assert extra.allowed_authors == {"34567", "45678"}
    for bad in ("45678", ["name"], [45678], ["45678"] * 26):
        with pytest.raises(ConfigError, match="authorized_user_ids"):
            replace(settings, authorized_user_ids=bad).validate()


def test_old_config_without_authorized_user_ids_loads(settings, tmp_path):
    from dataclasses import asdict

    from discord_grok_bridge.config import Settings

    data = asdict(settings)
    del data["authorized_user_ids"]
    path = tmp_path / "service.json"
    private_json(path, data)
    assert Settings.load(path).allowed_authors == {"34567"}

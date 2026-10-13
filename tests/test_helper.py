import os
import subprocess
import sys
from pathlib import Path

import pytest

from discord_grok_bridge import cli, helper, runtime
from discord_grok_bridge.config import ConfigError


@pytest.mark.parametrize("port,token", [(True, "a" * 40), (80, "a" * 40), (18792, "short")])
def test_stdlib_helper_rejects_invalid_local_configuration(port, token):
    with pytest.raises(ConfigError):
        helper.local_request(port, token, "GET", "/v1/health")


def test_helper_help_without_site_packages():
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-S", "-m", "discord_grok_bridge.helper", "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "claim" in result.stdout and "reply" in result.stdout


async def test_host_cannot_opt_into_container_networking(settings, tmp_path, monkeypatch):
    original = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: False if str(path) == "/.dockerenv" else original(path)
    )
    with pytest.raises(RuntimeError, match="requires Docker"):
        await runtime.run(settings, tmp_path / "state", container=True)
    assert not (tmp_path / "state").exists()


async def test_container_health_uses_admin_credentials_without_printing_them(
    settings, tmp_path, monkeypatch, capsys
):
    from unittest.mock import AsyncMock

    path = tmp_path / "service.json"
    settings.save(path)
    transport = AsyncMock(
        return_value={"discord_ready": True, "worker_alive": True, "catchup_ok": True}
    )
    monkeypatch.setattr(cli, "request", transport)
    args = cli.parser().parse_args(["container-health", "--config", str(path)])
    assert await cli.admin_command(args) == 0
    assert transport.await_args.args[1] == settings.admin_token
    assert capsys.readouterr().out == ""
    transport.return_value["discord_ready"] = False
    assert await cli.admin_command(args) == 1

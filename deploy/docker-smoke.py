#!/usr/bin/env python3
"""Test Docker with fake transports and disposable state; never consumes real credentials."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, input=None):
    result = subprocess.run(["docker", *args], input=input, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Docker command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def helper(profile, *args, text=None):
    result = subprocess.run(
        [str(ROOT / "deploy/bridge"), *args, "--agent-config", str(profile)],
        input=text,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"Host helper failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


SERVER = """
import asyncio
from pathlib import Path
from discord_grok_bridge.config import Settings
from discord_grok_bridge.store import Store
from discord_grok_bridge import runtime

async def main():
    cfg = Settings.load(Path('/state/service.json'))
    store = Store(Path('/state/inbox.db'), cfg.capability_secret)
    store.recover_restart()
    store.add('123456', cfg.guild_id, cfg.channel_id, cfg.author_id, 'Docker round-trip')
    async def fake_wake(*args): return 'accepted'
    async def fake_discord(*args, **kwargs): return {'id': '98765'}
    runtime.wake = fake_wake
    bridge = runtime.Bridge(cfg, store, api_bind='0.0.0.0')
    bridge.http.request = fake_discord
    bridge.is_ready = lambda: True
    bridge.ready_for_work = True
    await bridge.setup_hook()
    try: await asyncio.Event().wait()
    finally:
        await bridge.close()
        store.close()
asyncio.run(main())
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="discord-grok-bridge:local")
    parser.add_argument("--work-dir", type=Path, default=Path("work"))
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    identifier = "bridge-smoke-" + uuid.uuid4().hex[:12]
    volume, container = identifier + "-state", None
    docker("volume", "create", volume)
    try:
        with tempfile.TemporaryDirectory(dir=args.work_dir.resolve()) as folder:
            agent = Path(folder) / "agent"
            agent.mkdir(mode=0o700)
            init = f"""
import argparse, getpass, builtins
from discord_grok_bridge.cli import setup
values=iter(['fake-discord-token', 'https://api2.cursor.sh/automations/webhook/test', 'fake-key'])
ids=iter(['12345','23456','34567'])
getpass.getpass=lambda prompt: next(values)
builtins.input=lambda prompt: next(ids)
setup(argparse.Namespace(container=True, development=False, agent_user=None, config=None,
                        agent_config=None, agent_uid={os.getuid()}, agent_gid={os.getgid()}))
"""
            docker(
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "DAC_OVERRIDE",
                "--cap-add",
                "FOWNER",
                "--mount",
                f"type=volume,source={volume},target=/state",
                "--mount",
                f"type=bind,source={agent},target=/agent",
                "--entrypoint",
                "python",
                args.image,
                "-",
                input=init,
            )
            profile = agent / "agent.json"
            assert profile.stat().st_mode & 0o777 == 0o600
            assert set(json.loads(profile.read_text())) == {"schema", "port", "token"}
            assert "discord_token" not in profile.read_text()
            for iteration in range(2):
                container = docker(
                    "run",
                    "-d",
                    "--init",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--pids-limit",
                    "128",
                    "--memory",
                    "256m",
                    "--tmpfs",
                    "/tmp:size=16m,mode=1777",
                    "--health-interval",
                    "2s",
                    "--health-start-period",
                    "0s",
                    "-p",
                    "127.0.0.1::18792",
                    "--mount",
                    f"type=volume,source={volume},target=/state",
                    "--entrypoint",
                    "python",
                    args.image,
                    "-c",
                    SERVER,
                )
                details = json.loads(docker("inspect", container))[0]
                assert details["Config"]["User"] == "10001:10001"
                assert details["HostConfig"]["ReadonlyRootfs"]
                assert details["HostConfig"]["CapDrop"] == ["ALL"]
                mappings = details["NetworkSettings"]["Ports"]["18792/tcp"]
                assert all(p["HostIp"] == "127.0.0.1" for p in mappings)
                profile_data = json.loads(profile.read_text())
                profile_data["port"] = int(mappings[0]["HostPort"])
                profile.write_text(json.dumps(profile_data))
                for _ in range(100):
                    details = json.loads(docker("inspect", container))[0]
                    if details["State"].get("Health", {}).get("Status") == "healthy":
                        break
                    if not details["State"]["Running"]:
                        raise RuntimeError("Smoke server exited before becoming healthy")
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Container healthcheck did not pass")
                assert helper(profile, "doctor")["discord_ready"]
                unauth = urllib.request.Request(
                    f"http://127.0.0.1:{profile_data['port']}/v1/claim",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
                try:
                    urllib.request.urlopen(unauth, timeout=5)
                    raise AssertionError("API accepted an unauthenticated claim")
                except urllib.error.HTTPError as error:
                    assert error.code == 401
                if iteration == 0:
                    job = helper(profile, "claim")["job"]
                    assert job["text"] == "Docker round-trip"
                    receipt = helper(
                        profile, "reply", "--job", job["handle"], text="Mock Docker answer"
                    )
                    assert receipt["ok"] and not receipt["duplicate"]
                    assert helper(
                        profile, "reply", "--job", job["handle"], text="Mock Docker answer"
                    )["duplicate"]
                    for _ in range(100):
                        state = docker(
                            "exec",
                            container,
                            "python",
                            "-c",
                            "import sqlite3; print(sqlite3.connect('/state/inbox.db')"
                            ".execute('SELECT state FROM jobs').fetchone()[0])",
                        )
                        if state == "delivered":
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("Mock answer was not delivered")
                else:
                    assert helper(profile, "claim")["job"] is None
                    state = docker(
                        "exec",
                        container,
                        "python",
                        "-c",
                        "import sqlite3; print(sqlite3.connect('/state/inbox.db')"
                        ".execute('SELECT state FROM jobs').fetchone()[0])",
                    )
                    assert state == "delivered"
                docker("stop", "--time", "5", container)
                docker("rm", container)
                container = None
            print(
                "Docker smoke passed: non-root, readonly, authenticated host helper, "
                "healthcheck, duplicate reply, and volume persistence after recreation."
            )
    finally:
        if container:
            docker("rm", "-f", container)
        docker("volume", "rm", volume)


if __name__ == "__main__":
    main()

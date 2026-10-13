from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import pwd
import secrets
import sys
import uuid
from pathlib import Path

import aiohttp

from . import helper
from .config import ConfigError, Settings, digest, private_json
from .runtime import run
from .store import Store


async def request(port: int, token: str, method: str, path: str, body=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ConfigError("invalid local API port")
    if not isinstance(token, str) or len(token) < 32 or len(token) > 128:
        raise ConfigError("invalid local API credential")
    async with aiohttp.ClientSession(
        trust_env=False, json_serialize=lambda value: json.dumps(value, ensure_ascii=False)
    ) as session:
        async with session.request(
            method,
            f"http://127.0.0.1:{port}{path}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                raise ConfigError(f"local API returned HTTP {response.status}; check bridge status")
            return await response.json()


def setup(args):
    if args.container:
        if not Path("/.dockerenv").is_file() or os.geteuid() != 0:
            raise ConfigError("container setup must run as root inside Docker")
        if (
            args.agent_uid is None
            or args.agent_gid is None
            or not (1 <= args.agent_uid <= 2**31 - 1 and 0 <= args.agent_gid <= 2**31 - 1)
        ):
            raise ConfigError("container setup requires an unprivileged helper UID and GID")
        if args.development or args.agent_user or args.config or args.agent_config:
            raise ConfigError("container setup cannot override its secret or helper paths")
        service_path = Path("/state/service.json")
        agent_path = Path("/agent/agent.json")
        service_uid, agent_uid = 10001, args.agent_uid
    elif args.development:
        service_path = args.config or Path("work/bridge-local/service.json").resolve()
        agent_path = args.agent_config or service_path.parent / "agent.json"
        service_uid = agent_uid = None
        print("Development mode trusts every process under your OS account.")
    else:
        if sys.platform != "linux" or os.geteuid() != 0 or not args.agent_user:
            raise ConfigError(
                "secure setup requires Linux root and --agent-user; use the installer"
            )
        agent = pwd.getpwnam(args.agent_user)
        service = pwd.getpwnam("discord-grok-bridge")
        if agent.pw_uid == 0 or agent.pw_uid == service.pw_uid:
            raise ConfigError("Grok must run as a separate unprivileged user")
        service_path = Path("/var/lib/discord-grok-bridge/service.json")
        agent_path = Path(agent.pw_dir) / ".config/discord-grok-bridge/agent.json"
        service_uid, agent_uid = service.pw_uid, agent.pw_uid
    if service_path.exists() or agent_path.exists():
        raise ConfigError("configuration already exists; setup will not overwrite credentials")
    if service_path.resolve().parent == agent_path.resolve().parent and not args.development:
        raise ConfigError("service and agent secrets must use separate directories")
    print("Invite a Discord bot with View Channels, Send Messages, and Read Message History.")
    print("Use a private text channel. Leave privileged intents disabled.")
    token = getpass.getpass("Discord bot token (hidden): ")
    guild = input("Discord server ID: ").strip()
    channel = input("Discord channel ID: ").strip()
    author = input("Your Discord user ID: ").strip()
    hook = getpass.getpass("Grok routine webhook URL (hidden): ").strip()
    key = getpass.getpass("Grok routine webhook key (hidden): ")
    agent_token = secrets.token_urlsafe(32)
    cfg = Settings(
        token,
        guild,
        channel,
        author,
        hook,
        key,
        digest(agent_token),
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
    ).validate()
    cfg.save(service_path)
    agent_profile = {"schema": "discord-grok.agent.v1", "port": cfg.port, "token": agent_token}
    if service_uid is None:
        private_json(agent_path, agent_profile)
    elif args.container:
        os.chown(service_path.parent, 10001, 10001)
        os.chown(service_path, 10001, 10001)
        try:
            private_json(agent_path, agent_profile)
            os.chown(agent_path.parent, args.agent_uid, args.agent_gid)
            os.chown(agent_path, args.agent_uid, args.agent_gid)
        except Exception:
            service_path.unlink(missing_ok=True)
            agent_path.unlink(missing_ok=True)
            raise ConfigError("cannot write Docker helper profile; setup rolled back") from None
    else:
        # Never write into an agent-controlled home directory with root privileges.
        child = os.fork()
        if child == 0:
            try:
                account = pwd.getpwuid(agent_uid)
                os.initgroups(account.pw_name, account.pw_gid)
                os.setgid(account.pw_gid)
                os.setuid(account.pw_uid)
                private_json(agent_path, agent_profile)
                os._exit(0)
            except Exception:
                os._exit(1)
        _, outcome = os.waitpid(child, 0)
        if outcome != 0:
            # Leave no half-configured service credential after a failed helper write.
            service_path.unlink(missing_ok=True)
            raise ConfigError("cannot write helper profile under Grok's unprivileged account")
        service_account = pwd.getpwuid(service_uid)
        os.chown(service_path.parent, service_uid, service_account.pw_gid)
        os.chown(service_path, service_uid, service_account.pw_gid)
    print(f"Saved service configuration: {service_path}")
    print(f"Saved helper profile: {agent_path}")
    print("Copy prompts/grok-routine.md into the Grok webhook routine, then start the service.")
    print(
        "Send a Discord @mention and check the final reply. Compatibility is unverified until then."
    )


async def agent_command(args):
    return await helper.agent_command(args, request)


async def admin_command(args):
    path = args.config or Path(
        "/state/service.json"
        if args.command == "container-health"
        else "/var/lib/discord-grok-bridge/service.json"
    )
    cfg = Settings.load(path)
    if args.command == "container-health":
        result = await request(cfg.port, cfg.admin_token, "GET", "/v1/status")
        return (
            0
            if all(result.get(k) is True for k in ("discord_ready", "worker_alive", "catchup_ok"))
            else 1
        )
    if args.command == "run":
        await run(cfg, path.parent, container=args.container)
        return 0
    if args.command == "status":
        result = await request(cfg.port, cfg.admin_token, "GET", "/v1/status")
    else:
        if args.action in ("retry-wake", "retry-send") and not args.accept_duplicate_risk:
            raise ConfigError("retry requires --accept-duplicate-risk after inspecting the outcome")
        result = await request(
            cfg.port,
            cfg.admin_token,
            "POST",
            "/v1/recover",
            {"event_id": args.event, "action": args.action},
        )
    print(json.dumps(result, indent=2))
    return 0


def mock_roundtrip(directory: Path):
    from .runtime import InstanceLock

    lock = InstanceLock(directory)
    store = Store(directory / "inbox.db", "mock-secret")
    try:
        event, _ = store.add(str(uuid.uuid4().int), "12345", "23456", "34567", "Test request")
        job = store.claim()
        store.submit(event, job["generation"], job["reply_capability"], "Mock Grok reply", False)
        delivered = []
        while chunk := store.prepare_send():
            delivered.append(chunk["text"])
            store.sent(chunk["id"], str(uuid.uuid4().int))
        assert store.job(event)["state"] == "delivered"
        print(json.dumps({"ok": True, "transport": "mock", "messages": delivered}))
    finally:
        store.close()
        lock.close()
    return 0


def parser():
    result = argparse.ArgumentParser(prog="bridge")
    commands = result.add_subparsers(dest="command", required=True)
    wizard = commands.add_parser("setup", help="configure the local service and helper")
    wizard.add_argument("--agent-user")
    wizard.add_argument("--container", action="store_true")
    wizard.add_argument("--agent-uid", type=int)
    wizard.add_argument("--agent-gid", type=int)
    wizard.add_argument("--development", action="store_true")
    wizard.add_argument("--config", type=Path)
    wizard.add_argument("--agent-config", type=Path)
    for command in ("claim", "reply", "doctor"):
        sub = commands.add_parser(command)
        sub.add_argument("--agent-config", type=Path)
        if command == "reply":
            sub.add_argument("--job", type=Path, required=True)
            sub.add_argument("--no-reply", action="store_true")
    for command in ("run", "status", "recover", "container-health"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", type=Path)
        if command == "run":
            sub.add_argument("--container", action="store_true")
        if command == "recover":
            sub.add_argument("--event", required=True)
            sub.add_argument(
                "--action", choices=("retry-wake", "retry-send", "skip"), required=True
            )
            sub.add_argument("--accept-duplicate-risk", action="store_true")
    mock = commands.add_parser("mock", help="exercise the durable pipeline without credentials")
    mock.add_argument("--state-dir", type=Path, default=Path("work/mock-bridge"))
    return result


def main():
    os.umask(0o077)
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Avoid library tracebacks that may contain remote URLs or request bodies.
    logging.getLogger("discord").setLevel(logging.CRITICAL)
    try:
        if args.command == "setup":
            setup(args)
            code = 0
        elif args.command == "mock":
            code = mock_roundtrip(args.state_dir)
        elif args.command in ("claim", "reply", "doctor"):
            code = asyncio.run(agent_command(args))
        else:
            code = asyncio.run(admin_command(args))
    except KeyboardInterrupt:
        code = 130
    except (ConfigError, RuntimeError) as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        code = 1
    except Exception:
        print(
            "bridge: operation failed; inspect configuration and authenticated status",
            file=sys.stderr,
        )
        code = 1
    sys.exit(code)

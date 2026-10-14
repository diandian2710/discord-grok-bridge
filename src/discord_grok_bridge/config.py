from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ConfigError("private directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def private_json(path: Path, value: dict) -> None:
    private_dir(path.parent)
    temporary = path.parent / f".tmp-{secrets.token_hex(8)}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_private(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("private config must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigError("private config permissions must be 0600")
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise ConfigError("cannot read private config") from exc
    if not isinstance(data, dict):
        raise ConfigError("config must be an object")
    return data


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def webhook_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api2.cursor.sh"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/automations/webhook/[A-Za-z0-9_-]+", parsed.path)
    ):
        raise ConfigError("expected an HTTPS api2.cursor.sh/automations/webhook/<id> URL")
    return value


def valid_id(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]{4,19}", value))


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_id: str
    channel_id: str
    author_id: str
    webhook_url: str
    webhook_key: str
    agent_token_hash: str
    admin_token: str
    capability_secret: str
    port: int = 18792
    timeout_seconds: int = 900
    queue_limit: int = 100
    rate_per_minute: int = 10
    retention_days: int = 7
    # Post an interim "Queued." reply before Grok answers. Off: only the final answer is posted.
    ack_message: bool = False
    # Log one line (ID and reason, never text) per ignored message. Also BRIDGE_DEBUG=1.
    debug: bool = False
    # Extra Discord user IDs allowed to use the bridge, in addition to author_id (the owner).
    authorized_user_ids: list[str] = dataclasses.field(default_factory=list)

    @property
    def allowed_authors(self) -> frozenset[str]:
        return frozenset([self.author_id, *self.authorized_user_ids])

    def validate(self) -> Settings:
        if not all(valid_id(x) for x in (self.guild_id, self.channel_id, self.author_id)):
            raise ConfigError("guild, channel, and owner IDs must be Discord snowflakes")
        if (
            not isinstance(self.authorized_user_ids, list)
            or len(self.authorized_user_ids) > 25
            or not all(valid_id(x) for x in self.authorized_user_ids)
        ):
            raise ConfigError("authorized_user_ids must be a list of up to 25 Discord user IDs")
        webhook_url(self.webhook_url)
        for field in (self.discord_token, self.webhook_key):
            if not isinstance(field, str) or not field or any(c.isspace() for c in field):
                raise ConfigError("Discord and webhook credentials are required")
        for field in (self.admin_token, self.capability_secret):
            if not isinstance(field, str) or len(field) < 32 or any(c.isspace() for c in field):
                raise ConfigError("generated secrets must be at least 32 characters")
        if not re.fullmatch(r"[a-f0-9]{64}", self.agent_token_hash):
            raise ConfigError("invalid helper credential hash")
        for field, low, high in (
            (self.port, 1024, 65535),
            (self.timeout_seconds, 60, 3600),
            (self.queue_limit, 1, 1000),
            (self.rate_per_minute, 1, 100),
            (self.retention_days, 1, 30),
        ):
            if type(field) is not int or not low <= field <= high:
                raise ConfigError("numeric configuration out of range")
        if type(self.ack_message) is not bool:
            raise ConfigError("ack_message must be true or false")
        if type(self.debug) is not bool:
            raise ConfigError("debug must be true or false")
        return self

    @classmethod
    def load(cls, path: Path) -> Settings:
        try:
            return cls(**read_private(path)).validate()
        except TypeError as exc:
            raise ConfigError("missing or unknown config fields") from exc

    def save(self, path: Path) -> None:
        private_json(path, asdict(self.validate()))

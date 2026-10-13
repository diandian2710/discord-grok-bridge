import pytest

from discord_grok_bridge.config import Settings, digest
from discord_grok_bridge.store import Store


@pytest.fixture
def settings():
    return Settings(
        "fake-discord-token",
        "12345",
        "23456",
        "34567",
        "https://api2.cursor.sh/automations/webhook/test",
        "fake-webhook-key",
        digest("a" * 40),
        "b" * 40,
        "c" * 40,
    )


@pytest.fixture
def clock():
    return [100000.0]


@pytest.fixture
def store(tmp_path, clock):
    value = Store(tmp_path / "state/inbox.db", "test-secret", now=lambda: clock[0])
    yield value
    value.close()


def add(store, message="123456", channel="23456", **kwargs):
    # Most store tests exercise the optional "Queued." chunk; pass ack=False to omit it.
    kwargs.setdefault("ack", True)
    return store.add(message, "12345", channel, "34567", "  hello\nworld  ", **kwargs)[0]

import sqlite3

import pytest

from discord_grok_bridge.store import Rejected, Store, split_text

from .conftest import add


def submit(store, job, text="response", no_reply=False):
    return store.submit(job["event_id"], job["generation"], job["reply_capability"], text, no_reply)


def finish(store):
    while chunk := store.prepare_send():
        store.sent(chunk["id"], "98765")


def test_duplicate_survives_restart(store, tmp_path, clock):
    event = add(store)
    other = Store(tmp_path / "state/inbox.db", "test-secret", now=lambda: clock[0])
    try:
        duplicate, fresh = other.add("123456", "12345", "23456", "34567", "changed text")
        assert duplicate == event and not fresh
        assert other.job(event)["text"] == "  hello\nworld  "
        assert other.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    finally:
        other.close()


def test_fifo_blocks_retry_claim_and_delivery(store, clock):
    first = add(store)
    second = add(store, "123457")
    assert store.prepare_wake() == first
    store.wake_result(first, "retry")
    assert store.prepare_wake() is None
    job = store.claim()
    assert job["event_id"] == first
    assert store.claim() is None
    submit(store, job)
    assert store.claim() is None
    finish(store)
    assert store.claim()["event_id"] == second


def test_unrelated_conversation_progresses(store):
    first = add(store)
    second = add(store, "123457", channel="45678")
    assert store.claim()["event_id"] == first
    assert store.claim()["event_id"] == second


def test_atomic_claim_two_database_handles(store, tmp_path, clock):
    add(store)
    other = Store(tmp_path / "state/inbox.db", "test-secret", now=lambda: clock[0])
    try:
        assert store.claim()
        assert other.claim() is None
    finally:
        other.close()


def test_replayed_reply_is_idempotent_and_conflict_rejected(store):
    add(store)
    job = store.claim()
    assert submit(store, job)["duplicate"] is False
    assert submit(store, job)["duplicate"] is True
    with pytest.raises(Rejected, match="already accepted"):
        submit(store, job, "changed")
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
    finish(store)
    assert submit(store, job)["duplicate"] is True


@pytest.mark.parametrize("change", ["event", "generation", "capability"])
def test_wrong_capability_and_unknown_event(store, change):
    add(store)
    job = store.claim()
    if change == "event":
        job["event_id"] = "0" * 32
    elif change == "generation":
        job["generation"] += 1
    else:
        job["reply_capability"] = "wrong-token"
    with pytest.raises(Rejected):
        submit(store, job)


def test_expiry_records_failure_and_fences_old_reply(store, clock):
    first = add(store, timeout=60)
    second = add(store, "123457", timeout=600)
    job = store.claim()
    clock[0] += 60
    store.expire()
    with pytest.raises(Rejected):
        submit(store, job)
    assert store.job(first)["error"] == "expired"
    assert store.claim() is None
    finish(store)
    assert store.claim()["event_id"] == second


def test_200_without_reply_expires(store, clock):
    event = add(store, timeout=60)
    store.prepare_wake()
    store.wake_result(event, "accepted")
    assert store.job(event)["state"] == "awaiting"
    clock[0] += 61
    store.expire()
    assert store.job(event)["error"] == "expired"


def test_fast_claim_reply_is_not_overwritten_by_wake_result(store):
    event = add(store)
    store.prepare_wake()
    job = store.claim()
    submit(store, job)
    store.wake_result(event, "accepted")
    assert store.job(event)["state"] == "reply"


def test_capacity_and_rate_limits_do_not_admit_or_ack(store):
    add(store, limit=1)
    with pytest.raises(Rejected, match="queue is full"):
        add(store, "123457", limit=1)
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    with pytest.raises(Rejected, match="rate limit"):
        add(store, "123458", rate=1)


def test_atomic_rollback_on_outbox_failure(store):
    store.db.execute(
        "CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN "
        "SELECT RAISE(ABORT,'simulated disk failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        add(store)
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert store.watermark() is None


def test_restart_keeps_claimed_work_and_blocks_duplicate_execution(store):
    event = add(store)
    store.claim()
    store.recover_restart()
    assert store.job(event)["state"] == "claimed"
    assert store.claim() is None


def test_interrupted_wake_requires_reconciliation(store):
    event = add(store)
    store.prepare_wake()
    store.recover_restart()
    assert store.job(event)["state"] == "uncertain"
    assert store.prepare_wake() is None
    assert store.claim() is None
    store.recover(event, "retry-wake")
    assert store.prepare_wake() == event


def test_partial_delivery_preserves_sent_chunks(store, clock):
    add(store)
    job = store.claim()
    submit(store, job, "x" * 4000)
    ack = store.prepare_send()
    store.sent(ack["id"], "111")
    first = store.prepare_send()
    store.sent(first["id"], "222")
    second = store.prepare_send()
    store.send_error(second["id"])
    clock[0] += 10
    retried = store.prepare_send()
    assert retried["id"] == second["id"]
    assert retried["nonce"] == second["nonce"]
    store.sent(retried["id"], "333")
    finish(store)
    assert store.job(job["event_id"])["state"] == "delivered"


def test_crash_after_send_recovers_with_same_nonce_within_window(store, clock):
    add(store)
    chunk = store.prepare_send()
    clock[0] += 10
    store.recover_restart()
    retry = store.prepare_send()
    assert retry["nonce"] == chunk["nonce"]


def test_old_ambiguous_send_is_not_automatically_repeated(store, clock):
    event = add(store)
    store.prepare_send()
    clock[0] += 121
    store.recover_restart()
    assert store.prepare_send() is None
    assert store.status()["uncertain_chunks"] == 1
    store.recover(event, "retry-send")
    assert store.prepare_send()


def test_unknown_and_invalid_recovery_rejected(store):
    with pytest.raises(Rejected):
        store.recover("unknown", "skip")
    event = add(store)
    with pytest.raises(Rejected):
        store.recover(event, "retry-wake")
    store.recover(event, "skip")
    assert store.job(event)["state"] == "failed"


def test_no_reply_completes_without_blocking_queue(store):
    first = add(store)
    second = add(store, "123457")
    job = store.claim()
    submit(store, job, "", True)
    assert store.job(first)["state"] == "delivered"
    assert store.claim()["event_id"] == second


def test_history_is_scoped_and_ordered(store):
    add(store)
    submit(store, store.claim(), "answer one")
    finish(store)
    add(store, "123457", channel="45678")
    submit(store, store.claim(), "other channel")
    finish(store)
    add(store, "123458")
    assert store.claim()["history"] == [{"user": "  hello\nworld  ", "assistant": "answer one"}]


def test_retention_erases_bodies_but_keeps_duplicate_tombstone(store, clock):
    event = add(store)
    job = store.claim()
    submit(store, job)
    finish(store)
    clock[0] += 8 * 86400
    store.prune(7)
    assert store.job(event)["text"] == ""
    assert store.job(event)["outcome"] == ""
    assert store.add("123456", "12345", "23456", "34567", "same old event")[1] is False


def test_unicode_split_preserves_exact_text():
    text = "  code\n" + "🦉" * 3000 + "\n  "
    chunks = split_text(text)
    assert "".join(chunks) == text
    assert all(len(x.encode("utf-16-le")) // 2 <= 1900 for x in chunks)


@pytest.mark.parametrize("text,no_reply", [(" ", False), ("text", True), ("x" * 20001, False)])
def test_output_limits_and_disposition(store, text, no_reply):
    add(store)
    with pytest.raises(Rejected):
        submit(store, store.claim(), text, no_reply)


def test_new_owner_does_not_receive_previous_owners_history(store):
    add(store)
    submit(store, store.claim(), "previous private answer")
    finish(store)
    store.add("123457", "12345", "23456", "45678", "new owner request")
    job = store.claim()
    assert job["history"] == []

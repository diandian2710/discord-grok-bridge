from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .config import digest, private_dir

MAX_INPUT = 4000
MAX_OUTPUT = 20000
NONCE_WINDOW = 120
TERMINAL = ("delivered", "failed")


class Rejected(ValueError):
    def __init__(self, reason: str, status: int = 409):
        super().__init__(reason)
        self.status = status


class Store:
    """One service owns the database; every API mutation is a synchronous transaction."""

    def __init__(self, path: Path, secret: str, *, now=time.time):
        private_dir(path.parent)
        if path.is_symlink():
            raise ValueError("database must not be a symlink")
        # Service umask also protects SQLite sidecars.
        if not path.exists():
            path.touch(mode=0o600)
        path.chmod(0o600)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.now = now
        self.secret = secret
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError("unsupported database schema")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT UNIQUE NOT NULL,
              message TEXT UNIQUE NOT NULL, guild TEXT NOT NULL, channel TEXT NOT NULL,
              author TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
              expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
              generation INTEGER NOT NULL DEFAULT 0, cap_hash TEXT NOT NULL DEFAULT '',
              response_hash TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '',
              completed REAL NOT NULL DEFAULT 0, wake_attempts INTEGER NOT NULL DEFAULT 0,
              next_wake REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS outbox (
              id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL REFERENCES jobs(event),
              ordinal INTEGER NOT NULL, text TEXT NOT NULL, nonce TEXT UNIQUE NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', first_attempt REAL,
              attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
              discord_id TEXT, UNIQUE(event, ordinal)
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_active ON jobs(state,seq);
            CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(state,id);
            PRAGMA user_version=1;
        """)

    def close(self):
        self.db.close()

    def transaction(self):
        return Transaction(self.db)

    def job(self, event: str):
        return self.db.execute("SELECT * FROM jobs WHERE event=?", (event,)).fetchone()

    def watermark(self):
        row = self.db.execute("SELECT value FROM metadata WHERE key='watermark'").fetchone()
        return row[0] if row else None

    def advance(self, message: str):
        previous = self.watermark()
        if previous is None or int(message) > int(previous):
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('watermark',?)", (message,))

    def add(
        self,
        message: str,
        guild: str,
        channel: str,
        author: str,
        text: str,
        *,
        timeout=900,
        limit=100,
        rate=10,
        ack=False,
    ):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT:
            raise Rejected("text must contain 1..4000 characters", 422)
        now = self.now()
        with self.transaction():
            existing = self.db.execute(
                "SELECT event FROM jobs WHERE message=?", (message,)
            ).fetchone()
            if existing:
                return existing[0], False
            active = self.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state NOT IN ('delivered','failed')"
            ).fetchone()[0]
            if active >= limit:
                raise Rejected("queue is full", 429)
            recent = self.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE author=? AND created>?", (author, now - 60)
            ).fetchone()[0]
            if recent >= rate:
                raise Rejected("message rate limit reached", 429)
            event = uuid.uuid4().hex
            self.db.execute(
                """INSERT INTO jobs
                (event,message,guild,channel,author,text,created,expires)
                VALUES (?,?,?,?,?,?,?,?)""",
                (event, message, guild, channel, author, text, now, now + timeout),
            )
            if ack:
                # Optional interim notice; durability does not depend on it.
                self._chunk(event, 0, "Queued.")
            self.advance(message)
            return event, True

    def _chunk(self, event: str, ordinal: int, text: str):
        nonce = hashlib.sha256(f"{event}:{ordinal}".encode()).hexdigest()[:24]
        self.db.execute(
            "INSERT INTO outbox(event,ordinal,text,nonce) VALUES (?,?,?,?)",
            (event, ordinal, text, nonce),
        )

    def heads(self):
        return self.db.execute("""SELECT j.* FROM jobs j
            WHERE j.state NOT IN ('delivered','failed') AND NOT EXISTS (
              SELECT 1 FROM jobs earlier WHERE earlier.channel=j.channel
                AND earlier.seq<j.seq AND earlier.state NOT IN ('delivered','failed'))
            ORDER BY j.seq""").fetchall()

    def prepare_wake(self):
        with self.transaction():
            for job in self.heads():
                if job["state"] == "queued" and job["next_wake"] <= self.now():
                    self.db.execute(
                        "UPDATE jobs SET state='waking',wake_attempts=wake_attempts+1 "
                        "WHERE event=?",
                        (job["event"],),
                    )
                    return job["event"]
        return None

    def wake_result(self, event: str, result: str):
        with self.transaction():
            job = self.job(event)
            if job["state"] != "waking":
                return  # A fast helper claim/reply owns the later state.
            if result == "accepted":
                self.db.execute("UPDATE jobs SET state='awaiting' WHERE event=?", (event,))
            elif result == "retry" and job["wake_attempts"] < 3:
                self.db.execute(
                    "UPDATE jobs SET state='queued',next_wake=? WHERE event=?",
                    (self.now() + 2 ** job["wake_attempts"], event),
                )
            elif result == "rejected" or result == "retry":
                self._failure(
                    event, "Grok wake was rejected. Check bridge status.", "wake_rejected"
                )
            else:
                self.db.execute(
                    "UPDATE jobs SET state='uncertain',error='wake_uncertain' WHERE event=?",
                    (event,),
                )

    def claim(self, owner: str | None = None):
        # Only the configured owner gets role "owner"; anyone else (or unknown owner) is chat_only.
        self.expire()
        with self.transaction():
            for job in self.heads():
                if job["state"] not in ("queued", "waking", "awaiting"):
                    continue
                generation = job["generation"] + 1
                token = hmac.new(
                    self.secret.encode(), f"{job['event']}:{generation}".encode(), "sha256"
                ).hexdigest()
                self.db.execute(
                    "UPDATE jobs SET state='claimed',generation=?,cap_hash=? WHERE event=?",
                    (generation, digest(token), job["event"]),
                )
                history = self.db.execute(
                    """SELECT text,outcome FROM jobs WHERE channel=? AND guild=? AND author=?
                    AND seq<? AND state='delivered' AND outcome!='' ORDER BY seq DESC LIMIT 6""",
                    (job["channel"], job["guild"], job["author"], job["seq"]),
                ).fetchall()
                return {
                    "schema": "discord-grok.job.v1",
                    "event_id": job["event"],
                    "generation": generation,
                    "reply_capability": token,
                    "expires_at": job["expires"],
                    # Lets the routine tell the owner apart from other authorized users.
                    "author_id": job["author"],
                    "role": "owner" if owner and job["author"] == owner else "chat_only",
                    "conversation_id": digest(f"{job['guild']}:{job['channel']}:{job['author']}")[
                        :32
                    ],
                    "text": job["text"],
                    "history": [
                        {"user": r["text"], "assistant": r["outcome"]} for r in reversed(history)
                    ],
                }
        return None

    def submit(self, event: str, generation: int, token: str, text: str, no_reply: bool):
        if (
            type(generation) is not int
            or not isinstance(token, str)
            or not re.fullmatch(r"[a-f0-9]{64}", token)
        ):
            raise Rejected("invalid capability", 403)
        if type(no_reply) is not bool or not isinstance(text, str) or len(text) > MAX_OUTPUT:
            raise Rejected("invalid reply", 422)
        if (no_reply and text != "") or (not no_reply and not text.strip()):
            raise Rejected("provide text or explicit no_reply", 422)
        try:
            text.encode("utf-8")
        except UnicodeError:
            raise Rejected("reply must be valid UTF-8", 422) from None
        response_hash = digest(json.dumps([text, no_reply], ensure_ascii=False))
        with self.transaction():
            job = self.job(event)
            if not job:
                raise Rejected("unknown event", 404)
            if (
                generation != job["generation"]
                or not job["cap_hash"]
                or not hmac.compare_digest(digest(token), job["cap_hash"])
            ):
                raise Rejected("invalid capability", 403)
            if job["response_hash"]:
                if hmac.compare_digest(response_hash, job["response_hash"]):
                    return {"ok": True, "event_id": event, "duplicate": True}
                raise Rejected("final reply already accepted")
            if job["state"] != "claimed" or self.now() >= job["expires"]:
                raise Rejected("request is inactive or expired")
            self.db.execute(
                "UPDATE jobs SET response_hash=?,outcome=?,state=? WHERE event=?",
                (response_hash, text, "delivered" if no_reply else "reply", event),
            )
            if no_reply:
                self.db.execute("UPDATE jobs SET completed=? WHERE event=?", (self.now(), event))
            else:
                for ordinal, chunk in enumerate(split_text(text), 1):
                    self._chunk(event, ordinal, chunk)
            return {"ok": True, "event_id": event, "duplicate": False}

    def _failure(self, event: str, text: str, error: str):
        self.db.execute(
            "UPDATE jobs SET state='reply',cap_hash='',error=? WHERE event=?", (error, event)
        )
        self._chunk(event, 1, text)

    def expire(self):
        with self.transaction():
            rows = self.db.execute(
                "SELECT event FROM jobs WHERE expires<=? AND state IN "
                "('queued','waking','awaiting','claimed','uncertain')",
                (self.now(),),
            ).fetchall()
            for row in rows:
                self._failure(
                    row[0], "Request expired without a final reply. Check bridge status.", "expired"
                )

    def prepare_send(self):
        with self.transaction():
            row = self.db.execute(
                """SELECT o.*,j.channel,j.message,j.guild,j.author
                FROM outbox o JOIN jobs j ON j.event=o.event WHERE o.state='pending'
                AND o.next_at<=? AND NOT EXISTS (
                  SELECT 1 FROM outbox p WHERE p.event=o.event AND p.ordinal<o.ordinal
                  AND p.state!='sent') ORDER BY o.id LIMIT 1""",
                (self.now(),),
            ).fetchone()
            if not row:
                return None
            if (
                row["first_attempt"] is not None
                and self.now() - row["first_attempt"] >= NONCE_WINDOW
            ):
                self.db.execute("UPDATE outbox SET state='uncertain' WHERE id=?", (row["id"],))
                return None
            self.db.execute(
                "UPDATE outbox SET state='sending',attempts=attempts+1,"
                "first_attempt=COALESCE(first_attempt,?) WHERE id=?",
                (self.now(), row["id"]),
            )
            return dict(row)

    def sent(self, chunk_id: int, discord_id: str):
        with self.transaction():
            chunk = self.db.execute("SELECT * FROM outbox WHERE id=?", (chunk_id,)).fetchone()
            self.db.execute(
                "UPDATE outbox SET state='sent',discord_id=? WHERE id=?", (discord_id, chunk_id)
            )
            remaining = self.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE event=? AND state!='sent'", (chunk["event"],)
            ).fetchone()[0]
            if remaining == 0:
                self.db.execute(
                    "UPDATE jobs SET state='delivered',completed=? WHERE event=? AND state='reply'",
                    (self.now(), chunk["event"]),
                )

    def send_error(self, chunk_id: int, permanent=False):
        with self.transaction():
            chunk = self.db.execute("SELECT * FROM outbox WHERE id=?", (chunk_id,)).fetchone()
            if permanent:
                self.db.execute(
                    "UPDATE outbox SET state='failed' WHERE event=? AND state!='sent'",
                    (chunk["event"],),
                )
                self.db.execute(
                    "UPDATE jobs SET state='failed',cap_hash='',completed=?,"
                    "error='discord_rejected' WHERE event=?",
                    (self.now(), chunk["event"]),
                )
            elif chunk["attempts"] >= 3 or self.now() - chunk["first_attempt"] >= NONCE_WINDOW:
                self.db.execute("UPDATE outbox SET state='uncertain' WHERE id=?", (chunk_id,))
            else:
                self.db.execute(
                    "UPDATE outbox SET state='pending',next_at=? WHERE id=?",
                    (self.now() + 2 ** chunk["attempts"], chunk_id),
                )

    def recover_restart(self):
        with self.transaction():
            self.db.execute(
                "UPDATE jobs SET state='uncertain',error='wake_interrupted' WHERE state='waking'"
            )
            self.db.execute(
                "UPDATE outbox SET state=CASE WHEN first_attempt>? THEN 'pending' "
                "ELSE 'uncertain' END WHERE state='sending'",
                (self.now() - NONCE_WINDOW,),
            )

    def recover(self, event: str, action: str):
        with self.transaction():
            job = self.job(event)
            if not job:
                raise Rejected("unknown event", 404)
            if action == "retry-wake" and job["state"] == "uncertain":
                self.db.execute(
                    "UPDATE jobs SET state='queued',next_wake=0,error='' WHERE event=?", (event,)
                )
            elif action == "retry-send":
                changed = self.db.execute(
                    "UPDATE outbox SET state='pending',first_attempt=NULL,"
                    "attempts=0,next_at=0 WHERE event=? AND state='uncertain'",
                    (event,),
                ).rowcount
                if not changed:
                    raise Rejected("no uncertain delivery")
            elif action == "skip":
                if job["state"] in TERMINAL:
                    raise Rejected("request is already finished")
                self.db.execute(
                    "UPDATE jobs SET state='failed',cap_hash='',error='operator_skip',"
                    "completed=? WHERE event=?",
                    (self.now(), event),
                )
                self.db.execute(
                    "UPDATE outbox SET state='failed' WHERE event=? AND state!='sent'", (event,)
                )
            else:
                raise Rejected("action does not match current state")

    def status(self):
        return {
            "jobs": [
                dict(r)
                for r in self.db.execute(
                    "SELECT event,state,error,created,expires FROM jobs ORDER BY seq DESC LIMIT 100"
                )
            ],
            "uncertain_chunks": self.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE state='uncertain'"
            ).fetchone()[0],
        }

    def revoke_unallowed(self, guild: str, channel: str, authors):
        authors = [authors] if isinstance(authors, str) else sorted(authors)
        if not authors:
            raise ValueError("at least one authorized author is required")
        marks = ",".join("?" * len(authors))
        with self.transaction():
            rows = self.db.execute(
                "SELECT event FROM jobs WHERE state NOT IN ('delivered','failed') "
                f"AND (guild!=? OR channel!=? OR author NOT IN ({marks}))",
                (guild, channel, *authors),
            ).fetchall()
            for row in rows:
                self.db.execute(
                    "UPDATE jobs SET state='failed',cap_hash='',completed=?,"
                    "error='policy_changed' WHERE event=?",
                    (self.now(), row[0]),
                )
                self.db.execute(
                    "UPDATE outbox SET state='failed' WHERE event=? AND state!='sent'", (row[0],)
                )

    def prune(self, days: int):
        # Keep minimal tombstones indefinitely, but erase old conversation bodies and capabilities.
        with self.transaction():
            cutoff = self.now() - days * 86400
            self.db.execute(
                "UPDATE jobs SET text='',outcome='',cap_hash='' WHERE completed>0 "
                "AND completed<? AND state IN ('delivered','failed')",
                (cutoff,),
            )
            self.db.execute(
                "UPDATE outbox SET text='' WHERE event IN "
                "(SELECT event FROM jobs WHERE completed>0 AND completed<? "
                "AND state IN ('delivered','failed'))",
                (cutoff,),
            )


class Transaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind, value, traceback):
        self.db.execute("ROLLBACK" if kind else "COMMIT")


def split_text(text: str):
    # UTF-16 units bound Discord payload size while preserving every source character.
    chunks, current, units = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > 1900:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += width
    if current:
        chunks.append("".join(current))
    return chunks

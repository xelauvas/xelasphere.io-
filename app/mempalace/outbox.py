"""Flask-side outbox for mempalace turn delivery.

M0.15 Stage 1 RED scaffolding: `Outbox.__init__` opens the sqlite connection,
configures PRAGMAs per deviations D1/D2/D9, and runs SCHEMA. Every method
tests exercise (`insert`, `depth`, `claim_batch`, `mark_sent`, `mark_failed`,
`abandon_user`, `drain_then_abandon`, `purge_old_sent`) is DELIBERATELY
UNDEFINED. Stage 3 GREEN lands real behavior.

See `docs/superpowers/plans/2026-04-20-mempalace-integration.md` lines
3575-3859 for the plan sketch. Nine pre-declared deviations (D1-D9) will be
documented in deltas at Stage 4a.

**D9 — PRAGMA synchronous=NORMAL durability rationale (inline per dispatch).**
Weaker than FULL. Process-crash-safe (WAL recovery replays committed WAL on
reopen). OS-crash-lossy (up to the last few transactions since the previous
checkpoint can be lost if the kernel hasn't fsync'd them). Acceptable because
the outbox has retry semantics — lost rows on OS crash are re-enqueued by
the Flask request that populates the outbox; durability guarantees at this
layer would be belt-and-suspenders for a use case that already tolerates
transient loss.

**Threading note (for future callers).** `check_same_thread=False` permits
the flusher thread and request-handling thread to share this `Outbox`
instance's connection. External coordination (claim_batch's BEGIN IMMEDIATE
lock, `timeout=5.0` busy-wait per D1) provides the actual mutual exclusion.
Concurrent raw `self._conn.execute(...)` calls from two threads without
external coordination can still corrupt sqlite3 connection state — avoid.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id TEXT NOT NULL UNIQUE,
  pair_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  text BLOB NOT NULL,
  ts REAL NOT NULL,
  request_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  claim_token TEXT,
  claimed_at REAL,
  enqueued_at REAL NOT NULL,
  sent_at REAL
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox(status, enqueued_at);
CREATE INDEX IF NOT EXISTS outbox_sent_purge_idx ON outbox(status, sent_at);
CREATE INDEX IF NOT EXISTS outbox_user_idx ON outbox(user_id);
"""


class Outbox:
    """SQLite-backed outbox for pending turn deliveries.

    Stage 1 RED: `__init__` sets up schema + WAL + PRAGMAs; all data-access
    methods are undefined.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # D1: explicit busy_timeout=5s for BEGIN IMMEDIATE multi-worker
        # contention. check_same_thread=False enables shared use across
        # flusher/request threads; coordination is external (BEGIN IMMEDIATE
        # + claim_token in claim_batch, caller-stops-first in drain_then_abandon).
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, timeout=5.0
        )
        self._conn.row_factory = sqlite3.Row
        # D9 (rationale in module docstring): synchronous=NORMAL tradeoff.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # D2: enable WAL and VERIFY. Silent fallback to DELETE mode (NFS,
        # missing shm support, read-only mounts) would bypass the entire
        # concurrency story; fail-loud instead.
        self._conn.execute("PRAGMA journal_mode=WAL")
        actual = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        if actual != "wal":
            raise RuntimeError(
                f"WAL mode not available at {self._path}; journal_mode={actual}"
            )
        self._conn.executescript(SCHEMA)
        self._conn.commit()

"""Flask-side outbox for mempalace turn delivery.

Implements the plan sketch at `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 3575-3859 (Tasks M0.15.1 through M0.15.6) with D1-D9 deviations per
the M0.15 Stage 4a deltas entry.

**D9 — PRAGMA synchronous=NORMAL durability rationale.** Weaker than FULL.
Process-crash-safe (WAL recovery replays committed WAL on reopen).
OS-crash-lossy (up to the last few transactions since the previous
checkpoint can be lost if the kernel hasn't fsync'd them). Acceptable
because the outbox has retry semantics — lost rows on OS crash are
re-enqueued by the Flask request that populates the outbox; durability
guarantees at this layer would be belt-and-suspenders for a use case
that already tolerates transient loss.

**Threading note.** `check_same_thread=False` permits the flusher thread
and request-handling thread to share this Outbox instance's connection.
External coordination (`claim_batch`'s BEGIN IMMEDIATE lock, `timeout=5.0`
busy-wait per D1, `isolation_level=None` autocommit mode for predictable
transaction control) provides the actual mutual exclusion.

**Autocommit / transaction management.** `isolation_level=None` puts the
sqlite3 module in autocommit mode — no implicit transactions around
DML. Methods that need a multi-statement atomic operation
(`claim_batch` with BEGIN IMMEDIATE) manage their own transaction
boundaries explicitly via `_immediate_txn`. Single-statement methods
(`insert`, `mark_sent`, `mark_failed`, `abandon_user`, `purge_old_sent`)
rely on SQLite's statement-level atomicity without explicit transactions.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

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
    """SQLite-backed outbox for pending turn deliveries."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # D1: busy_timeout=5s for BEGIN IMMEDIATE multi-worker contention.
        # isolation_level=None: autocommit mode. See module docstring for
        # transaction-management rationale.
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            timeout=5.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        # D9 (rationale in module docstring).
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # D2: enable WAL and VERIFY. Silent fallback (NFS, missing shm,
        # read-only) would bypass the concurrency story; fail-loud instead.
        self._conn.execute("PRAGMA journal_mode=WAL")
        actual = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        if actual != "wal":
            raise RuntimeError(
                f"WAL mode not available at {self._path}; journal_mode={actual}"
            )
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------
    # Transaction helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _immediate_txn(self) -> Iterator[None]:
        """Explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK for multi-statement
        atomic operations. Required because isolation_level=None means the
        module does not start implicit transactions around DML.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # M0.15.2 — insert
    # ------------------------------------------------------------------

    def insert(
        self,
        *,
        turn_id: str,
        pair_id: str,
        user_id: str,
        session_id: str,
        role: str,
        text: str,
        ts: float,
        request_id: str | None,
    ) -> None:
        """Insert a pending row. Raises `sqlite3.IntegrityError` on duplicate turn_id."""
        self._conn.execute(
            "INSERT INTO outbox "
            "(turn_id, pair_id, user_id, session_id, role, text, ts, request_id, enqueued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (turn_id, pair_id, user_id, session_id, role, text, ts, request_id, time.time()),
        )

    # ------------------------------------------------------------------
    # M0.15.3 — depth + claim_batch
    # ------------------------------------------------------------------

    def depth(self, status: str) -> int:
        """Count rows in the given status."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE status=?", (status,)
        ).fetchone()
        return row[0]

    def claim_batch(self, limit: int) -> list[dict]:
        """Atomically claim up to `limit` pending rows.

        Uses BEGIN IMMEDIATE + conditional UPDATE with a unique claim_token.
        Even though v1 runs a single flusher, claim tokens protect against
        accidental double-flush and make the outbox inspectable to ops queries
        without mutation races.
        """
        token = str(uuid.uuid4())
        with self._immediate_txn():
            rows = self._conn.execute(
                "SELECT id FROM outbox WHERE status='pending' "
                "ORDER BY enqueued_at LIMIT ?",
                (limit,),
            ).fetchall()
            ids = [r[0] for r in rows]
            if not ids:
                return []
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE outbox SET status='claimed', claim_token=?, claimed_at=? "
                f"WHERE id IN ({placeholders}) AND status='pending'",
                (token, time.time(), *ids),
            )
        # Read back outside the transaction.
        rows = self._conn.execute(
            "SELECT * FROM outbox WHERE claim_token=?", (token,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # M0.15.4 — mark_sent + mark_failed
    # ------------------------------------------------------------------

    def mark_sent(self, ids: list[int]) -> None:
        """Mark the given rows as sent and clear their claim_token."""
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE outbox SET status='sent', sent_at=?, claim_token=NULL "
            f"WHERE id IN ({placeholders})",
            (time.time(), *ids),
        )

    def mark_failed(self, row_id: int, err: str) -> None:
        """Record a failure. Increments attempts; transitions to 'abandoned'
        when attempts reaches 5 (poison-pill protection).

        D3: single atomic UPDATE with CASE for status. Clears claim_token in
        both pending and abandoned branches — no 'abandoned with stale
        claim_token' intermediate state.
        """
        self._conn.execute(
            """UPDATE outbox SET
                status = CASE WHEN attempts + 1 >= 5 THEN 'abandoned' ELSE 'pending' END,
                attempts = attempts + 1,
                last_error = ?,
                claim_token = NULL
            WHERE id = ?""",
            (err[:500], row_id),
        )

    # ------------------------------------------------------------------
    # M0.15.5 — abandon_user + drain_then_abandon
    # ------------------------------------------------------------------

    def abandon_user(self, user_id: str) -> int:
        """Mark all non-sent rows for the given user as abandoned.

        Used by delete_palace flow — on user data deletion, the outbox must
        discard any pending deliveries for that user rather than flush them
        after the data is already gone.
        """
        cur = self._conn.execute(
            "UPDATE outbox SET status='abandoned' "
            "WHERE user_id=? AND status IN ('pending', 'claimed')",
            (user_id,),
        )
        return cur.rowcount

    def drain_then_abandon(
        self,
        flush_callable: Callable[[dict], None],
        *,
        timeout_s: float = 600.0,
        poll_s: float = 2.0,
    ) -> dict:
        """Flush all pending rows; after timeout, mark remaining as abandoned.

        Used by mempalace-mode off (spec §3: "outbox pre-flip rows drain
        best-effort for 10 minutes, then are marked abandoned").

        **D5 caller-stops-first contract.** The caller MUST stop the flusher
        before invoking (flusher.stop() + flusher.join()). Concurrent flusher
        + drain produces two-flushers-on-one-outbox race (hazard O). Test
        uses stub flush_callable (no flusher); production call site at
        mempalace-mode-off transition must stop+join flusher first. Not
        enforced by this method's signature — architectural contract
        documented here.
        """
        started = time.monotonic()
        drained = 0
        while time.monotonic() - started < timeout_s:
            if self.depth("pending") == 0:
                break
            rows = self.claim_batch(limit=20)
            for row in rows:
                try:
                    flush_callable(row)
                    self.mark_sent([row["id"]])
                    drained += 1
                except Exception as e:
                    self.mark_failed(row["id"], str(e)[:500])
            time.sleep(poll_s)
        cur = self._conn.execute(
            "UPDATE outbox SET status='abandoned' "
            "WHERE status IN ('pending', 'claimed')"
        )
        return {"drained": drained, "abandoned": cur.rowcount}

    # ------------------------------------------------------------------
    # M0.15.6 — purge_old_sent
    # ------------------------------------------------------------------

    def purge_old_sent(self, days: int = 7) -> int:
        """Delete rows that have been 'sent' for more than `days` days.
        Called periodically by the flusher (every N loops). Keeps the
        outbox small without a separate timer unit.
        """
        cutoff = time.time() - days * 86400
        cur = self._conn.execute(
            "DELETE FROM outbox WHERE status='sent' AND sent_at < ?", (cutoff,)
        )
        return cur.rowcount

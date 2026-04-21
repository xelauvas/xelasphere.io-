"""Durable KG-extraction queue (plan M0.7, spec §5.5).

Single sqlite database at the sidecar level (``settings.data_dir /
"kg_pending.sqlite"``) holding pending knowledge-graph extraction
jobs. Every successful ingest fires one ``enqueue`` call; the batched
worker (``kg_worker.run_kg_worker``) pulls rows, groups by user, runs
the KG LLM extractor, and writes triples to each user's palace KG.

**Per-user vs per-sidecar split.** The queue lives at the sidecar
level — one table for all users — because spending is accounted per
sidecar / per billing account, not per palace. Triples land in each
user's per-palace ``kg.sqlite`` via
``entry.handle.kg.add_triple(...)``. Keeping the spend table and the
pending queue together (same DB) makes the "cap hit, pause" check a
single WHERE query instead of a cross-DB coordinator.

**Idempotency contract.** The UNIQUE constraint
``(user_id, drawer_ids_hash)`` dedupes identical enqueue calls. The
hash is ``sha256(user_id || "|" || ",".join(drawer_ids))`` — order-
sensitive on purpose: two different ingests that happen to produce
the same drawer_ids in a different order are different batches.
``enqueue`` catches ``sqlite3.IntegrityError`` / aiosqlite's
equivalent so the caller (ingest route fire-and-forget) never sees a
duplicate signal; the dedup is invisible upstream.

**Attempt cap.** Rows get ``attempts`` incremented on each failure;
``claim`` filters on ``attempts < 5`` so a row that fails 5 times
gets excluded from future claims. The final failing tick flips status
to ``'failed'`` explicitly for clarity (and for the
``mempalace_kg_queue_depth{status=failed}`` metric landing in M0.11).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    drawer_ids TEXT NOT NULL,
    drawer_ids_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    enqueued_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, drawer_ids_hash)
);

CREATE INDEX IF NOT EXISTS idx_kg_pending_claim
    ON kg_pending (status, attempts, enqueued_at);

CREATE TABLE IF NOT EXISTS kg_spend (
    date TEXT PRIMARY KEY,
    usd REAL NOT NULL DEFAULT 0
);
"""

_ATTEMPT_CAP = 5


def _hash_drawer_ids(user_id: str, drawer_ids: list[str]) -> str:
    """Deterministic dedup hash for ``(user_id, drawer_ids)``.

    Order-sensitive: ``["d1", "d2"]`` and ``["d2", "d1"]`` hash
    differently — two ingests that fire in reverse order are two
    different batches worth extracting separately.
    """
    payload = f"{user_id}|{','.join(drawer_ids)}"
    return hashlib.sha256(payload.encode()).hexdigest()


class KGQueue:
    """Async wrapper around ``kg_pending.sqlite``.

    Every public method opens a fresh ``aiosqlite.connect`` context,
    does one write/read, commits, and closes. sqlite's internal
    lock + WAL is the concurrency primitive — we don't hold a
    long-lived connection. This avoids the "connection held across
    an await in another task" failure mode that bit earlier
    iterations of the design.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)

    async def ensure(self) -> None:
        """Create schema if missing. Idempotent; safe to call every
        sidecar startup."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def enqueue(self, user_id: str, drawer_ids: list[str]) -> None:
        """Insert a pending job. Silent no-op on duplicate (UNIQUE
        constraint violation) — the caller never sees the conflict.

        Fire-and-forget semantics: the ingest route awaits this, but
        any exception is swallowed here (not re-raised). Enqueue
        must NEVER fail ingest.
        """
        h = _hash_drawer_ids(user_id, drawer_ids)
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO kg_pending "
                    "(user_id, drawer_ids, drawer_ids_hash, "
                    " enqueued_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, json.dumps(drawer_ids), h, now, now),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                # Duplicate — silent no-op. The existing row stands.
                pass

    async def claim(self, limit: int = 4) -> list[dict]:
        """Mark up to ``limit`` pending rows as 'running' and return
        them. Atomic against concurrent claimers via a single
        transaction + WHERE id IN (...) update.

        Returns dicts with keys ``id``, ``user_id``, ``drawer_ids``
        (still JSON-encoded string — caller decodes with
        ``json.loads`` or the ``_batch_by_user`` helper does it).
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, user_id, drawer_ids FROM kg_pending "
                "WHERE status = 'pending' AND attempts < ? "
                "ORDER BY enqueued_at LIMIT ?",
                (_ATTEMPT_CAP, limit),
            )
            rows = await cursor.fetchall()
            if not rows:
                return []

            row_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(row_ids))
            await db.execute(
                f"UPDATE kg_pending SET status='running', updated_at=? "
                f"WHERE id IN ({placeholders})",
                (time.time(), *row_ids),
            )
            await db.commit()
            return [dict(r) for r in rows]

    async def mark_done(self, row_id: int) -> None:
        """Flip a row to ``status='done'``. Terminal state."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE kg_pending SET status='done', updated_at=? "
                "WHERE id = ?",
                (time.time(), row_id),
            )
            await db.commit()

    async def mark_failed(self, row_id: int, err: str) -> None:
        """Record a failed extraction attempt.

        Increments ``attempts`` and stores ``last_error`` (truncated
        to 500 chars). If the new ``attempts`` value reaches the cap,
        flips status to 'failed' (terminal). Otherwise status goes
        back to 'pending' so a future tick re-claims it.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT attempts FROM kg_pending WHERE id = ?", (row_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return  # row disappeared — nothing to update
            new_attempts = row["attempts"] + 1
            new_status = "failed" if new_attempts >= _ATTEMPT_CAP else "pending"
            await db.execute(
                "UPDATE kg_pending SET status=?, attempts=?, "
                "last_error=?, updated_at=? WHERE id = ?",
                (new_status, new_attempts, err[:500], time.time(), row_id),
            )
            await db.commit()

    async def today_spend(self) -> float:
        """Return today's cumulative USD spend. 0.0 if no row exists
        for today (the worker's very first call of the day)."""
        today = time.strftime("%Y-%m-%d")
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT usd FROM kg_spend WHERE date = ?", (today,)
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0

    async def add_spend(self, usd: float) -> None:
        """Add ``usd`` to today's running total. Upsert — creates the
        row for today if missing."""
        today = time.strftime("%Y-%m-%d")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO kg_spend (date, usd) VALUES (?, ?) "
                "ON CONFLICT(date) DO UPDATE SET usd = usd + excluded.usd",
                (today, usd),
            )
            await db.commit()


__all__ = ["KGQueue"]

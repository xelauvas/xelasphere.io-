"""Append-only audit log for destructive operations (plan M0.8, spec §4.4).

Single sqlite database at the sidecar level (``settings.data_dir /
"audit.sqlite"``) holding a permanent record of every palace deletion
— user_id, requested_by, reason, before_bytes, timestamp. Append-only
by contract; no UPDATE or DELETE paths. The file is INCLUDED in
backups (spec §7.8 backup-excludes list omits ``audit.sqlite``
deliberately — it's first-class sidecar state, not a derivable
artifact).

**Why sidecar-level, not per-palace.** The audit row must survive
the palace it records the deletion of. A per-palace audit row would
vanish with ``shutil.rmtree(palace_dir)`` — the opposite of what
audit is for. One sidecar-wide DB, one row per deletion, never
rmtree'd.

**Why write the audit row BEFORE the rmtree.** Spec §4.4 shape:
``mark pending → purge outbox → sidecar DELETE → close handles →
rm -rf → audit row`` is what you'd assume, but reversing the last
two is load-bearing. If the rmtree crashes mid-run (OOM, kill -9,
disk full, power cycle), the audit row needs to exist so the
nightly reaper can see "deletion was authorized and started; finish
the rmtree of ``.pending_delete.<ts>``." Without the pre-rmtree
audit row, a crashed rmtree leaves an orphan dir with no paper
trail — operationally indistinguishable from "user A never deleted
and their palace moved for no reason."
"""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS deletion_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_bytes INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deletion_audit_user
    ON deletion_audit (user_id, ts);
"""


class AuditLog:
    """Async wrapper around ``audit.sqlite``.

    Public surface (v1 — M0.8):

    - ``ensure()`` — create schema if missing. Idempotent; safe to
      call every sidecar startup.
    - ``record_delete(user_id, requested_by, reason, before_bytes)`` —
      append one row recording a deletion. **Called BEFORE the
      rmtree side-effect runs**; spec §4.4 order-of-operations.

    Future milestones may add ``record_rebuild``, ``record_eviction``,
    or similar — all append-only, all sidecar-level. ``UPDATE`` and
    ``DELETE`` on ``deletion_audit`` are explicitly NOT in the
    contract; the reaper's job is to finish unfinished rmtrees, not
    to mutate the audit trail.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)

    async def ensure(self) -> None:
        """Create schema if missing. Idempotent."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    # record_delete intentionally NOT YET IMPLEMENTED — lands in the
    # M0.8 GREEN commit alongside the DELETE route body. The RED
    # commit's test_audit_log_records_deletion fails with
    # AttributeError, which is the discipline-correct RED failure
    # shape ("method missing") rather than an import-error or a
    # wrong-answer pass.


__all__ = ["AuditLog"]

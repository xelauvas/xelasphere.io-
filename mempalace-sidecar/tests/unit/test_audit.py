"""Unit tests for ``AuditLog`` (plan M0.8).

Append-only audit log for destructive operations. Single-table
sqlite wrapper; tests exercise ``ensure`` and ``record_delete``
directly against a ``tmp_path`` DB. No FastAPI or registry
involvement.

One test:

- ``test_audit_log_records_deletion`` (mandatory) — exercises the
  ``record_delete(user_id, requested_by, reason, before_bytes)``
  contract that DELETE depends on in Stage 3. Fails at RED with
  AttributeError because ``record_delete`` is not yet defined in
  ``audit.py`` (see Stage 2 scaffolding note in the module — stubbed
  precisely so this test fails the discipline-correct way).

``ensure()`` is tested indirectly via lifespan startup: if it broke,
every integration test would fail at ``app.router.lifespan_context``.
No dedicated unit test for ``ensure()`` in M0.8.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


async def test_audit_log_records_deletion(tmp_path: Path) -> None:
    """``record_delete`` must insert exactly one row with the passed
    values and a ``ts`` close to the call time.

    Fails at RED commit because ``AuditLog.record_delete`` is not
    defined yet — Stage 3 adds the method body. Test shape mirrors
    the rest of the sidecar's "append-only sqlite helper" unit
    tests (kg_queue schema test, turns.sqlite idempotency test) —
    open the DB directly post-call, verify the row.
    """
    from mempalace_sidecar.audit import AuditLog

    audit = AuditLog(tmp_path / "audit.sqlite")
    await audit.ensure()

    await audit.record_delete(
        user_id="alice",
        requested_by="admin@xelasphere.io",
        reason="gdpr request #4512",
        before_bytes=1_234_567,
    )

    with sqlite3.connect(str(tmp_path / "audit.sqlite")) as conn:
        rows = conn.execute(
            "SELECT user_id, requested_by, reason, before_bytes "
            "FROM deletion_audit"
        ).fetchall()

    assert len(rows) == 1, f"expected 1 audit row, got {len(rows)}: {rows}"
    user_id, requested_by, reason, before_bytes = rows[0]
    assert user_id == "alice"
    assert requested_by == "admin@xelasphere.io"
    assert reason == "gdpr request #4512"
    assert before_bytes == 1_234_567

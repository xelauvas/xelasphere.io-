"""Unit tests for KG queue + batched worker (plan M0.7).

Queue tests exercise ``KGQueue.{enqueue, claim, mark_done, mark_failed,
today_spend, add_spend}`` directly against a ``tmp_path`` sqlite.
Worker tests exercise ``_batch_by_user`` + ``_run_one_tick`` against
stub extractors.

No registry / FastAPI involvement — these are pure unit tests. Real
``handle.kg.add_triple`` wiring is exercised in the integration tests
at ``tests/integration/test_kg_queue_integration.py``.

Test list:
- ``test_kg_pending_schema_rejects_duplicate_enqueue`` (mandatory;
  UNIQUE constraint dedup).
- ``test_kg_pending_same_drawer_ids_different_users_both_allowed``
  (optional, rung-neutral; belt-and-suspenders for per-user UNIQUE
  scope).
- ``test_batch_by_user_slices_to_max_drawers`` (mandatory; deviation
  #1 N=8 chunking).
- ``test_worker_backoff_increments_attempts_on_failure`` (mandatory;
  backoff + cap attempts=5).
- ``test_worker_pauses_when_cap_hit`` (mandatory; cap ceiling).
- ``test_worker_inner_handler_propagates_cancelled_error`` (discovered
  addition, per dispatch reminder: write the CancelledError test FIRST
  to force correct handler shape).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Queue schema tests
# ---------------------------------------------------------------------------


async def test_kg_pending_schema_rejects_duplicate_enqueue(tmp_path: Path):
    """Plan M0.7 task 1: ``UNIQUE(user_id, drawer_ids_hash)`` dedups
    identical enqueue calls. The second call is a silent no-op at the
    Python layer — enqueue catches ``IntegrityError`` internally so
    the caller (ingest route fire-and-forget) never sees it.
    """
    from mempalace_sidecar.kg_queue import KGQueue

    queue = KGQueue(tmp_path / "kg_pending.sqlite")
    await queue.ensure()

    await queue.enqueue("alice", ["d1", "d2"])
    await queue.enqueue("alice", ["d1", "d2"])  # dup — silent no-op

    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM kg_pending WHERE user_id = ?", ("alice",)
        ).fetchone()[0]
    assert count == 1, (
        f"expected 1 row after duplicate enqueue, got {count}"
    )


async def test_kg_pending_same_drawer_ids_different_users_both_allowed(
    tmp_path: Path,
):
    """Belt-and-suspenders for the UNIQUE constraint's per-user scope.
    Two users enqueuing the SAME ``drawer_ids`` list must both land
    (distinct rows, one per user). Guards a future schema change that
    widened the UNIQUE constraint beyond
    ``(user_id, drawer_ids_hash)`` — such a change would make one of
    the two calls silently drop, losing a user's KG work.
    """
    from mempalace_sidecar.kg_queue import KGQueue

    queue = KGQueue(tmp_path / "kg_pending.sqlite")
    await queue.ensure()

    await queue.enqueue("alice", ["d1", "d2"])
    await queue.enqueue("bob", ["d1", "d2"])

    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        users = [
            r[0]
            for r in conn.execute(
                "SELECT user_id FROM kg_pending ORDER BY user_id"
            )
        ]
    assert users == ["alice", "bob"], (
        f"expected both users present, got {users}"
    )


# ---------------------------------------------------------------------------
# _batch_by_user helper (deviation #1)
# ---------------------------------------------------------------------------


def test_batch_by_user_slices_to_max_drawers():
    """Deviation #1: spec §5.5 says one LLM call per batch of N=8
    drawers. The ``_batch_by_user`` helper groups claimed rows by
    ``user_id`` and slices each user's concatenated drawer_ids into
    chunks of at most ``max_drawers``.

    Construct rows whose total drawer count exceeds 8 for one user
    and fits for another. Assert every output batch has
    ``len(drawer_ids) <= max_drawers`` AND every batch is
    single-user. Alice has 9 drawers total (3+3+3) → at least 2
    batches. Bob has 2 drawers in one row → 1 batch.
    """
    from mempalace_sidecar.kg_worker import _batch_by_user

    rows = [
        {"user_id": "alice", "drawer_ids": '["d1", "d2", "d3"]'},
        {"user_id": "alice", "drawer_ids": '["d4", "d5", "d6"]'},
        {"user_id": "bob", "drawer_ids": '["d7", "d8"]'},
        {"user_id": "alice", "drawer_ids": '["d9", "d10", "d11"]'},
    ]
    batches = _batch_by_user(rows, max_drawers=8)

    # Every batch respects max_drawers.
    for user_id, drawer_ids in batches:
        assert isinstance(user_id, str)
        assert isinstance(drawer_ids, list)
        assert len(drawer_ids) <= 8, (
            f"batch for {user_id} has {len(drawer_ids)} drawers, "
            f"expected <= 8"
        )

    # Alice: 9 drawers, must split into at least 2 batches.
    alice_batches = [d for u, d in batches if u == "alice"]
    assert sum(len(d) for d in alice_batches) == 9
    assert len(alice_batches) >= 2, (
        f"alice's 9 drawers should split into >=2 batches at "
        f"max_drawers=8, got {len(alice_batches)}"
    )

    # Bob: 2 drawers, exactly 1 batch.
    bob_batches = [d for u, d in batches if u == "bob"]
    assert sum(len(d) for d in bob_batches) == 2
    assert len(bob_batches) == 1

    # Empty-input case.
    assert _batch_by_user([], max_drawers=8) == []


# ---------------------------------------------------------------------------
# Worker backoff / attempts
# ---------------------------------------------------------------------------


async def test_worker_backoff_increments_attempts_on_failure(tmp_path: Path):
    """Plan M0.7 task 3 failure path: on extractor failure, the row's
    status goes back to 'pending', ``attempts`` increments,
    ``last_error`` captures the exception message. After ``attempts``
    reaches 5, status flips to 'failed' and no further tick picks it
    up.
    """
    from mempalace_sidecar.kg_queue import KGQueue
    from mempalace_sidecar.kg_worker import _run_one_tick

    queue = KGQueue(tmp_path / "kg_pending.sqlite")
    await queue.ensure()
    await queue.enqueue("alice", ["d1"])

    class FailingExtractor:
        async def extract(self, drawer_texts):
            raise RuntimeError("simulated extractor failure")

    # First tick — attempts=1, status back to 'pending'.
    await _run_one_tick(
        queue, registry=None, extractor=FailingExtractor(), cap_usd=5.0
    )

    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        row = conn.execute(
            "SELECT status, attempts, last_error FROM kg_pending "
            "WHERE user_id=?",
            ("alice",),
        ).fetchone()
    assert row[0] == "pending", (
        f"after 1 failure, status={row[0]}, expected 'pending'"
    )
    assert row[1] == 1, f"after 1 failure, attempts={row[1]}, expected 1"
    assert "simulated" in (row[2] or ""), (
        f"last_error should contain exception message, got {row[2]!r}"
    )

    # Run 4 more times to hit the attempts < 5 cap.
    for _ in range(4):
        await _run_one_tick(
            queue, registry=None, extractor=FailingExtractor(), cap_usd=5.0
        )

    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        row = conn.execute(
            "SELECT status, attempts FROM kg_pending WHERE user_id=?",
            ("alice",),
        ).fetchone()
    assert row[0] == "failed", (
        f"after 5 failures, status={row[0]}, expected 'failed'"
    )
    assert row[1] >= 5, (
        f"after 5 failures, attempts={row[1]}, expected >= 5"
    )


# ---------------------------------------------------------------------------
# Cap ceiling (plan M0.7 task 4)
# ---------------------------------------------------------------------------


async def test_worker_pauses_when_cap_hit(tmp_path: Path):
    """Plan M0.7 task 4: when ``today_spend`` exceeds ``cap_usd``, the
    tick short-circuits — ``claim`` is never called, no rows transition
    from 'pending' to 'running'/'done'. Raising the cap lets the tick
    proceed on the next invocation.
    """
    from mempalace_sidecar.kg_queue import KGQueue
    from mempalace_sidecar.kg_worker import _run_one_tick

    queue = KGQueue(tmp_path / "kg_pending.sqlite")
    await queue.ensure()
    await queue.enqueue("alice", ["d1"])

    # Push spend above the cap.
    await queue.add_spend(10.0)
    assert await queue.today_spend() >= 5.0

    class NullExtractor:
        async def extract(self, drawer_texts):
            return []

    # Tick with cap hit — row MUST NOT transition.
    await _run_one_tick(
        queue, registry=None, extractor=NullExtractor(), cap_usd=5.0
    )

    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        status = conn.execute(
            "SELECT status FROM kg_pending WHERE user_id=?", ("alice",)
        ).fetchone()[0]
    assert status == "pending", (
        f"expected status='pending' under cap-hit, got {status!r}"
    )

    # Raise the cap — next tick processes the row.
    await _run_one_tick(
        queue, registry=None, extractor=NullExtractor(), cap_usd=100.0
    )

    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        status = conn.execute(
            "SELECT status FROM kg_pending WHERE user_id=?", ("alice",)
        ).fetchone()[0]
    assert status == "done", (
        f"expected status='done' after cap cleared, got {status!r}"
    )


# ---------------------------------------------------------------------------
# CancelledError propagation (discovered, per dispatch reminder #4)
# ---------------------------------------------------------------------------


async def test_worker_inner_handler_propagates_cancelled_error(
    tmp_path: Path,
):
    """Deviation #3 asyncio-hygiene guard. The inner try/except
    around the extractor call catches ordinary ``Exception`` (so
    extractor failures are recorded via ``mark_failed``) but MUST NOT
    catch ``CancelledError`` — which inherits from ``BaseException``,
    not ``Exception``, in Python 3.8+.

    The test raises ``CancelledError`` from the extractor and asserts
    it propagates out of ``_run_one_tick``. Python's natural exception
    hierarchy makes this work; the test guards against a future
    refactor that broadens the handler to ``except BaseException:`` or
    ``except (Exception, CancelledError):`` — either of which would
    make the worker task uncancellable and hang the sidecar's
    shutdown sequence indefinitely (violating spec §5.7's 10s drain).

    Corollary assertion: the row is NOT marked 'failed'. Cancellation
    is a shutdown signal, not a retryable error. A refactor that
    caught CancelledError and called ``mark_failed`` would survive
    the propagation test (with tweaks) but fail this assertion.
    """
    from mempalace_sidecar.kg_queue import KGQueue
    from mempalace_sidecar.kg_worker import _run_one_tick

    queue = KGQueue(tmp_path / "kg_pending.sqlite")
    await queue.ensure()
    await queue.enqueue("alice", ["d1"])

    class CancellingExtractor:
        async def extract(self, drawer_texts):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _run_one_tick(
            queue,
            registry=None,
            extractor=CancellingExtractor(),
            cap_usd=5.0,
        )

    # Row must NOT be marked failed — CancelledError is a shutdown
    # signal, not a retryable error. Status may be 'running' (claimed
    # but not finished) — that's acceptable; what matters is that we
    # didn't treat shutdown as an extractor bug.
    with sqlite3.connect(str(tmp_path / "kg_pending.sqlite")) as conn:
        status, attempts = conn.execute(
            "SELECT status, attempts FROM kg_pending WHERE user_id=?",
            ("alice",),
        ).fetchone()
    assert status != "failed", (
        f"CancelledError must not be recorded as a failed attempt; "
        f"got status={status!r}, attempts={attempts}"
    )

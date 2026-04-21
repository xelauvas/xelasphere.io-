"""Integration tests for KG queue wiring + worker lifecycle (plan M0.7).

Two tests — both optional (each moves the ladder rung by +1):

- ``test_ingest_enqueues_kg_pending_row`` — POST /ingest against the
  real app; assert a row lands in the sidecar-level
  ``kg_pending.sqlite``. Verifies the fire-and-forget enqueue
  wire-up in ``routes/palace.py``.

- ``test_worker_lifecycle_cancels_cleanly_on_shutdown`` — drive
  ``app.router.lifespan_context(app)`` open/close. Asserts:
    (a) ``app.state.kg_worker_task`` exists during the with-block
        and is running.
    (b) After teardown, the task is done + cancelled cleanly.
    (c) **THE ORDERING** — the worker task finished BEFORE
        ``registry.close_all`` ran. Deviation #3's failure mode is
        "close_all first, worker dies mid-write, handles yanked";
        the ordering assertion is the only thing that catches it.

Shares conftest fixtures (app, auth, client, token) with the other
integration test modules. Fixture duplication was eliminated in the
M0.6 review response.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


async def test_ingest_enqueues_kg_pending_row(
    client, auth, tmp_path: Path
):
    """POST /ingest causes a row to land in the sidecar-level
    ``kg_pending.sqlite`` (under ``settings.data_dir``). The row
    carries the accepted drawer_id so the worker can locate the
    palace to write triples to.

    Exercises: ingest route's fire-and-forget enqueue wire-up,
    the ``await queue.enqueue(...)`` shape (not ``asyncio.create_task``
    — see dispatch enqueue-shape note), the try/except wrapper
    that prevents enqueue failures from failing ingest.
    """
    r = await client.post(
        "/palace/alice/ingest",
        headers=auth,
        json={
            "turn_id": "e" * 32,
            "session_id": "s",
            "pair_id": "f" * 32,
            "role": "user",
            "text": "first conversation turn",
            "ts": 1_700_000_000.0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    # Query the sidecar-level kg_pending.sqlite directly. The queue DB
    # lives at settings.data_dir / "kg_pending.sqlite" per
    # deviation #4; conftest's app fixture sets MEMPALACE_DATA_DIR to
    # tmp_path.
    queue_path = tmp_path / "kg_pending.sqlite"
    assert queue_path.exists(), (
        f"kg_pending.sqlite not created at {queue_path} — "
        f"ingest enqueue wire-up missing or pointing elsewhere"
    )
    with sqlite3.connect(str(queue_path)) as conn:
        rows = conn.execute(
            "SELECT user_id, drawer_ids, status FROM kg_pending"
        ).fetchall()
    assert len(rows) == 1, (
        f"expected exactly 1 kg_pending row after one ingest, "
        f"got {len(rows)}: {rows}"
    )
    user_id, drawer_ids_json, status = rows[0]
    assert user_id == "alice"
    assert status == "pending"
    # drawer_ids is a JSON-encoded non-empty array.
    assert drawer_ids_json.startswith("[") and drawer_ids_json.endswith("]")
    assert len(drawer_ids_json) > 2, (
        f"drawer_ids should be a non-empty JSON array, got {drawer_ids_json!r}"
    )


async def test_worker_lifecycle_cancels_cleanly_on_shutdown(app):
    """Deviation #3 teeth-bearing test: during lifespan teardown, the
    KG worker task is cancelled BEFORE ``registry.close_all`` runs.

    A test that only checks "task is cancelled after shutdown" would
    pass even with the wrong ordering (close_all first, then task
    cancel) — which is exactly the failure mode deviation #3 guards
    against ("close_all yanks handles out from under the worker
    mid-write").

    Verification approach: patch ``registry.close_all`` to record
    whether the worker task was done at the moment close_all ran. If
    the lifespan's order is correct (cancel worker → await task →
    then close_all), ``task.done()`` is True when close_all runs. If
    the order is reversed, ``task.done()`` is False when close_all
    runs — test fails with a clear message.

    The patch is an instance-level shadow of ``registry.close_all``,
    applied inside the with-block BEFORE teardown. When the lifespan
    finally-block does ``await app.state.registry.close_all()``, it
    picks up the instrumented version via normal attribute lookup.
    """
    task_done_when_close_all_ran: list[bool] = []

    async with app.router.lifespan_context(app):
        # Assertion (a): worker task exists and is running.
        assert hasattr(app.state, "kg_worker_task"), (
            "lifespan must attach kg_worker_task to app.state"
        )
        task = app.state.kg_worker_task
        assert not task.done(), (
            "worker task should be running during lifespan "
            "(found already done before teardown)"
        )

        # Instrument registry.close_all to record the task's done-state
        # at the moment close_all is invoked. Shadow the bound method
        # on the instance; the lifespan finally-block calls
        # app.state.registry.close_all() which resolves via attribute
        # lookup on the instance first.
        registry = app.state.registry
        original_close_all = registry.close_all

        async def instrumented_close_all():
            task_done_when_close_all_ran.append(task.done())
            return await original_close_all()

        registry.close_all = instrumented_close_all  # type: ignore[method-assign]

    # After teardown:

    # Assertion (b): worker task completed cleanly.
    assert task.done(), "task should be done post-lifespan"
    # Either cancelled() is True (CancelledError propagated), or the
    # task caught CancelledError and returned normally. Both are
    # clean; what's NOT clean is an unhandled non-CancelledError
    # exception.
    if not task.cancelled():
        exc = task.exception()
        assert exc is None, (
            f"worker task has unhandled exception: {type(exc).__name__}: {exc}"
        )

    # Assertion (c) — THE ORDERING.
    assert len(task_done_when_close_all_ran) == 1, (
        f"close_all should have been called exactly once during "
        f"shutdown; recorded {len(task_done_when_close_all_ran)} "
        f"invocations"
    )
    assert task_done_when_close_all_ran[0] is True, (
        "deviation #3 ordering violated: close_all was called while "
        "the KG worker task was still running. Expected order: "
        "cancel worker -> await task done -> THEN call close_all. "
        "Actual order had close_all before worker completion, which "
        "would yank palace handles out from under any in-flight "
        "triple writes."
    )

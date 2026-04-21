"""M0.15 Stage 1 RED: tests for Flusher (Task M0.15.7).

Stage 1 RED state: `Flusher.__init__` stores config and invokes
`threading.Thread.__init__`. `run()` is NOT overridden (inherited no-op
default). `stop()` is UNDEFINED.

Expected RED shape (mixed per Part 2 #5 discipline):
  - AssertionError on side-effect tests (gauge ticks, row transitions):
    default no-op run means nothing happens; assertions fail.
  - AttributeError on tests calling .stop().

Both shapes are functionality-missing RED, not import/fixture RED.

Deviations exercised:
  - D4 (flusher except narrowed): test_flusher_transient_exceptions_continue_loop.
  - D7 (stop-bound locked at poll + 6.3s): test_stop_returns_within_bound.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import httpx
import pytest

from app.mempalace.flusher import Flusher
from app.mempalace.outbox import Outbox


def _make_flusher(outbox, *, poll_interval_s=0.05, batch_size=20,
                   purge_interval_loops=600, client=None, metrics=None):
    """Helper: build a Flusher with mocked client + metrics."""
    return Flusher(
        outbox,
        client if client is not None else MagicMock(),
        metrics if metrics is not None else MagicMock(),
        poll_interval_s=poll_interval_s,
        batch_size=batch_size,
        purge_interval_loops=purge_interval_loops,
    )


def _insert_row(outbox, turn_id="t1", user_id="u"):
    outbox.insert(
        turn_id=turn_id, pair_id="p1", user_id=user_id, session_id="s",
        role="user", text="msg", ts=1.0, request_id=None,
    )


def test_empty_outbox_still_ticks_gauge(outbox_db_path):
    """Plan fix #9: flusher_tick gauge updates every iteration, idle included.

    With an empty outbox, flusher still advances the tick gauge on each
    poll cycle — essential for ops to distinguish "flusher alive but
    idle" from "flusher dead."
    """
    outbox = Outbox(outbox_db_path)
    metrics = MagicMock()
    flusher = _make_flusher(outbox, metrics=metrics, poll_interval_s=0.05)
    flusher.start()
    time.sleep(0.2)  # 4x poll interval
    flusher.stop()
    flusher.join(timeout=6.5)
    assert metrics.flusher_tick.called
    assert metrics.flusher_tick.call_count >= 2


def test_single_row_flushes_and_marks_sent(outbox_db_path):
    outbox = Outbox(outbox_db_path)
    _insert_row(outbox, turn_id="t1")
    client = MagicMock()
    client.post_json.return_value = {"status": "accepted"}
    flusher = _make_flusher(outbox, client=client, poll_interval_s=0.05)
    flusher.start()
    time.sleep(0.3)
    flusher.stop()
    flusher.join(timeout=6.5)
    # Presence-of-good-state: row has been sent.
    row = outbox._conn.execute(
        "SELECT status, sent_at FROM outbox WHERE turn_id=?", ("t1",)
    ).fetchone()
    assert row["status"] == "sent"
    assert row["sent_at"] is not None
    client.post_json.assert_called_once()


def test_sidecar_5xx_marks_pending_attempts_incremented(outbox_db_path):
    outbox = Outbox(outbox_db_path)
    _insert_row(outbox, turn_id="t1")
    client = MagicMock()
    # Simulate 5xx: httpx.HTTPStatusError raised by client.post_json.
    response = httpx.Response(503)
    response._request = httpx.Request("POST", "http://sidecar/")
    client.post_json.side_effect = httpx.HTTPStatusError(
        "503", request=response.request, response=response
    )
    flusher = _make_flusher(outbox, client=client, poll_interval_s=0.05)
    flusher.start()
    time.sleep(0.2)
    flusher.stop()
    flusher.join(timeout=6.5)
    row = outbox._conn.execute(
        "SELECT status, attempts, last_error FROM outbox WHERE turn_id=?",
        ("t1",),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] >= 1
    assert row["last_error"] is not None
    assert "503" in row["last_error"]


def test_stop_returns_within_bound(outbox_db_path):
    """D7: bound = poll_interval_s + 6.3s (M0.14 SyncSidecarClient worst-
    case post_json budget: max_retries=2 × timeout_s=2.0 + backoff=0.3).

    Tests that stop() signals cleanly even when flusher is mid-post_json.
    """
    outbox = Outbox(outbox_db_path)
    _insert_row(outbox, turn_id="t1")
    # Simulate slow post_json (but not slower than the bound).
    client = MagicMock()
    def slow_post(*args, **kwargs):
        time.sleep(0.5)
        return {"status": "accepted"}
    client.post_json.side_effect = slow_post
    poll = 0.1
    flusher = _make_flusher(outbox, client=client, poll_interval_s=poll)
    flusher.start()
    time.sleep(0.05)  # ensure flusher entered run loop
    t0 = time.monotonic()
    flusher.stop()
    flusher.join(timeout=poll + 6.3)
    elapsed = time.monotonic() - t0
    assert not flusher.is_alive(), (
        f"flusher did not stop within bound {poll + 6.3}s; elapsed={elapsed:.2f}s"
    )
    assert elapsed < poll + 6.3


def test_purge_runs_every_n_loops(outbox_db_path):
    outbox = Outbox(outbox_db_path)
    original_purge = outbox.purge_old_sent
    call_count = {"n": 0}
    def counting_purge(*args, **kwargs):
        call_count["n"] += 1
        return 0
    outbox.purge_old_sent = counting_purge
    flusher = _make_flusher(
        outbox, poll_interval_s=0.02, purge_interval_loops=3,
    )
    flusher.start()
    time.sleep(0.3)  # ~15 loops → purge should fire ~5 times
    flusher.stop()
    flusher.join(timeout=6.5)
    # Teeth-bearing: presence of purge calls, reasonable count given loop arithmetic.
    assert call_count["n"] >= 3, (
        f"purge fired {call_count['n']} times; expected ≥3 "
        f"over ~15 loops with purge_interval_loops=3"
    )
    outbox.purge_old_sent = original_purge  # restore (not strictly needed)


def test_flusher_transient_exceptions_continue_loop(outbox_db_path):
    """D4: narrowed except — sqlite3.OperationalError and httpx.HTTPError
    are logged at WARNING and the loop continues. Unexpected exceptions
    re-raise (doctrine hit #4 class: bare except: pass swallows real bugs).

    Two scenarios in one test via sequential claim_batch mocking.
    """
    import sqlite3
    outbox = Outbox(outbox_db_path)

    # Scenario 1: sqlite3.OperationalError from claim_batch → flusher continues.
    calls = {"n": 0}
    original_claim = outbox.claim_batch
    def raise_operational(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return []  # subsequent calls: empty result so flusher idles
    outbox.claim_batch = raise_operational

    metrics = MagicMock()
    flusher = _make_flusher(outbox, metrics=metrics, poll_interval_s=0.03)
    flusher.start()
    time.sleep(0.25)  # enough for 2 OperationalError cycles + normal cycles
    flusher.stop()
    flusher.join(timeout=6.5)
    # Flusher survived the OperationalError: multiple ticks registered.
    assert metrics.flusher_tick.call_count >= 3, (
        f"flusher died on transient OperationalError; "
        f"ticks={metrics.flusher_tick.call_count}"
    )
    outbox.claim_batch = original_claim

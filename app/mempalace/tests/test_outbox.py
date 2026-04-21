"""M0.15 Stage 1 RED: tests for Outbox (Tasks M0.15.1-6).

Stage 1 RED state: `app.mempalace.outbox.Outbox.__init__` is complete
(schema + WAL + PRAGMAs per D1/D2/D9). Every data-access method
(`insert`, `depth`, `claim_batch`, `mark_sent`, `mark_failed`,
`abandon_user`, `drain_then_abandon`, `purge_old_sent`) is UNDEFINED.

Expected RED shape:
  - PASS at RED: test_outbox_schema_creates_tables_and_indexes (M0.15.1),
    test_wal_mode_enabled_after_init (D2) — both exercise __init__
    infrastructure which is complete at Stage 1.
  - FAIL at RED with AttributeError: all tests that call Outbox data-access
    methods. That's the "functionality-missing" RED shape per template.

Deviations exercised:
  - D2 (WAL init verification): test_wal_mode_enabled_after_init.
  - D3 (mark_failed atomic UPDATE): test_mark_failed_abandoned_branch_clears_claim_token.
  - D6 (genuine concurrency): test_concurrent_claim_batches_dont_overlap_from_two_threads.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.mempalace.outbox import Outbox


# ============================================================================
# M0.15.1 — Schema + WAL mode
# ============================================================================


def test_outbox_schema_creates_tables_and_indexes(outbox_db_path):
    """Infrastructure test: __init__ creates the outbox table + three indexes.

    Passes at Stage 1 RED because schema setup is part of __init__.
    """
    outbox = Outbox(outbox_db_path)
    rows = outbox._conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "outbox" in names
    assert "outbox_pending_idx" in names
    assert "outbox_sent_purge_idx" in names
    assert "outbox_user_idx" in names


def test_wal_mode_enabled_after_init(outbox_db_path):
    """D2: WAL mode verified at __init__. Passes at Stage 1 RED; presence-
    of-good-state assertion gates the D2 check from silent fallback.
    """
    outbox = Outbox(outbox_db_path)
    mode = outbox._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


# ============================================================================
# M0.15.2 — insert()
# ============================================================================


def test_insert_duplicate_turn_id_raises_integrity_error(outbox_db_path):
    outbox = Outbox(outbox_db_path)
    outbox.insert(
        turn_id="t1", pair_id="p1", user_id="u", session_id="s",
        role="user", text="msg", ts=1.0, request_id=None,
    )
    with pytest.raises(sqlite3.IntegrityError):
        outbox.insert(
            turn_id="t1", pair_id="p2", user_id="u", session_id="s",
            role="user", text="msg-dup", ts=2.0, request_id=None,
        )


# ============================================================================
# M0.15.3 — depth() + claim_batch() with serialization
# ============================================================================


def test_two_claim_batches_never_overlap(outbox_db_path):
    """Plan sequential version. Tests that claim_batch properly transitions
    rows OUT of 'pending' status; does NOT exercise concurrent claims
    (see test_concurrent_claim_batches_dont_overlap_from_two_threads for
    genuine concurrency — D6).
    """
    a = Outbox(outbox_db_path)
    b = Outbox(outbox_db_path)
    for i in range(10):
        a.insert(
            turn_id=f"t{i}", pair_id=f"p{i}", user_id="u",
            session_id="s", role="user", text=f"msg{i}", ts=i,
            request_id=None,
        )
    ra = a.claim_batch(10)
    rb = b.claim_batch(10)
    ids_a = {r["id"] for r in ra}
    ids_b = {r["id"] for r in rb}
    assert not (ids_a & ids_b), f"overlap! {ids_a & ids_b}"
    assert len(ids_a) + len(ids_b) <= 10


def test_concurrent_claim_batches_dont_overlap_from_two_threads(outbox_db_path):
    """D6: the actual multi-flusher safety guarantee test.

    Two threads, each with own Outbox instance, synchronized via
    threading.Barrier to enter claim_batch simultaneously. Exercises
    BEGIN IMMEDIATE cross-connection mutual exclusion under real
    concurrent load (nastiest reachable context for the claim_batch
    serialization invariant).

    Presence-of-good-state: every inserted row appears in exactly one
    thread's claim result, not both.
    """
    # Seed via one instance; both threads then each claim via their own.
    seed = Outbox(outbox_db_path)
    for i in range(10):
        seed.insert(
            turn_id=f"t{i}", pair_id=f"p{i}", user_id="u",
            session_id="s", role="user", text=f"msg{i}", ts=i,
            request_id=None,
        )

    a = Outbox(outbox_db_path)
    b = Outbox(outbox_db_path)
    results: dict[str, list] = {"a": [], "b": []}
    barrier = threading.Barrier(2)

    def claim(instance: Outbox, bucket: list):
        barrier.wait()
        bucket.extend(instance.claim_batch(10))

    t_a = threading.Thread(target=claim, args=(a, results["a"]))
    t_b = threading.Thread(target=claim, args=(b, results["b"]))
    t_a.start()
    t_b.start()
    t_a.join(timeout=10.0)
    t_b.join(timeout=10.0)
    assert not t_a.is_alive() and not t_b.is_alive(), "thread did not complete"

    ids_a = {r["id"] for r in results["a"]}
    ids_b = {r["id"] for r in results["b"]}
    assert not (ids_a & ids_b), (
        f"concurrent claim overlap: {ids_a & ids_b} — BEGIN IMMEDIATE "
        f"did not enforce mutual exclusion"
    )
    assert ids_a | ids_b == {i + 1 for i in range(10)}, (
        f"union != all 10 rows; some were lost"
    )


# ============================================================================
# M0.15.4 — mark_sent() / mark_failed()
# ============================================================================


def test_mark_sent_transitions_rows_and_clears_claim_token(outbox_db_path):
    outbox = Outbox(outbox_db_path)
    outbox.insert(
        turn_id="t1", pair_id="p1", user_id="u", session_id="s",
        role="user", text="msg", ts=1.0, request_id=None,
    )
    claimed = outbox.claim_batch(1)
    assert len(claimed) == 1
    row_id = claimed[0]["id"]
    outbox.mark_sent([row_id])
    row = outbox._conn.execute(
        "SELECT status, claim_token, sent_at FROM outbox WHERE id=?", (row_id,)
    ).fetchone()
    assert row["status"] == "sent"
    assert row["claim_token"] is None
    assert row["sent_at"] is not None


def test_mark_failed_increments_attempts_or_abandons_at_5(outbox_db_path):
    """Drive a row through 5 failures. After each mark_failed, assert the
    row's status BEFORE the next claim_batch — otherwise the re-claim
    mutates the state the assertion is trying to verify (intra-author
    plan-half inconsistency caught at Stage 3 GREEN, 2026-04-21).
    """
    outbox = Outbox(outbox_db_path)
    outbox.insert(
        turn_id="t1", pair_id="p1", user_id="u", session_id="s",
        role="user", text="msg", ts=1.0, request_id=None,
    )
    claimed = outbox.claim_batch(1)
    row_id = claimed[0]["id"]
    # Drive through 5 failures. Assert AFTER mark_failed and BEFORE the
    # next re-claim, so the assertion observes mark_failed's effect
    # rather than the subsequent re-claim's.
    for attempt in range(1, 6):
        outbox.mark_failed(row_id, f"err{attempt}")
        row = outbox._conn.execute(
            "SELECT status, attempts FROM outbox WHERE id=?", (row_id,)
        ).fetchone()
        assert row["attempts"] == attempt
        if attempt < 5:
            assert row["status"] == "pending", (
                f"after failure {attempt}, status={row['status']} "
                f"(expected 'pending' before 5th failure)"
            )
            # Re-claim for next iteration (production path:
            # failed row → pending → re-claimed → fail again).
            outbox.claim_batch(1)
        else:
            # 5th failure: transitions to 'abandoned'.
            assert row["status"] == "abandoned", (
                f"after 5th failure, status={row['status']} "
                f"(expected 'abandoned')"
            )


def test_mark_failed_abandoned_branch_clears_claim_token(outbox_db_path):
    """D3: the abandoned UPDATE must also clear claim_token atomically.
    Otherwise an 'abandoned with stale claim_token' intermediate state exists.

    Presence-of-good-state: query claim_token column directly after the
    abandon-triggering failure; assert NULL.
    """
    outbox = Outbox(outbox_db_path)
    outbox.insert(
        turn_id="t1", pair_id="p1", user_id="u", session_id="s",
        role="user", text="msg", ts=1.0, request_id=None,
    )
    row_id = outbox.claim_batch(1)[0]["id"]
    # Drive through 5 failures to trigger abandoned branch.
    for i in range(5):
        outbox.mark_failed(row_id, f"err{i}")
        if i < 4:
            outbox.claim_batch(1)
    row = outbox._conn.execute(
        "SELECT status, claim_token FROM outbox WHERE id=?", (row_id,)
    ).fetchone()
    assert row["status"] == "abandoned"
    assert row["claim_token"] is None, (
        f"abandoned row has stale claim_token={row['claim_token']!r} — "
        f"D3 atomic UPDATE not applied"
    )


# ============================================================================
# M0.15.5 — abandon_user() + drain_then_abandon()
# ============================================================================


def test_abandon_user_only_affects_named_user(outbox_db_path):
    outbox = Outbox(outbox_db_path)
    for uid in ("u1", "u2"):
        for i in range(3):
            outbox.insert(
                turn_id=f"{uid}-t{i}", pair_id=f"p{i}", user_id=uid,
                session_id="s", role="user", text="msg", ts=i,
                request_id=None,
            )
    count = outbox.abandon_user("u1")
    assert count == 3
    u1_rows = outbox._conn.execute(
        "SELECT status FROM outbox WHERE user_id='u1'"
    ).fetchall()
    u2_rows = outbox._conn.execute(
        "SELECT status FROM outbox WHERE user_id='u2'"
    ).fetchall()
    assert all(r["status"] == "abandoned" for r in u1_rows)
    assert all(r["status"] == "pending" for r in u2_rows)


def test_drain_then_abandon_returns_drained_and_abandoned_counts(outbox_db_path):
    """Plan test: stub flush_callable (no real Flusher thread — D5 signature
    decouples drain_then_abandon from Flusher).
    """
    outbox = Outbox(outbox_db_path)
    for i in range(5):
        outbox.insert(
            turn_id=f"t{i}", pair_id=f"p{i}", user_id="u",
            session_id="s", role="user", text="msg", ts=i,
            request_id=None,
        )

    drained_rows = []

    def stub_flush(row):
        drained_rows.append(row["turn_id"])
        # Successful flush — drain_then_abandon should mark_sent these.

    result = outbox.drain_then_abandon(stub_flush, timeout_s=5.0, poll_s=0.01)
    assert result["drained"] == 5
    assert result["abandoned"] == 0
    assert set(drained_rows) == {f"t{i}" for i in range(5)}


# ============================================================================
# M0.15.6 — purge_old_sent()
# ============================================================================


def test_purge_old_sent_removes_rows_past_cutoff(outbox_db_path):
    import time

    outbox = Outbox(outbox_db_path)
    # Seed 3 "old sent" rows + 2 "recent sent" rows.
    now = time.time()
    old_ts = now - (8 * 86400)  # 8 days ago
    recent_ts = now - (1 * 86400)  # 1 day ago
    for i in range(3):
        outbox.insert(
            turn_id=f"old{i}", pair_id=f"p{i}", user_id="u",
            session_id="s", role="user", text="msg", ts=i,
            request_id=None,
        )
        outbox._conn.execute(
            "UPDATE outbox SET status='sent', sent_at=? WHERE turn_id=?",
            (old_ts, f"old{i}"),
        )
    for i in range(2):
        outbox.insert(
            turn_id=f"new{i}", pair_id=f"p{i}", user_id="u",
            session_id="s", role="user", text="msg", ts=i,
            request_id=None,
        )
        outbox._conn.execute(
            "UPDATE outbox SET status='sent', sent_at=? WHERE turn_id=?",
            (recent_ts, f"new{i}"),
        )
    outbox._conn.commit()

    removed = outbox.purge_old_sent(days=7)
    assert removed == 3
    remaining = outbox._conn.execute(
        "SELECT turn_id FROM outbox ORDER BY turn_id"
    ).fetchall()
    assert {r["turn_id"] for r in remaining} == {"new0", "new1"}

"""Integration tests for POST /palace/{user_id}/ingest (plan M0.4.2).

These exercise the full stack: HTTP → FastAPI lifespan → PalaceRegistry →
PalaceHandle → real Chroma/sqlite on tmp_path. Marked ``integration``
because they open a real palace (embedding function load + Chroma
PersistentClient + sqlite) on every run; a unit-level ingest test would
mock past the single most important invariant here (``turns.sqlite`` as
authoritative dedup store).

Four tests:

* ``test_ingest_twice_returns_duplicate`` — authoritative dedup via
  ``turns.sqlite`` PK.
* ``test_ingest_idempotency_key_header_advisory`` — the ``Idempotency-Key``
  HTTP header is correlation-only; dedup decisions are made by
  ``turns.sqlite``, not by header caching.
* ``test_ingest_idempotency_key_mismatch_returns_400`` — header-vs-body
  sanity check; header present AND ``!= body.turn_id`` is a contract
  violation.
* ``test_concurrent_ingest_same_user_serialises`` (teeth-bearing) —
  ten concurrent writes with distinct turn_ids all land, without
  interleaving corruption. Exercises the writer_lock discipline.

Lifespan note: httpx 0.28's ``ASGITransport`` no longer drives the ASGI
lifespan protocol. The fixtures below drive lifespan explicitly via
``app.router.lifespan_context(app)`` — matches what a real uvicorn run
would do, without pulling in ``asgi-lifespan`` as a test-only dep. The
pattern is the same as ``test_sd_notify.py`` (which uses ``TestClient``
for its synchronous equivalent).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

# Fixtures (app, auth, client, token) come from tests/integration/conftest.py
# — shared across all integration test modules after the M0.6 review response
# unified them.

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------


async def test_ingest_twice_returns_duplicate(client, auth, tmp_path: Path):
    """Plan M0.4.2 Step 1 + §4.1 invariant.

    Two identical POSTs with the same body (same turn_id). First returns
    ``accepted``; second returns ``duplicate``. Chroma holds exactly one
    row for that turn_id — the library's upsert on the deterministic
    drawer_id is idempotent, and more importantly the route's pre-insert
    SELECT in ``turns.sqlite`` prevents the library call on the second
    pass (see ``palace_handle.add_drawer``).
    """
    body = {
        "turn_id": "018f1bce-9ab7-7bca-baaa-000000000000",
        "session_id": "s1",
        "pair_id": "018f1bce-9ab7-7bca-baaa-111111111111",
        "role": "user",
        "text": "hello world",
        "ts": 1_700_000_000.0,
    }
    r1 = await client.post("/palace/alice/ingest", json=body, headers=auth)
    r2 = await client.post("/palace/alice/ingest", json=body, headers=auth)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["status"] == "accepted"
    assert r2.json()["status"] == "duplicate"

    # Belt-and-suspenders: directly query the handle-owned turns.sqlite
    # to assert only one row landed. If the route ever accidentally ran
    # the insert twice, this would fire.
    turns_path = tmp_path / "palaces" / "alice" / "turns.sqlite"
    assert turns_path.exists(), f"palace not created at {turns_path}"
    conn = sqlite3.connect(str(turns_path))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE turn_id = ?",
            (body["turn_id"],),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1, f"expected 1 turns row, got {count}"


async def test_ingest_idempotency_key_header_advisory(client, auth):
    """Plan M0.4 critical rule 1: the ``Idempotency-Key`` header is
    advisory, not authoritative.

    Flow:
      1. POST with ``Idempotency-Key`` matching body.turn_id — accepted.
      2. POST the SAME body WITHOUT the header — must return
         ``duplicate``.

    If the route were caching responses by header value (bad pattern, see
    Stripe-style idempotency replay), step 2 would come back as
    ``accepted`` again (fresh write — no cached header). The authoritative
    store is ``turns.sqlite``; the header is correlation-only.
    """
    body = {
        "turn_id": "018f1bce-9ab7-7bca-baaa-222222222222",
        "session_id": "s2",
        "pair_id": "018f1bce-9ab7-7bca-baaa-333333333333",
        "role": "assistant",
        "text": "advisory key test",
        "ts": 1_700_000_001.0,
    }
    headers_with_key = {**auth, "Idempotency-Key": body["turn_id"]}
    r1 = await client.post(
        "/palace/bob/ingest", json=body, headers=headers_with_key
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "accepted"

    # Same body, header absent. turns.sqlite still remembers this turn_id.
    r2 = await client.post("/palace/bob/ingest", json=body, headers=auth)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate", (
        "Idempotency-Key header was treated as authoritative — "
        "removing it should NOT produce a fresh accepted. "
        "Dedup lives in turns.sqlite per spec §4.1."
    )


async def test_ingest_idempotency_key_mismatch_returns_400(client, auth):
    """Plan M0.4 critical rule 1: header-vs-body mismatch is a contract
    violation, not a dedup decision.

    If the client sends ``Idempotency-Key: X`` and ``body.turn_id = Y``,
    the sidecar returns 400. This is a client-programming-error
    detector, not a retry-safety mechanism — the dedup itself lives in
    ``turns.sqlite``.
    """
    body = {
        "turn_id": "aaaaaaaa-aaaa-7aaa-aaaa-aaaaaaaaaaaa",
        "session_id": "s3",
        "pair_id": "bbbbbbbb-bbbb-7bbb-bbbb-bbbbbbbbbbbb",
        "role": "user",
        "text": "mismatched header",
        "ts": 1_700_000_002.0,
    }
    mismatched = {
        **auth,
        "Idempotency-Key": "cccccccc-cccc-7ccc-cccc-cccccccccccc",  # != body.turn_id
    }
    r = await client.post(
        "/palace/carol/ingest", json=body, headers=mismatched
    )
    assert r.status_code == 400, (
        f"expected 400 on header-vs-body mismatch; got {r.status_code}: "
        f"{r.text}"
    )


async def test_concurrent_ingest_same_user_serialises(
    client, auth, tmp_path: Path
):
    """TEETH-BEARING (plan M0.4 critical rule 3 + spec §5.3 concurrency).

    Fire ~10 concurrent POSTs with distinct turn_ids at the same user_id.
    All must return 200/accepted. After the dust settles:

      1. ``turns.sqlite`` has exactly 10 rows (no INSERT collisions).
      2. Every response's implied drawer is unique (no two turns accepted
         with the same drawer_id — which would imply interleaved library
         calls under a dropped writer_lock).
      3. Chroma count matches — not asserted here because counting Chroma
         requires opening another client; the turns.sqlite row count +
         the handle's INSERT-after-library-call ordering is sufficient.

    If the route ever drops the writer_lock acquisition, two concurrent
    add_drawer calls can interleave between SELECT and INSERT, both
    pass the dedup check, both call the library — the second library
    call upserts the same drawer_id (because our derivation is turn_id-
    keyed, not rand()-keyed), which silently corrupts nothing per se,
    but turns.sqlite ends up with two rows for the same drawer_id. That
    would fire assertion 2 below.

    We use DISTINCT turn_ids (not the same turn_id) because hammering
    the same turn_id hides a dropped lock behind the INSERT-OR-IGNORE
    idempotency in handle.add_drawer. Distinct turn_ids exercise the
    pure concurrency path.
    """
    user = "dave"
    n = 10
    bodies = [
        {
            "turn_id": f"018f1bce-9ab7-7bca-baaa-{i:012x}",
            "session_id": "concurrent-s",
            "pair_id": f"018f1bce-9ab7-7bca-bbbb-{i:012x}",
            "role": "user" if i % 2 == 0 else "assistant",
            "text": f"concurrent message #{i}",
            "ts": 1_700_000_100.0 + i,
        }
        for i in range(n)
    ]

    async def fire(body: dict):
        return await client.post(
            f"/palace/{user}/ingest", json=body, headers=auth
        )

    # Warm up the palace with one sequential call first. Otherwise all 10
    # concurrent calls race to do the FIRST ever open of a new palace,
    # which triggers embedding-function load, Chroma client init, and
    # directory creation — noise orthogonal to the write-lock discipline
    # we're actually testing. One warmup call, then the real concurrent
    # fan-out.
    warmup = {
        "turn_id": "00000000-0000-7000-0000-000000000001",
        "session_id": "concurrent-s",
        "pair_id": "00000000-0000-7000-0000-000000000002",
        "role": "user",
        "text": "warmup",
        "ts": 1_700_000_099.0,
    }
    warm_resp = await fire(warmup)
    assert warm_resp.status_code == 200

    results = await asyncio.gather(*(fire(b) for b in bodies))

    statuses = [r.status_code for r in results]
    bodies_out = [r.json() for r in results]
    assert all(s == 200 for s in statuses), (
        f"some concurrent ingests failed: {list(zip(statuses, bodies_out))}"
    )
    accepted_count = sum(1 for b in bodies_out if b.get("status") == "accepted")
    assert accepted_count == n, (
        f"expected {n} 'accepted', got {accepted_count}: {bodies_out}"
    )

    # Disk evidence: turns.sqlite has exactly n rows for our warmup + n
    # test turns = n+1 total. drawer_ids must all be unique (teeth).
    turns_path = tmp_path / "palaces" / user / "turns.sqlite"
    conn = sqlite3.connect(str(turns_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        unique_drawers = conn.execute(
            "SELECT COUNT(DISTINCT drawer_id) FROM turns"
        ).fetchone()[0]
    finally:
        conn.close()
    assert total == n + 1, (
        f"expected {n + 1} turns rows (incl. warmup), got {total}"
    )
    assert unique_drawers == n + 1, (
        f"drawer_id collision detected: {unique_drawers} distinct vs "
        f"{total} rows — writer_lock discipline broken"
    )


async def test_ingest_during_shutdown_returns_503(client, auth):
    """Spec review-fix (quality review Important #3): when the registry is
    in shutdown, ``registry.open`` raises ``RuntimeError`` with the
    shutdown message. The route maps this to HTTP 503, not 500, so
    health-checkers and load balancers correctly classify the sidecar
    as unavailable rather than broken.
    """
    # Flip the shutdown flag on the live registry without actually
    # closing handles — we just want to exercise the route's branch.
    registry = client._transport.app.state.registry  # type: ignore[attr-defined]
    registry._shutting_down = True
    try:
        body = {
            "turn_id": "018f1bce-9ab7-7bca-baaa-ffffffffffff",
            "session_id": "shutdown-s",
            "pair_id": "018f1bce-9ab7-7bca-bbbb-ffffffffffff",
            "role": "user",
            "text": "too late",
            "ts": 1_700_000_999.0,
        }
        r = await client.post("/palace/eve/ingest", json=body, headers=auth)
        assert r.status_code == 503, (
            f"expected 503 during shutdown; got {r.status_code}: {r.text}"
        )
    finally:
        registry._shutting_down = False

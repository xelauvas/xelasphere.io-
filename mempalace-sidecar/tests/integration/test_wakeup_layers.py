"""Integration tests for POST /palace/{uid}/wake-up (plan M0.5.3).

Four tests:

  * ``test_wake_up_returns_all_four_layers`` — end-to-end shape test:
    seed 10 drawers in a session, POST /wake-up, verify every layer
    key is present and L1 (session) is populated.

  * ``test_wakeup_concurrent_faster_than_sum_of_parts`` — TEETH-BEARING,
    non-skippable. Enforces the ``asyncio.to_thread`` wrapping via a
    wall-clock comparison against a serial lower bound. The 0.7×
    coefficient is load-bearing (see the test's inline comment); DO
    NOT loosen it to make a failing implementation pass.

  * ``test_wakeup_during_shutdown_returns_503`` — mirrors the M0.4
    equivalent: ``registry.open`` raising "shutting down" maps to HTTP
    503 in the route.

  * ``test_wakeup_composition_within_latency_budget`` — spec §4.2 p95
    budget (< 500 ms hot). Slow-marker so CI can skip if budget flakes;
    real enforcement is the sum-of-parts test above.

Measurement approach for the 0.7× test: since M0.6 hasn't landed yet
(no /drawers, /search routes), we time the PalaceHandle layer methods
DIRECTLY via the live registry (reader_lock + to_thread). Same cost
model as the plan sketch's /drawers + /search probes — we just bypass
the HTTP layer for the component timings. The wake-up total is still
measured through the full HTTP + asyncio stack.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


async def _seed_drawers(client, auth, user: str, n: int, session: str = "s1") -> None:
    """Seed ``n`` drawers through the real ingest route.

    We go via HTTP + the real palace so size, Chroma state, and
    turns.sqlite all match a live sidecar — no shortcuts.
    """
    for i in range(n):
        r = await client.post(
            f"/palace/{user}/ingest",
            headers=auth,
            json={
                "turn_id": f"{user}{i:040d}",
                "session_id": session,
                "pair_id": f"{user}p{i:040d}",
                "role": "user" if i % 2 == 0 else "assistant",
                "text": f"content turn {i} — retrievable words and phrases for layer {i % 4}",
                "ts": 1_700_000_000.0 + i,
            },
        )
        assert r.status_code == 200, r.text


async def test_wake_up_returns_all_four_layers(client, auth) -> None:
    """Seed 10 drawers in session s1; POST /wake-up; assert every
    layer key is present and L1 (session) is non-empty (we seeded it).

    L2 and L3 may or may not return hits depending on Chroma's
    similarity threshold, but the key must ALWAYS be present.
    """
    await _seed_drawers(client, auth, "u", n=10, session="s1")

    r = await client.post(
        "/palace/u/wake-up",
        headers=auth,
        json={"query": "content turn 5", "k": 5, "session_id": "s1"},
    )
    assert r.status_code == 200, r.text
    ctx = r.json()

    assert ctx["user_id"] == "u"
    for layer in ("l0_recent", "l1_session", "l2_topic", "l3_semantic"):
        assert layer in ctx, f"wake-up response missing {layer}"
    assert len(ctx["l1_session"]) > 0, "L1 empty despite seeded session"
    # L0 is query-independent — should always have drawers after seeding.
    assert len(ctx["l0_recent"]) > 0, "L0 empty despite seeded drawers"

    # Meta present and sensible.
    assert "meta" in ctx
    assert ctx["meta"]["hot"] is True  # second open on same user = hot
    assert ctx["meta"]["total_ms"] >= 0


async def test_wakeup_concurrent_faster_than_sum_of_parts(
    app, auth, client
) -> None:
    """TEETH-BEARING (plan M0.5.3 + spec §5.3 concurrency).

    Assertion: wake-up total wall time is strictly less than 0.7× the
    serial lower bound computed from component layer timings.

    The 0.7× coefficient is NOT arbitrary — see the plan comment at
    M0.5.3. Ideal four-way parallelism is ~0.33×; real-world thread-
    pool + GIL overhead lands higher. 0.7× catches a serial
    implementation (ratio ~1.0) with wide margin while tolerating
    thread scheduling on modest CI hardware.

    If THIS test fails, the fix is ``asyncio.to_thread`` wrapping in
    ``build_wakeup``, NOT loosening the coefficient. Component times
    are logged on every run for flake diagnosis.
    """
    user = "par"
    # Non-trivial seed so each layer has real work to do. 300 drawers
    # is enough for Chroma's embedding cost to dominate and the
    # sort-in-Python path in recent_drawers to take measurable time.
    await _seed_drawers(client, auth, user, n=300, session="s1")

    # Warm both the palace (registry cold → hot) and Chroma's internal
    # query path (first search tends to be slower than subsequent ones
    # even once the model is loaded — Chroma compiles the plan).
    r = await client.post(
        f"/palace/{user}/wake-up",
        headers=auth,
        json={"query": "retrievable content", "k": 5, "session_id": "s1"},
    )
    assert r.status_code == 200

    # Pull the live handle out of the registry for component timings.
    # We go under the same reader_lock discipline the route uses, wrap
    # in asyncio.to_thread for a fair comparison.
    registry = app.state.registry
    entry = await registry.open(user)
    handle = entry.handle
    lock = registry.lock_for(user)

    # L0-like component timing: handle.recent_drawers. Cheap Chroma
    # get + python sort; stands in for the plan's /drawers route.
    t0 = time.monotonic()
    async with lock.reader_lock:
        await asyncio.to_thread(handle.recent_drawers, limit=5)
    l0_like = time.monotonic() - t0

    # L3-like component timing: handle.semantic_search. Hybrid vector+
    # BM25 query — the expensive one. Stands in for /search.
    t0 = time.monotonic()
    async with lock.reader_lock:
        await asyncio.to_thread(
            handle.semantic_search, "retrievable content", 5
        )
    l3_like = time.monotonic() - t0

    # Full wake-up timing — the whole composition (HTTP + FastAPI +
    # build_wakeup + 4 layer calls) under measurement.
    t0 = time.monotonic()
    r = await client.post(
        f"/palace/{user}/wake-up",
        headers=auth,
        json={"query": "retrievable content", "k": 5, "session_id": "s1"},
    )
    total = time.monotonic() - t0
    assert r.status_code == 200

    # Serial lower bound: wake-up does L0 (l0_like) + L1 (cheap sqlite,
    # subsumed in l0_like) + L2 (same cost as L3) + L3. A serial
    # implementation totals at LEAST l0 + 2*l3. We compare the measured
    # wake-up total to 0.7× that bound.
    serial_bound = l0_like + 2 * l3_like
    ratio = total / serial_bound if serial_bound else 1.0

    print(
        f"[wakeup-parallelism] l0={l0_like*1000:.1f}ms "
        f"l3={l3_like*1000:.1f}ms total={total*1000:.1f}ms "
        f"serial_bound={serial_bound*1000:.1f}ms ratio={ratio:.2f} "
        f"(threshold 0.70)",
        file=sys.stderr,
    )

    assert total < 0.7 * serial_bound, (
        f"Wake-up took {total*1000:.0f} ms; serial bound "
        f"{serial_bound*1000:.0f} ms. Ratio {ratio:.2f} >= 0.7 — "
        f"layers may be running sequentially. Ensure build_wakeup "
        f"wraps each layer in asyncio.to_thread. "
        f"l0={l0_like*1000:.1f}ms l3={l3_like*1000:.1f}ms."
    )


async def test_wakeup_during_shutdown_returns_503(client, auth) -> None:
    """Mirror of ``test_ingest_during_shutdown_returns_503`` (M0.4).

    When ``registry._shutting_down`` is flipped, ``registry.open``
    raises ``RuntimeError("...shutting down...")``. The wake-up route
    must map this to HTTP 503, same as ingest.
    """
    registry = client._transport.app.state.registry  # type: ignore[attr-defined]
    registry._shutting_down = True
    try:
        r = await client.post(
            "/palace/eve/wake-up",
            headers=auth,
            json={"query": "hi", "k": 5, "session_id": "s"},
        )
        assert r.status_code == 503, (
            f"expected 503 during shutdown; got {r.status_code}: {r.text}"
        )
    finally:
        registry._shutting_down = False


async def test_wakeup_warmup_param_skips_composition(client, auth) -> None:
    """The ``?warmup=true`` query param is a connection-warmup hook —
    it opens the palace (so the user's first real wake-up is hot) but
    skips the L0-L3 composition.

    Returns an EMPTY WakeupContext with meta.hot reflecting whether
    this open was cold (first) or hot (cached).
    """
    r = await client.post(
        "/palace/warm/wake-up?warmup=true",
        headers=auth,
        json={"query": "", "k": 5, "session_id": None},
    )
    assert r.status_code == 200, r.text
    ctx = r.json()
    # Empty composition: no layer populated.
    assert ctx["l0_recent"] == []
    assert ctx["l1_session"] == []
    assert ctx["l2_topic"] == []
    assert ctx["l3_semantic"] == []
    # First call = cold.
    assert ctx["meta"]["hot"] is False

    # Second warmup call = hot.
    r2 = await client.post(
        "/palace/warm/wake-up?warmup=true",
        headers=auth,
        json={"query": "", "k": 5, "session_id": None},
    )
    assert r2.status_code == 200
    assert r2.json()["meta"]["hot"] is True


@pytest.mark.slow
async def test_wakeup_composition_within_latency_budget(client, auth) -> None:
    """Spec §4.2: p95 wake-up total_ms < 500 ms hot on modest palaces.

    10 measured wake-ups after warmup, sorted, pick p95. Marked slow so
    CI runs can skip when the box is noisy — enforcement lives in the
    sum-of-parts test.
    """
    user = "perf"
    async def seed_one(i: int):
        return await client.post(
            f"/palace/{user}/ingest",
            headers=auth,
            json={
                "turn_id": f"{user}{i:040d}",
                "session_id": "s1",
                "pair_id": f"{user}p{i:040d}",
                "role": "user" if i % 2 == 0 else "assistant",
                "text": f"turn {i} — conversational content",
                "ts": 1_700_000_000.0 + i,
            },
        )

    for i in range(100):
        await seed_one(i)

    # warmup
    await client.post(
        f"/palace/{user}/wake-up",
        headers=auth,
        json={"query": "content", "k": 5, "session_id": "s1"},
    )

    times_ms: list[float] = []
    for _ in range(10):
        r = await client.post(
            f"/palace/{user}/wake-up",
            headers=auth,
            json={"query": "content", "k": 5, "session_id": "s1"},
        )
        assert r.status_code == 200
        times_ms.append(r.json()["meta"]["total_ms"])

    times_ms.sort()
    p95 = times_ms[int(len(times_ms) * 0.95)]
    # Generous budget — CI/thread-pool variance. Spec §4.2 has the
    # hard 500 ms number; we log p95 and only fail at 2× budget so
    # the test stays green on noisy CI while still catching real
    # regressions (e.g. the sidecar swapping back to serial).
    print(f"[wakeup-p95] p95={p95:.1f}ms budget=500ms", file=sys.stderr)
    assert p95 < 1000, f"p95={p95:.1f}ms exceeds 2× hot budget (500ms)"

"""Integration tests for M0.11 — metrics + /readyz (spec §8.1 + §8.4).

Nine tests. Every one drives the real app (not a mock) so route
instrumentation, middleware wiring, lifespan state, and registry
mutation all stay in scope for each assertion.

- ``test_metrics_endpoint_returns_openmetrics`` — ``GET /metrics``
  returns 200 with Prometheus-compatible ``Content-Type`` and at
  least one expected sentinel metric (``mempalace_readyz``).
  Nastiest context: ``PROMETHEUS_MULTIPROC_DIR`` unset (unit-test
  environment); the fallback path in ``render_metrics`` must
  succeed without the env var.

- ``test_ingest_increments_accepted_on_success`` — successful
  ingest bumps ``mempalace_ingest_total{status="accepted"}``.
  Nastiest context: label contract — only ``accepted`` bumps, not
  ``{status=""}`` or a default label.

- ``test_ingest_increments_duplicate_on_second_call`` — replay of
  the same turn idempotency-key bumps
  ``{status="duplicate"}``, not ``accepted`` a second time.

- ``test_ingest_increments_error_on_raise`` — monkeypatched ingest
  path raises; ``{status="error"}`` bumps then the exception
  propagates to the client (500). Nastiest context: exception
  path, same failure class as M0.10 middleware try/finally.

- ``test_cache_hit_miss_counters_increment`` — first wake-up is a
  cache miss, second a cache hit. Plus the livesum cache-size
  gauge reflects current ``_cache`` length after each op.
  Nastiest context: asserting the gauge state ties to registry
  mutation — if a future refactor forgets to call
  ``update_cache_gauges`` at a mutation site, this test goes red.

- ``test_wakeup_records_layer_timers`` — ``wakeup_seconds``
  histogram has an observation for each of l0/l1/l2/l3 + total
  after one wake-up. Nastiest context: per-layer assertion, not
  just total. A refactor that wraps only the gather result
  silently produces only ``layer="total"`` observations.

- ``test_search_records_hot_cold_label`` — search emits
  ``hot="0"`` on the cold first call, ``hot="1"`` on a re-search
  against the now-hot palace. Nastiest context: label values —
  catches an inverted boolean.

- ``test_readyz_ok_after_lifespan`` — ``GET /readyz`` returns 200
  with ``ok: true`` after lifespan completes (embedding + registry
  + audit all wired).

- ``test_readyz_not_ok_during_shutdown`` — after
  ``app.state.accepting = False`` fires manually, ``/readyz``
  returns ``ok: false``. Nastiest context: verifies the corrected
  gate check (deviation #1 — plan originally wrote
  ``shutting_down``; M0.10 shipped ``accepting`` + ``_shutdown_done``).

All fail at Stage 1 RED because the ``/metrics`` + ``/readyz``
routes return 501 stubs, the route instrumentation isn't landed,
and ``update_cache_gauges`` isn't called from the registry.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


# -----------------------------------------------------------------
# /metrics endpoint
# -----------------------------------------------------------------


async def test_metrics_endpoint_returns_openmetrics(client) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    ct = r.headers.get("content-type", "")
    # Prometheus scrapes accept ``text/plain; version=0.0.4`` or the
    # OpenMetrics variant. Assert on the prefix to tolerate either.
    assert ct.startswith("text/plain"), f"unexpected content-type: {ct!r}"
    body = r.text
    # Sentinel: the readyz gauge should be present in the rendered
    # scrape output even when its value is 0.
    assert "mempalace_readyz" in body, (
        "rendered metrics did not include mempalace_readyz sentinel"
    )


# -----------------------------------------------------------------
# Ingest counter labels
# -----------------------------------------------------------------


def _counter_value(metric, **labels) -> float:
    """Read a Counter's current value for a given label set without
    triggering the side effect of ``.labels(...).inc(0)``.
    """
    return metric.labels(**labels)._value.get()


def _ingest_payload(
    turn_id: str,
    session_id: str = "s-m011",
    text: str = "hello",
    role: str = "user",
) -> dict:
    """Match the IngestRequest schema shipped at M0.4.1 (schemas.py)."""
    return {
        "turn_id": turn_id,
        "session_id": session_id,
        "pair_id": f"pair-{turn_id}",
        "role": role,
        "text": text,
        "ts": 1_700_000_000.0,
    }


async def test_ingest_increments_accepted_on_success(client, auth) -> None:
    from mempalace_sidecar import metrics

    before = _counter_value(metrics.ingest_total, status="accepted")

    r = await client.post(
        "/palace/alice-m11/ingest",
        headers=auth,
        json=_ingest_payload("018f1bce-m011-accept-aaaa-000000000001"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    after = _counter_value(metrics.ingest_total, status="accepted")
    assert after - before == 1, (
        f"accepted counter delta: expected 1, got {after - before}"
    )


async def test_ingest_increments_duplicate_on_second_call(
    client, auth
) -> None:
    from mempalace_sidecar import metrics

    payload = _ingest_payload("018f1bce-m011-dup-bbbb-000000000002")
    first = await client.post(
        "/palace/alice-m11/ingest", headers=auth, json=payload
    )
    assert first.status_code == 200, first.text

    before = _counter_value(metrics.ingest_total, status="duplicate")
    second = await client.post(
        "/palace/alice-m11/ingest", headers=auth, json=payload
    )
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    after = _counter_value(metrics.ingest_total, status="duplicate")
    assert after - before == 1, (
        f"duplicate counter delta: expected 1, got {after - before}"
    )


async def test_ingest_increments_error_on_raise(
    client, auth, monkeypatch
) -> None:
    from mempalace_sidecar import metrics
    from mempalace_sidecar.palace_handle import PalaceHandle

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated ingest failure")

    monkeypatch.setattr(PalaceHandle, "add_drawer", _boom)

    before = _counter_value(metrics.ingest_total, status="error")

    r = await client.post(
        "/palace/alice-m11-err/ingest",
        headers=auth,
        json=_ingest_payload("018f1bce-m011-err-cccc-000000000003"),
    )
    # FastAPI surfaces the raise as 500. The counter must bump BEFORE
    # the raise reaches the client — wrapper discipline.
    assert r.status_code == 500, r.text
    after = _counter_value(metrics.ingest_total, status="error")
    assert after - before == 1, (
        f"error counter delta: expected 1, got {after - before}"
    )


# -----------------------------------------------------------------
# Cache-hit / miss counters + livesum gauges
# -----------------------------------------------------------------


async def test_cache_hit_miss_counters_increment(client, auth, app) -> None:
    from mempalace_sidecar import metrics

    miss_before = metrics.palace_cache_misses_total._value.get()
    hit_before = metrics.palace_cache_hits_total._value.get()

    # First wake-up on "bob" is a cache miss (cold open).
    r1 = await client.post(
        "/palace/bob/wake-up",
        headers=auth,
        json={"query": "hello"},
    )
    assert r1.status_code == 200

    miss_mid = metrics.palace_cache_misses_total._value.get()
    assert miss_mid - miss_before == 1, (
        f"miss delta after cold open: expected 1, got "
        f"{miss_mid - miss_before}"
    )
    # Livesum gauge reflects current cache size after mutation.
    assert metrics.palace_cache_size._value.get() == len(
        app.state.registry._cache
    ), "palace_cache_size gauge out of sync with registry._cache length"

    # Second wake-up on "bob" is a cache hit (warm).
    r2 = await client.post(
        "/palace/bob/wake-up",
        headers=auth,
        json={"query": "hello again"},
    )
    assert r2.status_code == 200

    hit_after = metrics.palace_cache_hits_total._value.get()
    assert hit_after - hit_before == 1, (
        f"hit delta after warm open: expected 1, got "
        f"{hit_after - hit_before}"
    )


# -----------------------------------------------------------------
# Wake-up per-layer timers
# -----------------------------------------------------------------


async def test_wakeup_records_layer_timers(client, auth) -> None:
    from mempalace_sidecar import metrics

    def _count(layer: str, hot: str) -> float:
        return (
            metrics.wakeup_seconds.labels(layer=layer, hot=hot)._sum.get()
        )

    # Sum is 0.0 before any observation lands on this label pair.
    before = {
        layer: _count(layer, "0") for layer in ("l0", "l1", "l2", "l3", "total")
    }

    r = await client.post(
        "/palace/carol/wake-up",
        headers=auth,
        json={"query": "hello"},
    )
    assert r.status_code == 200

    after = {
        layer: _count(layer, "0") for layer in ("l0", "l1", "l2", "l3", "total")
    }
    for layer in ("l0", "l1", "l2", "l3", "total"):
        assert after[layer] > before[layer], (
            f"wakeup_seconds sum did not increase for layer={layer} "
            f"(before={before[layer]}, after={after[layer]}) — "
            "instrumentation missing?"
        )


# -----------------------------------------------------------------
# Search hot/cold labels
# -----------------------------------------------------------------


async def test_search_records_hot_cold_label(client, auth) -> None:
    from mempalace_sidecar import metrics

    cold_before = metrics.search_seconds.labels(hot="0")._sum.get()
    hot_before = metrics.search_seconds.labels(hot="1")._sum.get()

    # Ingest a drawer so search returns something.
    await client.post(
        "/palace/dana/ingest",
        headers=auth,
        json={
            "turn_id": "t-s-1",
            "session_id": "s-d",
            "user_msg": "the quick brown fox",
            "assistant_msg": "lazy dog",
            "mined_at_ms": 1_700_000_000_000,
        },
    )

    # First search is "cold" (the palace handle was resident from
    # ingest, but the ``was_cold`` metric label tracks per-call
    # open-state tracking; ingest does not count as a search-side
    # warm-up — this is exercised by the instrumentation in
    # routes/palace.py::search).
    #
    # To force a cold search, close the palace handle between ingest
    # and search by evicting the cache.
    # Use a fresh user "dana_search" to make cold explicit.
    r1 = await client.post(
        "/palace/dana_search/search",
        headers=auth,
        json={"query": "fox"},
    )
    assert r1.status_code == 200

    # Second search against the same user is hot.
    r2 = await client.post(
        "/palace/dana_search/search",
        headers=auth,
        json={"query": "fox again"},
    )
    assert r2.status_code == 200

    cold_after = metrics.search_seconds.labels(hot="0")._sum.get()
    hot_after = metrics.search_seconds.labels(hot="1")._sum.get()

    assert cold_after > cold_before, (
        "search_seconds{hot=0} did not record cold search"
    )
    assert hot_after > hot_before, (
        "search_seconds{hot=1} did not record hot search"
    )


# -----------------------------------------------------------------
# /readyz
# -----------------------------------------------------------------


async def test_readyz_ok_after_lifespan(client) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, f"readyz not ok: {body}"
    assert body["embedding_loaded"] is True
    assert body["db_ok"] is True


async def test_readyz_not_ok_during_shutdown(client, app) -> None:
    # Simulate the M0.10 shutdown gate flipping without actually
    # running shutdown_sequence — avoids closing the registry
    # mid-test which would break subsequent tests running against
    # the same app lifecycle.
    app.state.accepting = False
    try:
        r = await client.get("/readyz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False, (
            f"readyz reported ok=True despite accepting=False: {body}"
        )
    finally:
        app.state.accepting = True

"""Concurrent cold-palace-open regression gate + measurement harness.

From M0.3.1-5 sign-off (2026-04-20, deltas): ``PalaceRegistry._construct_entry``
runs inside ``_registry_lock``, serialising all cold palace opens
across the sidecar. Per-open cost is 50-200ms; N concurrent
first-touch opens queue behind each other. Original measurement at
N=10 was 0.5-2s total.

**This test is both a regression gate AND a measurement harness**
(M0.11 brief-review catch #2 — originally framed as measurement-
only, resolved to: keep in ``tests/``, make it a real gate, log the
p95 measurement for deltas ingestion):

  - **Gate.** Total wall-clock at N=8 concurrent cold opens < 3.0s.
    M0.3 baseline was ~1s at N=10 on the same hardware; 3.0s
    catches a ~3× regression without flapping on CI jitter.
  - **Measurement.** Per-open p95 is logged + emitted via pytest's
    stdout capture, in a form the M0.11 deltas entry ingests.

Nastiest reachable context: ``_registry_lock`` contention is the
specific regression surface. A future refactor that moves
``_construct_entry`` outside the lock drops the p95 dramatically
AND passes the gate — that's a good outcome, not a regression.
What this test catches: a regression *into* deeper serialization
(e.g., adding another global lock above ``_construct_entry``,
which would push N=8 past the 3s gate).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


async def test_concurrent_cold_opens_within_budget(
    tmp_path: Path, capsys
) -> None:
    from mempalace_sidecar.registry import PalaceRegistry

    registry = PalaceRegistry(
        data_dir=tmp_path, max_bytes=2**31, idle_ttl_sec=3600
    )
    try:
        user_ids = [f"bench-{i}" for i in range(8)]
        per_open_latencies: list[float] = []

        async def _open_and_time(uid: str) -> None:
            t0 = time.perf_counter()
            await registry.open(uid)
            per_open_latencies.append(time.perf_counter() - t0)

        t_total_start = time.perf_counter()
        await asyncio.gather(*[_open_and_time(u) for u in user_ids])
        total = time.perf_counter() - t_total_start

        # Sort ascending for p95.
        per_open_latencies.sort()
        p95_idx = max(0, int(round(len(per_open_latencies) * 0.95)) - 1)
        p95 = per_open_latencies[p95_idx]

        # Emit the measurement in a grep-friendly form; M0.11 deltas
        # entry captures this number at Stage 4.
        print(
            f"\n[bench] concurrent_cold_opens N=8 "
            f"total={total:.3f}s "
            f"p95_per_open={p95:.3f}s "
            f"mean_per_open={statistics.mean(per_open_latencies):.3f}s",
        )

        # Regression gate.
        assert total < 3.0, (
            f"concurrent cold opens at N=8 took {total:.3f}s "
            f"(regression gate: must be < 3.0s). "
            f"Per-open p95 was {p95:.3f}s."
        )
    finally:
        await registry.close_all()

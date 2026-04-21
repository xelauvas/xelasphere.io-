"""Concurrency-scaling micro-benchmark for the chromadb singleton patch.

The singleton ONNX ``InferenceSession`` is shared across all concurrent
wake-ups in the sidecar process. ``InferenceSession.run()`` is
thread-safe per ONNX docs, but the actual parallelism depends on
``intra_op_num_threads`` configuration and whether the GIL is released
during inference.

This benchmark asserts that four concurrent embedding calls against
the singleton EF do NOT climb proportionally with concurrency. If
they do (4× single-call cost), ONNX session contention is the
bottleneck — remediation is ``intra_op_num_threads=1`` in the
patched constructor or per-thread session caching.

Not a regression test in the usual sense — it's a one-time
architectural validation that lives in the suite so any future
refactor that breaks concurrent scaling fails loudly.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

import pytest

pytestmark = pytest.mark.integration


async def test_singleton_onnx_parallelizes_across_threads() -> None:
    from chromadb.api.types import DefaultEmbeddingFunction

    ef = DefaultEmbeddingFunction()

    # Warm the singleton (first call pays lazy-init).
    for _ in range(3):
        ef(["warmup"])

    # Single-call baseline — 20 sequential warm embeddings via
    # asyncio.to_thread (matches build_wakeup's call pattern).
    single_ms: list[float] = []
    for i in range(20):
        t0 = time.monotonic()
        await asyncio.to_thread(ef, [f"single query {i}"])
        single_ms.append((time.monotonic() - t0) * 1000)
    single_p95 = sorted(single_ms)[int(len(single_ms) * 0.95)]

    # 4-concurrent — asyncio.gather dispatches four to_thread futures
    # against the default thread pool. Each call's wall time is
    # measured independently; we take the p95 across all 20 calls
    # (5 batches of 4).
    concurrent_ms: list[float] = []

    async def timed_call(tag: str) -> float:
        t0 = time.monotonic()
        await asyncio.to_thread(ef, [tag])
        return (time.monotonic() - t0) * 1000

    for batch in range(5):
        durations = await asyncio.gather(*[
            timed_call(f"concurrent {batch}:{i}") for i in range(4)
        ])
        concurrent_ms.extend(durations)
    concurrent_p95 = sorted(concurrent_ms)[int(len(concurrent_ms) * 0.95)]

    print(
        f"[onnx-concurrency] single p95={single_p95:.1f}ms "
        f"4-concurrent p95={concurrent_p95:.1f}ms "
        f"ratio={concurrent_p95 / single_p95:.2f}",
        file=sys.stderr,
    )

    # Ceiling is 3× because:
    #   * Perfect parallelism on a 4-core VPS → ratio ~1.0
    #   * No parallelism (GIL-held or session-serialised) → ratio ~4.0
    #   * 3.0 catches "essentially no parallelism" while tolerating
    #     VPS variance, background noise, and the fact that with
    #     intra_op_num_threads=0 (chroma's default) ONNX may spawn
    #     its own worker pool that overlaps with ours.
    # If this asserts, the fix is per-thread session caching in
    # _chromadb_patch.py (see deltas entry B for context).
    assert concurrent_p95 < 3.0 * single_p95, (
        f"4-concurrent embedding p95 ({concurrent_p95:.1f}ms) is more "
        f"than 3× single-call p95 ({single_p95:.1f}ms). ONNX session "
        f"contention under load. Remediation: set "
        f"intra_op_num_threads=1 in the patched constructor, or "
        f"switch to per-thread session caching in "
        f"mempalace_sidecar/_chromadb_patch.py."
    )

    # Secondary assertion: single-call p95 floor. Regression signal —
    # if the singleton patch were broken (reverted to per-call
    # construction), p95 would balloon to ~250-400ms (cold lazy-init
    # dominates). 150ms is a generous ceiling that catches that
    # failure mode while tolerating the intra_op=1 tradeoff (~85ms
    # single-call p95 is expected on AMD EPYC-Rome; see deltas entry B).
    assert single_p95 < 150, (
        f"Single embedding p95 is {single_p95:.1f}ms — expected <150ms "
        f"with the singleton patch and intra_op_num_threads=1 active. "
        f"If p95 > 200ms: the patch has been reverted or the singleton "
        f"is re-instantiating. See tests/unit/test_chromadb_patch.py."
    )

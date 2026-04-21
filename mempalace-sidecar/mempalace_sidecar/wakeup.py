"""Wake-up context composition — the HOT READ path (M0.5.2).

Every user turn hits this before the LLM call. Correctness here defines
whether "concurrent wake-up" is real or a lie.

Spec §4.2 (read path A: pre-model wake-up): the route assembles four
context layers — L0 (recent turns), L1 (session turns), L2 (topic
search), L3 (semantic search) — and returns them together as a
``WakeupContext``. Each layer is backed by a synchronous method on
``PalaceHandle`` (``recent_drawers``, ``session_drawers``,
``topic_search``, ``semantic_search``) which touches sqlite / Chroma.

Concurrency contract (spec §5.3 "Concurrency model inside PalaceHandle"):

    Sync functions inside ``asyncio.gather`` run SEQUENTIALLY on the
    event loop. The only way to get real parallelism across sync
    bodies is to wrap each call in ``asyncio.to_thread`` and gather
    the thread-pool futures.

    WRONG:   asyncio.gather(_l0(), _l1(), _l2(), _l3())
    WRONG:   [await asyncio.to_thread(f) for f in ...]    # serial for-loop
    RIGHT:   asyncio.gather(asyncio.to_thread(_l0), asyncio.to_thread(_l1), ...)

Enforced by:

* Unit test ``test_build_wakeup_runs_layers_concurrently_via_to_thread``
  (time.sleep-based mocks; serial path > 0.5s, concurrent path < 0.5s).
* Integration test ``test_wakeup_concurrent_faster_than_sum_of_parts``
  (real Chroma, 300-drawer seed; 0.7x serial-bound threshold).

DO NOT change the concurrency shape without updating both tests — the
0.7x coefficient in particular is load-bearing, not arbitrary (see
the test docstring for the math).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from mempalace_sidecar.schemas import Drawer, WakeupContext, WakeupMeta


def to_drawer(raw: Any) -> Drawer:
    """Normalise the two handle-return shapes into ``schemas.Drawer``.

    ``PalaceHandle.recent_drawers`` and ``session_drawers`` return the
    Chroma ``get()`` shape::

        {"text": str, "metadata": {"ts": float, "wing": str, "room": str,
                                   "pair_id": str, "filed_at": str, ...}}

    ``PalaceHandle.semantic_search`` and ``topic_search`` return the
    library's searcher shape::

        {"text": str, "wing": str, "room": str, "similarity": float,
         "ts": float, "pair_id": str, ...}  # flat, NO nested metadata

    The converter must handle both without crashing — missing keys
    resolve via the nested ``metadata`` dict or fall back to sensible
    defaults (wing/room None, ts 0.0, score None).

    A raw object-shaped fallback is kept for defensive robustness in
    case a library refactor ever returns dataclasses; M0.5's tests
    only exercise the dict path.
    """
    if isinstance(raw, dict):
        meta = raw.get("metadata") or {}
        ts_val = raw.get("ts", meta.get("ts", 0.0))
        try:
            ts = float(ts_val) if ts_val is not None else 0.0
        except (TypeError, ValueError):
            ts = 0.0
        return Drawer(
            text=raw.get("text", ""),
            wing=raw.get("wing") or meta.get("wing"),
            room=raw.get("room") or meta.get("room"),
            ts=ts,
            pair_id=raw.get("pair_id") or meta.get("pair_id"),
            score=raw.get("similarity") or raw.get("score"),
        )

    # Defensive object-shaped fallback. Library never currently returns
    # dataclasses here; path exists so a future refactor doesn't crash
    # the HOT wake-up path with an AttributeError.
    ts_val = getattr(raw, "ts", 0.0)
    try:
        ts = float(ts_val) if ts_val is not None else 0.0
    except (TypeError, ValueError):
        ts = 0.0
    return Drawer(
        text=getattr(raw, "text", ""),
        wing=getattr(raw, "wing", None),
        room=getattr(raw, "room", None),
        ts=ts,
        pair_id=getattr(raw, "pair_id", None),
        score=getattr(raw, "similarity", None) or getattr(raw, "score", None),
    )


async def build_wakeup(
    *,
    user_id: str,
    query: str,
    handle: Any,
    session_id: Optional[str],
    hot: bool = True,
    k: int = 5,
) -> WakeupContext:
    """Compose L0/L1/L2/L3 concurrently via ``asyncio.to_thread``.

    Short-circuit rules:
      * L1 skipped when ``session_id is None`` — saves a sqlite SELECT
        that would always return empty.
      * L2/L3 skipped when ``query`` is empty — avoids embedding the
        empty string through Chroma's default EF.
      * L0 runs unconditionally (query-independent).

    Returns a ``WakeupContext`` with ``meta.total_ms`` measured from
    the start of this function. ``meta.embed_ms`` is always 0.0 —
    Chroma handles embedding internally and doesn't surface a timing;
    retained in the schema for stability and for M0.17 shadow-sink
    adapter back-compat.

    The caller MUST already hold the per-user reader_lock. This
    function does NOT acquire it — the route layer is the one that
    knows whether this is a read or a write (spec §5.3).
    """
    t0 = time.monotonic()

    # Define each layer as a plain sync callable; asyncio.to_thread
    # dispatches them to the default thread pool. Keeping each callable
    # as its own local function (NOT a lambda) makes stack traces
    # readable under failure and lets the unit-test mocks introspect
    # call-count cleanly.
    def _l0() -> list[dict]:
        return handle.recent_drawers(limit=k) or []

    def _l1() -> list[dict]:
        if session_id is None:
            return []
        return handle.session_drawers(session_id, limit=k) or []

    def _l2() -> list[dict]:
        if not query:
            return []
        return handle.topic_search(query, k=k) or []

    def _l3() -> list[dict]:
        if not query:
            return []
        return handle.semantic_search(query, k=k) or []

    # Concurrency contract (see module docstring). Each to_thread returns
    # a coroutine that delegates to loop.run_in_executor; gather drives
    # all four in parallel across the default thread pool. Replacing
    # this with plain asyncio.gather(_l0(), _l1(), ...) destroys
    # parallelism — enforced by test_build_wakeup_runs_layers_concurrently.
    l0r, l1r, l2r, l3r = await asyncio.gather(
        asyncio.to_thread(_l0),
        asyncio.to_thread(_l1),
        asyncio.to_thread(_l2),
        asyncio.to_thread(_l3),
    )

    total_ms = (time.monotonic() - t0) * 1000.0
    return WakeupContext(
        user_id=user_id,
        l0_recent=[to_drawer(r) for r in l0r],
        l1_session=[to_drawer(r) for r in l1r],
        l2_topic=[to_drawer(r) for r in l2r],
        l3_semantic=[to_drawer(r) for r in l3r],
        meta=WakeupMeta(hot=hot, embed_ms=0.0, total_ms=total_ms),
    )


__all__ = ["build_wakeup", "to_drawer"]

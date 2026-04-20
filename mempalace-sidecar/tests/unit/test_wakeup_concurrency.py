"""Unit tests for build_wakeup composition (M0.5.2).

These use MagicMock handles so they exercise the composition layer
without touching Chroma / sqlite. Two invariants:

* All four layers (L0, L1, L2, L3) populate a WakeupContext when
  given a non-empty query and a session_id.
* Empty query short-circuits L2 + L3 — we don't waste a Chroma query
  on the empty string.

The teeth-bearing concurrency assertion (wall-clock parallelism) lives
in the integration suite, where we can measure against real Chroma.
See ``tests/integration/test_wakeup_layers.py::test_wakeup_concurrent_faster_than_sum_of_parts``.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock


async def test_build_wakeup_populates_all_layers() -> None:
    from mempalace_sidecar.wakeup import build_wakeup

    handle = MagicMock()
    handle.recent_drawers.return_value = [
        {"text": "r", "metadata": {"ts": 1.0, "wing": "w0", "room": "r0"}}
    ]
    handle.session_drawers.return_value = [
        {"text": "s", "metadata": {"ts": 2.0, "wing": "w1", "room": "r1"}}
    ]
    handle.topic_search.return_value = [
        {"text": "t", "wing": "w2", "room": "r2", "similarity": 0.7}
    ]
    handle.semantic_search.return_value = [
        {"text": "d", "wing": "w3", "room": "r3", "similarity": 0.9}
    ]

    ctx = await build_wakeup(
        user_id="u",
        query="hi",
        handle=handle,
        session_id="s1",
        hot=True,
        k=5,
    )

    assert ctx.user_id == "u"
    assert len(ctx.l0_recent) == 1 and ctx.l0_recent[0].text == "r"
    assert len(ctx.l1_session) == 1 and ctx.l1_session[0].text == "s"
    assert len(ctx.l2_topic) == 1 and ctx.l2_topic[0].text == "t"
    assert len(ctx.l3_semantic) == 1 and ctx.l3_semantic[0].text == "d"
    assert ctx.meta.hot is True
    # total_ms comes from time.monotonic() inside build_wakeup — can be
    # zero on very fast mocks; just verify the field is a real float.
    assert isinstance(ctx.meta.total_ms, float)


async def test_build_wakeup_empty_query_skips_search_layers() -> None:
    """L2 (topic) and L3 (semantic) are query-driven. An empty query
    must NOT issue a Chroma call — the library would embed the empty
    string and waste a model invocation. L0 and L1 still run (they're
    query-independent).
    """
    from mempalace_sidecar.wakeup import build_wakeup

    handle = MagicMock()
    handle.recent_drawers.return_value = []
    handle.session_drawers.return_value = []
    handle.topic_search.return_value = [{"text": "should-not-be-called"}]
    handle.semantic_search.return_value = [{"text": "should-not-be-called"}]

    ctx = await build_wakeup(
        user_id="u",
        query="",
        handle=handle,
        session_id="s",
        hot=True,
        k=5,
    )

    handle.topic_search.assert_not_called()
    handle.semantic_search.assert_not_called()
    assert ctx.l2_topic == []
    assert ctx.l3_semantic == []


async def test_build_wakeup_no_session_id_skips_l1() -> None:
    """L1 needs a session_id. None short-circuits to empty — handle
    method isn't called (spares a sqlite SELECT that would return
    nothing)."""
    from mempalace_sidecar.wakeup import build_wakeup

    handle = MagicMock()
    handle.recent_drawers.return_value = []
    handle.session_drawers.return_value = [{"text": "should-not-be-called"}]
    handle.topic_search.return_value = []
    handle.semantic_search.return_value = []

    ctx = await build_wakeup(
        user_id="u",
        query="query",
        handle=handle,
        session_id=None,
        hot=True,
        k=5,
    )

    handle.session_drawers.assert_not_called()
    assert ctx.l1_session == []


async def test_build_wakeup_runs_layers_concurrently_via_to_thread() -> None:
    """Unit-level proxy for the concurrency contract.

    Each layer method sleeps ``d`` seconds via ``time.sleep`` (blocking,
    sync). If build_wakeup invokes them serially on the event loop, the
    total wall-clock time is ~4*d. If it wraps each call in
    ``asyncio.to_thread``, they run on separate thread-pool workers and
    total is ~d (plus thread-scheduling overhead).

    We pick d = 0.2s and assert total < 0.5s (serial would be >= 0.8s).

    This is the unit-test equivalent of the integration 0.7× coefficient
    test — mechanical enforcement of the ``asyncio.to_thread`` wrapping.
    """
    from mempalace_sidecar.wakeup import build_wakeup

    delay = 0.2

    def slow_recent(*, limit: int):
        time.sleep(delay)
        return [{"text": "r0", "metadata": {"ts": 1.0}}]

    def slow_session(session_id, *, limit: int):
        time.sleep(delay)
        return [{"text": "s0", "metadata": {"ts": 2.0}}]

    def slow_topic(query, *, k: int):
        time.sleep(delay)
        return [{"text": "t0", "similarity": 0.5}]

    def slow_semantic(query, *, k: int):
        time.sleep(delay)
        return [{"text": "d0", "similarity": 0.9}]

    handle = MagicMock()
    handle.recent_drawers.side_effect = slow_recent
    handle.session_drawers.side_effect = slow_session
    handle.topic_search.side_effect = slow_topic
    handle.semantic_search.side_effect = slow_semantic

    t0 = time.monotonic()
    ctx = await build_wakeup(
        user_id="u",
        query="q",
        handle=handle,
        session_id="s",
        hot=True,
        k=5,
    )
    elapsed = time.monotonic() - t0

    # Serial bound is 4 * delay = 0.8s. Concurrent should be near delay.
    # 0.5s is a generous ceiling that catches serial execution (~0.8s+)
    # while tolerating realistic thread-pool startup on modest hardware.
    assert elapsed < 0.5, (
        f"build_wakeup took {elapsed:.2f}s ({4 * delay:.2f}s serial bound) — "
        f"layers are NOT running concurrently. Ensure each handle call is "
        f"wrapped in asyncio.to_thread."
    )
    # Sanity: all four layers populated (not lost to concurrency bugs).
    assert len(ctx.l0_recent) == 1
    assert len(ctx.l1_session) == 1
    assert len(ctx.l2_topic) == 1
    assert len(ctx.l3_semantic) == 1


async def test_to_drawer_handles_recent_shape() -> None:
    """``handle.recent_drawers`` returns
    ``{"text": str, "metadata": {"ts", "wing", "room", "pair_id", ...}}``.
    The converter must extract fields from ``metadata`` nested dict."""
    from mempalace_sidecar.wakeup import _to_drawer

    raw = {
        "text": "hello",
        "metadata": {"ts": 12.0, "wing": "W", "room": "R", "pair_id": "P"},
    }
    d = _to_drawer(raw)
    assert d.text == "hello"
    assert d.wing == "W"
    assert d.room == "R"
    assert d.ts == 12.0
    assert d.pair_id == "P"


async def test_to_drawer_handles_searcher_shape() -> None:
    """``handle.semantic_search`` / ``topic_search`` return library
    searcher shape: ``{"text", "wing", "room", "similarity", ...}`` —
    flat, NO ``metadata`` nesting. The converter must handle both."""
    from mempalace_sidecar.wakeup import _to_drawer

    raw = {
        "text": "world",
        "wing": "W2",
        "room": "R2",
        "similarity": 0.42,
    }
    d = _to_drawer(raw)
    assert d.text == "world"
    assert d.wing == "W2"
    assert d.room == "R2"
    assert d.score == 0.42

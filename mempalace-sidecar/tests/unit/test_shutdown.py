"""Unit tests for M0.10 graceful shutdown (spec §5.7).

Two tests — both teeth-bearing, per doctrine commit c529ccd
("plan sketches require teeth-bearing verification"):

- ``test_in_flight_counter_enter_exit_balances`` — pure-logic
  exercise of ``InFlightCounter``. Enter/exit/peak/idle-signal
  behavior without the middleware or any asyncio.create_task
  scaffolding. Catches arithmetic and event-signal bugs at the
  datastructure layer before the middleware layers hide them.

- ``test_middleware_counter_sequence_under_exception`` — the
  load-bearing test for the try/finally discipline in the
  middleware. An inner handler observes the mid-call count then
  raises. Asserts the sequence is 0 → 1 (mid-call, observed by
  inner handler) → 0 (post-exception, via try/finally decrement).
  Stage 1 RED ships the middleware as HALF-implementation
  (increment-only, no try/finally) so this test FAILS with
  "counter ended at 1, not 0". Stage 3 GREEN adds the try/finally
  wrapper and the test passes.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from mempalace_sidecar.shutdown import (
    InFlightCounter,
    InFlightCounterMiddleware,
)


async def test_in_flight_counter_enter_exit_balances() -> None:
    """Pure-logic check: enter bumps count + clears idle; exit
    drains + re-sets idle when it hits zero. ``wait_idle`` returns
    immediately when the counter is already at zero.
    """
    c = InFlightCounter()

    # Baseline: idle set, count zero, wait_idle returns immediately.
    assert c.count == 0
    assert await c.wait_idle(timeout=0.01) is True

    c.enter()
    assert c.count == 1
    # wait_idle with a short timeout must return False while the
    # counter is positive.
    assert await c.wait_idle(timeout=0.05) is False

    c.enter()
    assert c.count == 2

    c.exit()
    assert c.count == 1
    assert await c.wait_idle(timeout=0.05) is False

    c.exit()
    assert c.count == 0
    # Once the counter returns to zero, wait_idle must succeed
    # without actually waiting.
    assert await c.wait_idle(timeout=0.01) is True


async def test_middleware_counter_sequence_under_exception() -> None:
    """Stage 1 RED-failing test.

    The middleware's contract is that the counter goes 0 → 1 →
    0 across a request, *even when the inner handler raises*. That
    requires wrapping ``call_next`` in ``try/finally``.

    This test instruments an inner handler that records the counter
    value mid-call (sequence element #2) and then raises. It then
    asserts the post-exception count is zero (sequence element #3).
    The first assertion (observed == [1]) passes in Stage 1 because
    the half-implementation DOES increment. The second assertion
    (post-exception count == 0) fails in Stage 1 because the
    half-implementation omits the decrement.
    """
    counter = InFlightCounter()
    observed: list[int] = []

    class _Boom(Exception):
        pass

    async def inner(_request):
        # Counter was bumped by the middleware before this handler
        # ran, so the mid-call snapshot must be 1.
        observed.append(counter.count)
        raise _Boom("inner-handler-raised")

    middleware = InFlightCounterMiddleware(inner, counter=counter)

    # Minimal ASGI scope for a non-exempt path. The middleware
    # doesn't actually touch the body or headers — it just inspects
    # ``request.url.path`` — so the thinnest possible scope works.
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/palace/u/ingest",
        "headers": [],
        "query_string": b"",
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req = Request(scope, receive=_receive)

    # Pre-call baseline: count is 0.
    assert counter.count == 0

    with pytest.raises(_Boom):
        await middleware.dispatch(req, inner)

    # Sequence element #2 — mid-call count observed by inner handler.
    assert observed == [1], (
        f"mid-call observed count should be [1], got {observed}"
    )
    # Sequence element #3 — post-exception count. FAILS in Stage 1
    # RED because the middleware has no try/finally decrement.
    assert counter.count == 0, (
        "post-exception counter should be 0; try/finally decrement "
        f"missing (actual={counter.count})"
    )

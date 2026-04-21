"""Graceful shutdown protocol (M0.10, spec §5.7).

Coordinates sidecar shutdown so in-flight HTTP requests drain, the
KG worker finishes its current tick, and every resident Palace
handle flushes before systemd observes ``STOPPING=1``.

Six phases — each emitted as a structured log record so journald
can surface where a slow or hung shutdown stalled:

    1. ``shutdown_gate`` — stop accepting new work. The middleware
       flips ``app.state.accepting`` to ``False``; subsequent
       requests short-circuit with 503.
    2. ``shutdown_worker_cancel`` — cancel + await the KG worker
       task (10s budget via ``asyncio.wait_for``).
    3. ``shutdown_drain`` — block on ``InFlightCounter.wait_idle``
       for ``drain_timeout_sec``. Stragglers are left to their
       natural asyncio cancellation path once the event loop
       exits.
    4. ``shutdown_registry_close`` — ``registry.close_all`` so each
       resident palace two-phase-evicts (spec §5.4). Exceptions are
       logged + swallowed — the outer sequence still proceeds to
       the STOPPING notify.
    5. ``shutdown_systemd_notify`` — emit ``STOPPING=1`` so systemd
       records clean shutdown instead of a failure.
    6. ``shutdown_done`` — terminal marker; event-loop can exit.

**Coroutine-safe, NOT thread-safe.** ``InFlightCounter`` mutates
``_count`` between ``enter``/``exit`` without awaiting, so two
concurrent coroutines can't interleave on the arithmetic. That is
the entire reason the sidecar's request path is pure-asyncio. Do
NOT reuse this counter from background OS threads — the
increment/decrement pair becomes a read-modify-write race.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


log = logging.getLogger(__name__)


EXEMPT_PATHS = ("/healthz", "/readyz", "/metrics")


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS)


class InFlightCounter:
    """Tracks the number of HTTP handlers between request-accepted
    and response-sent.

    ``wait_idle`` uses an ``asyncio.Event`` so drain wakes the
    instant the last request finishes instead of polling. See
    module docstring on coroutine-safe / not-thread-safe.
    """

    def __init__(self) -> None:
        self._count = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def count(self) -> int:
        return self._count

    def enter(self) -> None:
        self._count += 1
        if self._idle.is_set():
            self._idle.clear()

    def exit(self) -> None:
        # Clamp to 0 so a stray double-exit can't drive the counter
        # negative + trick ``wait_idle`` into returning while real
        # handlers are still running. A negative count would pass
        # the ``== 0`` short-circuit in ``wait_idle``.
        self._count -= 1
        if self._count <= 0:
            self._count = 0
            self._idle.set()

    async def wait_idle(self, timeout: float) -> bool:
        """Block until ``count`` returns to 0 or ``timeout`` elapses.

        Returns True if idle was reached; False on timeout.
        """
        if self._count == 0:
            return True
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


class InFlightCounterMiddleware(BaseHTTPMiddleware):
    """Increments ``InFlightCounter`` for the duration of each
    non-exempt request.

    Exempt paths (``/healthz``, ``/readyz``, ``/metrics``) MUST NOT
    bump the counter — a paused load-balancer healthcheck would
    otherwise keep drain open indefinitely.
    """

    def __init__(self, app, *, counter: InFlightCounter) -> None:
        super().__init__(app)
        self._counter = counter

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_exempt(request.url.path):
            return await call_next(request)
        # Stage 1 RED HALF-IMPLEMENTATION: increment only, no
        # try/finally decrement. Stage 3 GREEN wraps ``call_next``
        # in try/finally so the decrement runs on both the success
        # and exception paths. The unit test
        # ``test_middleware_counter_sequence_under_exception``
        # enforces that discipline by raising inside ``call_next``
        # and asserting the counter ended at 0.
        self._counter.enter()
        return await call_next(request)


async def shutdown_sequence(
    app,
    *,
    drain_timeout_sec: float = 10.0,
) -> None:
    """Run the six-phase shutdown protocol (spec §5.7).

    Stage 1 RED stub — full implementation lands in Stage 3 GREEN.
    """
    raise NotImplementedError("shutdown_sequence lands in M0.10 Stage 3 GREEN")

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
from starlette.responses import JSONResponse, Response


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
        # Clamp to 0 as a best-effort defense against a stray
        # double-exit — keeps the counter non-negative so
        # ``wait_idle``'s ``== 0`` short-circuit isn't fooled.
        #
        # **Limitation.** The clamp does NOT cover every interleaving
        # of a double-exit plus a legitimate fresh ``enter``. If the
        # sequence is ``enter → exit → enter → exit → exit``
        # (pathological second exit racing a real entry), the
        # second ``exit`` fires ``_idle.set()`` while a real handler
        # is still running. Risk is gated on the upstream invariant
        # "each ``enter`` is paired with exactly one ``exit``"
        # (middleware try/finally guarantees this), so the pathological
        # interleave is not reachable under the current middleware.
        # If a future caller site violates the invariant, this clamp
        # is not sufficient — swap for a stricter assertion then.
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
        # Phase-1 gate: once ``shutdown_sequence`` flips
        # ``accepting`` to False, new non-exempt requests get 503
        # *without* bumping the counter — otherwise a barrage of
        # post-gate clients would keep drain open past its budget.
        #
        # ``scope.get("app")`` instead of ``request.app``: the
        # ``Request.app`` property raises ``KeyError`` when ASGI
        # scope has no ``"app"`` entry (minimal unit-test scopes
        # skip it). Stage 3 first-pass used the property directly
        # and the exception-sequence unit test caught it — doctrine
        # hit #3.
        scope_app = request.scope.get("app")
        if scope_app is not None and not getattr(
            scope_app.state, "accepting", True
        ):
            return JSONResponse(
                {"detail": "shutting down"}, status_code=503
            )
        self._counter.enter()
        try:
            return await call_next(request)
        finally:
            # try/finally so the decrement runs on both the success
            # and exception paths. The unit test
            # ``test_middleware_counter_sequence_under_exception``
            # enforces this discipline by raising inside ``call_next``
            # and asserting the post-exception counter is zero.
            self._counter.exit()


async def shutdown_sequence(
    app,
    *,
    drain_timeout_sec: float = 10.0,
) -> None:
    """Run the six-phase shutdown protocol (spec §5.7).

    Idempotent. The lifespan's finally block calls this on normal
    exit; integration tests call it explicitly inside the same
    lifespan context. Second (and subsequent) invocations
    short-circuit via ``app.state._shutdown_done`` — re-running
    phase 4 (``registry.close_all``) or phase 5 (systemd notify)
    after the first pass would double-close palace handles and
    re-fire ``STOPPING=1`` to a dead socket.

    Parameters
    ----------
    app : FastAPI
        Reads ``app.state.{in_flight, kg_worker_task, registry,
        accepting, _shutdown_done}``. Missing state members are
        tolerated — each phase guards with ``getattr(..., None)``
        so a partially-built test harness can still exercise the
        surface without needing a full lifespan startup.
    drain_timeout_sec : float
        Maximum time phase 3 blocks waiting for in-flight handlers
        to finish. Systemd's ``TimeoutStopSec`` is 15s (10s drain +
        5s grace); exceeding the systemd budget risks SIGKILL
        mid-drain.
    """
    if getattr(app.state, "_shutdown_done", False):
        return
    # Set the flag BEFORE running phases. If a phase raises, the
    # second invocation still short-circuits — partial cleanup is
    # preferable to a second attempt at already-closed resources.
    app.state._shutdown_done = True

    # Phase 1: gate — stop accepting new work. The middleware reads
    # ``accepting`` on every non-exempt request and returns 503
    # without bumping the counter.
    app.state.accepting = False
    log.info("shutdown_gate: accepting=False")

    # Phase 2: worker cancel — the KG worker holds palace handles
    # via ``registry.open``. Closing the registry first (phase 4)
    # would yank handles out from under an in-flight triple write,
    # so the worker MUST finish or be cancelled here.
    worker_task = getattr(app.state, "kg_worker_task", None)
    log.info(
        "shutdown_worker_cancel: worker_task=%s",
        "absent" if worker_task is None else
        "done" if worker_task.done() else "cancelling",
    )
    if worker_task is not None and not worker_task.done():
        worker_task.cancel()
        try:
            # 10s budget independent of drain_timeout_sec — a stuck
            # extractor swallowing CancelledError shouldn't block
            # the HTTP drain phase.
            await asyncio.wait_for(worker_task, timeout=10.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            log.warning(
                "shutdown_worker_cancel: worker raised on cancel: %r",
                exc,
            )

    # Phase 3: drain — wait for in-flight HTTP handlers. Uses
    # ``wait_idle`` (asyncio.Event) so we wake the instant the last
    # request's try/finally decrement fires, not via polling.
    counter = getattr(app.state, "in_flight", None)
    if counter is not None:
        log.info(
            "shutdown_drain: waiting up to %.1fs for %d in-flight",
            drain_timeout_sec, counter.count,
        )
        idle = await counter.wait_idle(timeout=drain_timeout_sec)
        if not idle:
            log.warning(
                "shutdown_drain: timed out; %d handler(s) still "
                "in flight — leaving to event-loop teardown",
                counter.count,
            )
    else:
        log.info("shutdown_drain: no counter attached")

    # Phase 4: registry close — two-phase evict every resident
    # palace (spec §5.4). Exceptions are logged + swallowed so the
    # STOPPING notify (phase 5) still fires — systemd distinguishes
    # cleanly-stopped vs failed, and that routes oncall differently.
    registry = getattr(app.state, "registry", None)
    log.info(
        "shutdown_registry_close: registry=%s",
        "absent" if registry is None else "present",
    )
    if registry is not None:
        try:
            await registry.close_all()
        except Exception as exc:
            log.warning(
                "shutdown_registry_close: close_all raised: %r",
                exc,
            )

    # Phase 5: systemd notify. Fresh import from main so
    # monkeypatches in tests (``monkeypatch.setattr("...main.
    # _sd_notify_stopping", fake)``) resolve against the live
    # attribute at call time.
    from mempalace_sidecar.main import _sd_notify_stopping

    log.info("shutdown_systemd_notify: STOPPING=1")
    _sd_notify_stopping()

    # Phase 6: done — terminal marker. After this the event loop
    # can exit; the lifespan's generator returns from its yield.
    log.info("shutdown_done: event loop can now exit")

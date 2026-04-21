"""Flask-side async+sync HTTP client for the mempalace-sidecar UDS.

M0.14 Stage 1 RED scaffolding: __init__ and resource-teardown stubs are
defined so imports succeed and the fixture teardown path is clean; every
method tests exercise (post_json, get_json, delete_json, state machine
transitions, _request, _record_success, _record_failure) is deliberately
UNDEFINED. Stage 3 GREEN lands the real behavior.

See `docs/superpowers/plans/2026-04-20-mempalace-integration.md` lines
3353-3572 for the plan sketch. Stage 1 RED deviates from the plan in six
named ways (D1-D6; see M0.14 deltas entry on close), plus D7 for the
MockTransport.last_request / capturing-handler pattern.
"""

from __future__ import annotations

from enum import Enum

import httpx


class CircuitState(str, Enum):
    """Circuit-breaker state for SidecarClient / SyncSidecarClient.

    HALF_OPEN here denotes the RE-PROBE WINDOW, not the classical
    single-probe-gate semantic. Cooldown expiry transitions OPEN →
    HALF_OPEN; in HALF_OPEN the breaker tolerates up to
    breaker_threshold - 1 consecutive failures before re-tripping,
    identical to the CLOSED-state threshold. There is no fast-fail
    during HALF_OPEN.

    Example with threshold=5, 4 consecutive 503s during reprobe:
    state stays HALF_OPEN, _consecutive_failures == 4. 5th failure
    transitions to OPEN. A single success anywhere in the window
    transitions HALF_OPEN → CLOSED and resets the counter.

    On OPEN → HALF_OPEN transition (cooldown expiry detected by the
    `state` property), _consecutive_failures resets to 0 so reprobe
    has a full N-count window. Without this reset, HALF_OPEN would
    inherit the count that tripped OPEN, and a single failure in the
    window would re-trip — which is classical-single-probe semantics,
    not the simplified semantic this client implements.
    """

    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


class ChaosTransport(httpx.AsyncBaseTransport):
    """Test transport wrapping another async transport with failure injection.

    `calls` increments on every invocation BEFORE the failure check so
    that both successful and failed calls count toward fail_every. Tests
    depend on this ordering; see test_chaos_transport_fails_every_n_calls_deterministic.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        fail_every: int = 0,
    ) -> None:
        self._inner = inner
        self._fail_every = fail_every
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self._fail_every and self.calls % self._fail_every == 0:
            raise httpx.ConnectError("chaos: injected failure")
        return await self._inner.handle_async_request(request)


class SidecarClient:
    """Async client for the mempalace-sidecar over UDS.

    Stage 1 RED: __init__ stores config; `aclose` is a no-op stub
    (idempotent per M0.3 PalaceHandle.close convention). Every method
    exercised by tests (`post_json`, `get_json`, `delete_json`,
    `_request`, `state`, `_record_success`, `_record_failure`) is
    DELIBERATELY UNDEFINED. Stage 3 GREEN implements them.
    """

    def __init__(
        self,
        *,
        socket_path: str,
        token: str,
        timeout_s: float = 2.0,
        max_retries: int = 1,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._token = token
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._transport = transport
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0

    async def aclose(self) -> None:
        """No-op stub at Stage 1 RED. Stage 3 GREEN closes the httpx.AsyncClient.

        Idempotent: calling aclose on an already-closed client must not
        raise, matching M0.3 PalaceHandle.close convention. The no-op
        stub is idempotent trivially; Stage 3 GREEN preserves this.
        """
        return None


class SyncSidecarClient:
    """Sync variant of SidecarClient for the flusher thread.

    Plan review fix #3 (see plan sketch): the flusher runs in a thread
    and must NOT call asyncio.run() against a persistent AsyncClient —
    asyncio.run creates a fresh event loop each call, while
    httpx.AsyncClient binds to the loop it was created in. Use this
    sync client from the flusher instead.

    Stage 1 RED: __init__ stores config; `close` is a no-op stub. Every
    method exercised by tests (`post_json`, state-machine methods) is
    DELIBERATELY UNDEFINED. Stage 3 GREEN implements them.

    Per D5: sync client exposes only `post_json`. The flusher is the
    only Flask-side sync caller of the sidecar; it only POSTs. Future
    async callers get their own methods on `SidecarClient` as needed;
    future sync callers get sync-side methods as needed. No `get_json`
    / `delete_json` on `SyncSidecarClient` (YAGNI).
    """

    def __init__(
        self,
        *,
        socket_path: str,
        token: str,
        timeout_s: float = 2.0,
        max_retries: int = 1,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._token = token
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._transport = transport
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0

    def close(self) -> None:
        """No-op stub at Stage 1 RED. Idempotent (same convention as aclose)."""
        return None

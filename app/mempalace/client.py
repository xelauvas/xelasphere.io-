"""Flask-side async+sync HTTP client for the mempalace-sidecar UDS.

See `docs/superpowers/plans/2026-04-20-mempalace-integration.md` lines
3353-3572 for the plan sketch. Stage 3 GREEN implements the plan-sketch
with seven named deviations (D1-D7) documented in the M0.14 deltas
entry. Summary of deviations applied here:

  - D1/M: simplified HALF_OPEN semantic (re-probe window); counter
    resets on OPEN → HALF_OPEN transition.
  - D2/C+E+J: 5xx/transient retry + breaker count; 4xx fast-fail
    (including 429) with no retry and no breaker impact;
    per-outer-call breaker slot accounting (not per-internal-retry);
    malformed-JSON-on-200 treated as transient.
  - D3: socket-permission failure indistinguishable from sidecar-down
    (operator doc in deltas).
  - D4: client fixtures in conftest with guaranteed teardown.
  - D5: SyncSidecarClient exposes post_json only (flusher is the only
    sync caller; YAGNI on get/delete until future sync callers appear).
  - D6: retry budget documented in deltas (max_retries × (timeout +
    backoff capped) = worst-case 6.3s with defaults).
  - D7: CapturingMockTransport in conftest instead of plan-assumed
    transport.last_request attribute.

Concurrency semantics: this client is designed for single-event-loop
async use (future Flask async request paths) and single-flusher-thread
sync use (current only caller). State mutations (_record_success,
_record_failure, state property) are NOT guarded by locks. Single
event-loop and single thread each serialize mutations naturally; the
state property has no await/yield point so it is atomic from the
scheduler's perspective. Two gathered async coroutines hitting the
breaker simultaneously past cooldown expiry would both transition to
HALF_OPEN and both send a reprobe request — a 2x probe load that is
acceptable for current use. If a future caller needs strict
single-probe semantics, add an asyncio.Lock (async) or threading.Lock
(sync) around state mutations at that point.
"""

from __future__ import annotations

import asyncio
import time
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

    Retry contract: `ConnectError`, `TimeoutException`, 5xx
    `HTTPStatusError`, and malformed-JSON-on-2xx are TRANSIENT — retry
    up to max_retries, count toward breaker on final failure. 4xx
    `HTTPStatusError` (including 429) is DETERMINISTIC — no retry, no
    breaker impact, raise immediately.

    Breaker semantics: N consecutive outer-call failures open the
    breaker (N = breaker_threshold). OPEN state fast-fails with
    `RuntimeError("sidecar circuit open")`. After breaker_cooldown_s
    seconds, state transitions to HALF_OPEN (re-probe window);
    _consecutive_failures resets so reprobe tolerates up to N-1
    failures before re-opening. One success in HALF_OPEN transitions
    to CLOSED.
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
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0
        self._closed = False
        if transport is None:
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
        self._httpx_client = httpx.AsyncClient(
            transport=transport,
            base_url="http://sidecar",
            timeout=timeout_s,
        )

    @property
    def state(self) -> CircuitState:
        """Current breaker state, with lazy OPEN → HALF_OPEN transition.

        D1: on OPEN → HALF_OPEN (cooldown elapsed), reset
        _consecutive_failures to 0 so reprobe has a fresh N-count window.
        Without this reset, reprobe inherits the trip-count and a single
        failure re-trips — classical-single-probe semantics, not the
        simplified semantic documented on CircuitState.
        """
        if self._state == CircuitState.OPEN:
            now = time.monotonic()
            if (now - self._opened_at) >= self._breaker_cooldown_s:
                self._state = CircuitState.HALF_OPEN
                self._consecutive_failures = 0
        return self._state

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self) -> None:
        # Not thread-safe: consecutive_failures increment is LOAD/ADD/STORE.
        # Current use is single-flusher-thread (sync) / single-event-loop
        # (async). If multi-thread or multi-loop sharing becomes required,
        # wrap in threading.Lock or asyncio.Lock per use case.
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        if self.state == CircuitState.OPEN:
            raise RuntimeError("sidecar circuit open")
        merged_headers = {"X-Sidecar-Token": self._token, **(headers or {})}
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                # Plan fix #7: exponential backoff capped at 1.0s.
                await asyncio.sleep(min(1.0, 0.1 * (2 ** (attempt - 1))))
            try:
                r = await self._httpx_client.request(
                    method, path, json=body, headers=merged_headers
                )
                r.raise_for_status()
                data = r.json() if r.content else {}
                self._record_success()
                return data
            except (httpx.ConnectError, httpx.TimeoutException):
                # Transient network failure: retry, count toward breaker.
                if attempt == self._max_retries:
                    self._record_failure()
                    raise
            except httpx.HTTPStatusError as e:
                if 500 <= e.response.status_code < 600:
                    # Server error: retry, count toward breaker.
                    if attempt == self._max_retries:
                        self._record_failure()
                        raise
                else:
                    # 4xx deterministic: fast-fail. No retry, no breaker impact.
                    # Covers 401 (auth), 403, 404, 422, 429 (rate limit).
                    # 429 fast-fails by this client's contract: the flusher
                    # has no Retry-After honoring, and hammering a
                    # rate-limited backend compounds the problem. Sidecar
                    # does not currently emit 429; if it does in future,
                    # revisit this decision.
                    raise
            except (ValueError, httpx.DecodingError):
                # Malformed JSON on 2xx response: transient (partial body on
                # unstable connection). Retry, count toward breaker on final.
                # httpx.DecodingError MRO does NOT include ValueError (verified
                # httpx 0.28.1) so both are needed in the tuple.
                if attempt == self._max_retries:
                    self._record_failure()
                    raise
        # Unreachable: loop body either returns, raises, or continues to next
        # iteration. If all iterations continue without raising on the final
        # attempt's _record_failure branch, something is structurally wrong.
        raise RuntimeError("unreachable: _request exhausted retries without raising")

    async def post_json(
        self, path: str, body: dict, headers: dict | None = None
    ) -> dict:
        return await self._request("POST", path, body=body, headers=headers)

    async def get_json(self, path: str, headers: dict | None = None) -> dict:
        # Plan fix #5: real GET, not POST-to-a-GET-route.
        return await self._request("GET", path, body=None, headers=headers)

    async def delete_json(
        self, path: str, body: dict | None = None, headers: dict | None = None
    ) -> dict:
        # Plan fix #6: real DELETE with body, not X-HTTP-Method-Override hack.
        return await self._request("DELETE", path, body=body, headers=headers)

    async def aclose(self) -> None:
        """Close the underlying httpx client. Idempotent per M0.3 convention."""
        if self._closed:
            return
        await self._httpx_client.aclose()
        self._closed = True


class SyncSidecarClient:
    """Sync variant of SidecarClient for the flusher thread.

    Plan review fix #3 (see plan sketch): the flusher runs in a thread
    and must NOT call asyncio.run() against a persistent AsyncClient —
    asyncio.run creates a fresh event loop each call, while
    httpx.AsyncClient binds to the loop it was created in. Use this
    sync client from the flusher instead.

    Per D5: this client exposes only `post_json`. The flusher is the
    only Flask-side sync caller of the sidecar and it only POSTs.
    Future sync callers get their own methods as needed. No get_json /
    delete_json on the sync client (YAGNI).

    Semantics match `SidecarClient` exactly: same retry contract, same
    breaker contract, same 4xx fast-fail, same malformed-JSON-as-
    transient, same HALF_OPEN reprobe-window semantic with counter
    reset on OPEN → HALF_OPEN transition.
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
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0
        self._closed = False
        if transport is None:
            transport = httpx.HTTPTransport(uds=socket_path)
        self._httpx_client = httpx.Client(
            transport=transport,
            base_url="http://sidecar",
            timeout=timeout_s,
        )

    @property
    def state(self) -> CircuitState:
        """Current breaker state, with lazy OPEN → HALF_OPEN transition
        (counter reset on transition — same semantic as SidecarClient.state).
        """
        if self._state == CircuitState.OPEN:
            now = time.monotonic()
            if (now - self._opened_at) >= self._breaker_cooldown_s:
                self._state = CircuitState.HALF_OPEN
                self._consecutive_failures = 0
        return self._state

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def post_json(
        self, path: str, body: dict, headers: dict | None = None
    ) -> dict:
        if self.state == CircuitState.OPEN:
            raise RuntimeError("sidecar circuit open")
        merged_headers = {"X-Sidecar-Token": self._token, **(headers or {})}
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                time.sleep(min(1.0, 0.1 * (2 ** (attempt - 1))))
            try:
                r = self._httpx_client.post(
                    path, json=body, headers=merged_headers
                )
                r.raise_for_status()
                data = r.json() if r.content else {}
                self._record_success()
                return data
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == self._max_retries:
                    self._record_failure()
                    raise
            except httpx.HTTPStatusError as e:
                if 500 <= e.response.status_code < 600:
                    if attempt == self._max_retries:
                        self._record_failure()
                        raise
                else:
                    # 4xx fast-fail — same contract as SidecarClient.
                    raise
            except (ValueError, httpx.DecodingError):
                if attempt == self._max_retries:
                    self._record_failure()
                    raise
        raise RuntimeError("unreachable: post_json exhausted retries without raising")

    def close(self) -> None:
        """Close the underlying httpx client. Idempotent per M0.3 convention."""
        if self._closed:
            return
        self._httpx_client.close()
        self._closed = True

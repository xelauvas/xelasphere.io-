"""M0.14 Stage 1 RED: tests for Flask-side SidecarClient + SyncSidecarClient.

Stage 1 RED state: the target module `app.mempalace.client` exists
with __init__ and no-op aclose/close stubs; the methods these tests
exercise (post_json, get_json, delete_json, state, _record_success,
_record_failure) are UNDEFINED. Tests fail at method-lookup time with
AttributeError — the "functionality-missing" RED shape per standing
discipline.

Deviations from the plan sketch are enumerated in the M0.14 dispatch
body (D1-D7). Each test below documents which deviation(s) it exercises.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from app.mempalace.client import (
    ChaosTransport,
    CircuitState,
    SidecarClient,
    SyncSidecarClient,
)


# ============================================================================
# Async suite (16 declared, 20 collected with parametrization)
# ============================================================================


@pytest.mark.asyncio
async def test_client_posts_json_ok_on_200(
    async_sidecar_client, capturing_mock_transport
):
    wrap = capturing_mock_transport(
        lambda req: httpx.Response(200, json={"ok": True})
    )
    client = await async_sidecar_client(transport=wrap.transport)
    result = await client.post_json("/palace/u/search", {"query": "x"})
    assert result == {"ok": True}
    assert client.state == CircuitState.CLOSED
    assert client._consecutive_failures == 0


@pytest.mark.asyncio
async def test_client_retries_on_5xx_then_gives_up(
    async_sidecar_client, capturing_mock_transport
):
    wrap = capturing_mock_transport(lambda req: httpx.Response(503))
    client = await async_sidecar_client(transport=wrap.transport, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert len(wrap.requests) == 3  # 1 initial + 2 retries
    # E resolution: one outer call burns one breaker slot, not three.
    assert client._consecutive_failures == 1


@pytest.mark.asyncio
async def test_client_retries_on_connect_error_then_gives_up(
    async_sidecar_client,
):
    chaos = ChaosTransport(
        httpx.MockTransport(lambda req: httpx.Response(200)),
        fail_every=1,  # every call fails
    )
    client = await async_sidecar_client(transport=chaos, max_retries=2)
    with pytest.raises(httpx.ConnectError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert chaos.calls == 3
    assert client._consecutive_failures == 1


@pytest.mark.asyncio
async def test_client_retries_on_timeout_then_gives_up(
    async_sidecar_client, capturing_mock_transport
):
    def raise_timeout(req):
        raise httpx.TimeoutException("read timeout")

    wrap = capturing_mock_transport(raise_timeout)
    client = await async_sidecar_client(transport=wrap.transport, max_retries=2)
    with pytest.raises(httpx.TimeoutException):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert len(wrap.requests) == 3
    assert client._consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 422, 429])
async def test_client_fast_fails_on_4xx_no_retry_no_breaker_impact(
    async_sidecar_client, capturing_mock_transport, status
):
    """D2/J resolution. 4xx is deterministic: no retry, no breaker impact.

    Includes 429 (Rate Limit): retrying an active rate-limit compounds
    the problem and the Flask flusher has no way to honor Retry-After
    sensibly. Treated as 4xx fast-fail per this client's contract.
    """
    wrap = capturing_mock_transport(lambda req: httpx.Response(status))
    client = await async_sidecar_client(transport=wrap.transport, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert len(wrap.requests) == 1  # no retry
    assert client._consecutive_failures == 0  # no breaker impact
    assert client.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_client_breaker_opens_after_exactly_N_consecutive_outer_failures(
    async_sidecar_client, capturing_mock_transport
):
    """C resolution: N-th failure opens (threshold=5 → 5 outer failures open)."""
    wrap = capturing_mock_transport(lambda req: httpx.Response(503))
    client = await async_sidecar_client(
        transport=wrap.transport, max_retries=0, breaker_threshold=5
    )
    for i in range(4):
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_json("/palace/u/search", {"query": "x"})
        assert client.state == CircuitState.CLOSED, f"state after failure {i + 1}"
    # 5th failure opens.
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert client.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_client_one_outer_call_is_one_breaker_slot_regardless_of_internal_retries(
    async_sidecar_client, capturing_mock_transport
):
    """E resolution: 5 outer calls × 3 transport attempts = 15 transport calls
    but only 5 breaker slots burn, not 15.
    """
    wrap = capturing_mock_transport(lambda req: httpx.Response(503))
    client = await async_sidecar_client(
        transport=wrap.transport, max_retries=2, breaker_threshold=5
    )
    for _ in range(5):
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_json("/palace/u/search", {"query": "x"})
    assert len(wrap.requests) == 15
    assert client._consecutive_failures == 5
    assert client.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_client_breaker_open_fast_fails_without_transport_call(
    async_sidecar_client, capturing_mock_transport
):
    """Pre-existing OPEN state gates before transport touches."""
    wrap = capturing_mock_transport(lambda req: httpx.Response(200, json={}))
    client = await async_sidecar_client(transport=wrap.transport)
    # Pre-open the breaker directly.
    client._state = CircuitState.OPEN
    client._opened_at = time.monotonic()
    with pytest.raises(RuntimeError, match="sidecar circuit open"):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert len(wrap.requests) == 0


@pytest.mark.asyncio
async def test_client_successful_reprobe_transitions_to_closed(
    async_sidecar_client, capturing_mock_transport
):
    """HALF_OPEN → CLOSED on success."""
    wrap = capturing_mock_transport(lambda req: httpx.Response(200, json={"ok": True}))
    client = await async_sidecar_client(
        transport=wrap.transport, breaker_threshold=5, breaker_cooldown_s=30.0
    )
    # Pre-open with cooldown already elapsed.
    client._state = CircuitState.OPEN
    client._opened_at = time.monotonic() - 60.0
    client._consecutive_failures = 5
    result = await client.post_json("/palace/u/search", {"query": "x"})
    assert result == {"ok": True}
    assert client.state == CircuitState.CLOSED
    assert client._consecutive_failures == 0


@pytest.mark.asyncio
async def test_client_reprobe_window_requires_N_failures_to_retrip(
    async_sidecar_client, capturing_mock_transport
):
    """D1/M resolution KEY TEST: simplified HALF_OPEN tolerates N-1 failures
    before re-tripping, NOT 1.

    After cooldown expires, _consecutive_failures resets to 0 (D1 explicit
    instruction). Four 503s during the reprobe window leave state HALF_OPEN;
    the fifth re-trips to OPEN.
    """
    wrap = capturing_mock_transport(lambda req: httpx.Response(503))
    client = await async_sidecar_client(
        transport=wrap.transport,
        max_retries=0,
        breaker_threshold=5,
        breaker_cooldown_s=30.0,
    )
    client._state = CircuitState.OPEN
    client._opened_at = time.monotonic() - 60.0
    client._consecutive_failures = 5  # pre-reset; state property should reset
    for i in range(4):
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_json("/palace/u/search", {"query": "x"})
        assert (
            client.state == CircuitState.HALF_OPEN
        ), f"state after reprobe failure {i + 1}"
    # 5th failure in the window re-trips.
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert client.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_client_single_failure_during_reprobe_window_does_not_retrip(
    async_sidecar_client, capturing_mock_transport
):
    """D1/M resolution complement: a single failure in HALF_OPEN does NOT retrip.

    BR2 resolution: on OPEN → HALF_OPEN transition, counter resets to 0;
    single 503 during reprobe increments to 1, NOT threshold. Subsequent
    success transitions HALF_OPEN → CLOSED and resets counter.
    """
    responses = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])

    def handler(req):
        return next(responses)

    wrap = capturing_mock_transport(handler)
    client = await async_sidecar_client(
        transport=wrap.transport,
        max_retries=0,
        breaker_threshold=5,
        breaker_cooldown_s=30.0,
    )
    client._state = CircuitState.OPEN
    client._opened_at = time.monotonic() - 60.0
    client._consecutive_failures = 5
    # First reprobe call fails: state stays HALF_OPEN, counter at 1.
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert client.state == CircuitState.HALF_OPEN
    assert client._consecutive_failures == 1
    # Second reprobe call succeeds: transitions CLOSED, counter resets.
    result = await client.post_json("/palace/u/search", {"query": "x"})
    assert result == {"ok": True}
    assert client.state == CircuitState.CLOSED
    assert client._consecutive_failures == 0


@pytest.mark.asyncio
async def test_client_backoff_applies_between_retries(
    async_sidecar_client, capturing_mock_transport
):
    """Exponential backoff: 0.1s after attempt 1, 0.2s after attempt 2.

    Asserts on asyncio.sleep call args rather than wall-clock for CI
    determinism. Plan fix #7 specifies min(1.0, 0.1 * 2^(attempt-1)).
    """
    wrap = capturing_mock_transport(lambda req: httpx.Response(503))
    client = await async_sidecar_client(transport=wrap.transport, max_retries=2)
    with patch("app.mempalace.client.asyncio.sleep") as sleep_mock:
        # Make the mock awaitable.
        async def _sleep(t):
            return None

        sleep_mock.side_effect = _sleep
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_json("/palace/u/search", {"query": "x"})
    sleep_calls = [call.args[0] for call in sleep_mock.call_args_list]
    assert sleep_calls == [0.1, 0.2]


@pytest.mark.asyncio
async def test_client_get_json_issues_get_request(
    async_sidecar_client, capturing_mock_transport
):
    """Plan fix #5: real GET, not POST-to-a-GET-route."""
    wrap = capturing_mock_transport(lambda req: httpx.Response(200, json={"n": 1}))
    client = await async_sidecar_client(transport=wrap.transport)
    result = await client.get_json("/palace/u/stats")
    assert result == {"n": 1}
    assert wrap.last_request is not None
    assert wrap.last_request.method == "GET"
    assert wrap.last_request.url.path == "/palace/u/stats"
    assert wrap.last_request.content == b""


@pytest.mark.asyncio
async def test_client_delete_json_sends_delete_with_body(
    async_sidecar_client, capturing_mock_transport
):
    """Plan fix #6: real DELETE with body, not X-HTTP-Method-Override hack.

    Sidecar-side route at mempalace-sidecar/.../lifecycle.py:66-71
    accepts DeleteRequest body on DELETE /palace/u/.
    """
    import json

    wrap = capturing_mock_transport(
        lambda req: httpx.Response(200, json={"deleted": True})
    )
    client = await async_sidecar_client(transport=wrap.transport)
    result = await client.delete_json("/palace/u/", {"user_id": "x"})
    assert result == {"deleted": True}
    assert wrap.last_request is not None
    assert wrap.last_request.method == "DELETE"
    assert json.loads(wrap.last_request.content) == {"user_id": "x"}


@pytest.mark.asyncio
async def test_client_non_empty_malformed_json_on_200_treated_as_transient_retry(
    async_sidecar_client, capturing_mock_transport
):
    """Catch #1: malformed JSON on 2xx is transient (partial response on
    connection instability). Retry + count toward breaker.

    Exception class tuple per BR5: json.JSONDecodeError is a ValueError
    subclass; httpx.DecodingError may also fire depending on version.
    """
    wrap = capturing_mock_transport(
        lambda req: httpx.Response(200, content=b"not json")
    )
    client = await async_sidecar_client(transport=wrap.transport, max_retries=2)
    with pytest.raises((ValueError, httpx.DecodingError)):
        await client.post_json("/palace/u/search", {"query": "x"})
    assert len(wrap.requests) == 3
    assert client._consecutive_failures == 1


@pytest.mark.asyncio
async def test_chaos_transport_fails_every_n_calls_deterministic():
    """Infrastructure self-test: ChaosTransport.calls increments BEFORE
    the fail-check, so both successful and failed calls count toward
    fail_every. Exception type is httpx.ConnectError specifically.
    """
    inner = httpx.MockTransport(lambda req: httpx.Response(200))
    chaos = ChaosTransport(inner, fail_every=3)

    async with httpx.AsyncClient(transport=chaos, base_url="http://test") as c:
        # Calls 1, 2, 4, 5 succeed; 3, 6 fail.
        r1 = await c.get("/")
        assert r1.status_code == 200
        r2 = await c.get("/")
        assert r2.status_code == 200
        with pytest.raises(httpx.ConnectError) as exc3:
            await c.get("/")
        assert isinstance(exc3.value, httpx.ConnectError)
        r4 = await c.get("/")
        assert r4.status_code == 200
        r5 = await c.get("/")
        assert r5.status_code == 200
        with pytest.raises(httpx.ConnectError) as exc6:
            await c.get("/")
        assert isinstance(exc6.value, httpx.ConnectError)
    assert chaos.calls == 6


# ============================================================================
# Sync suite (5 declared, 6 collected with parametrization)
# ============================================================================


def test_sync_client_posts_json_ok_on_200(sync_sidecar_client):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    client = sync_sidecar_client(transport=transport)
    result = client.post_json("/palace/u/search", {"query": "x"})
    assert result == {"ok": True}
    assert client._consecutive_failures == 0


def test_sync_client_retries_on_5xx_then_gives_up(sync_sidecar_client):
    captured: list = []

    def handler(req):
        captured.append(req)
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    client = sync_sidecar_client(transport=transport, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.post_json("/palace/u/search", {"query": "x"})
    assert len(captured) == 3
    assert client._consecutive_failures == 1


@pytest.mark.parametrize("status", [401, 404])
def test_sync_client_fast_fails_on_4xx_no_breaker_impact(sync_sidecar_client, status):
    """D2/J resolution on sync path."""
    captured: list = []

    def handler(req):
        captured.append(req)
        return httpx.Response(status)

    transport = httpx.MockTransport(handler)
    client = sync_sidecar_client(transport=transport, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.post_json("/palace/u/search", {"query": "x"})
    assert len(captured) == 1
    assert client._consecutive_failures == 0


def test_sync_client_breaker_opens_after_N_failures(sync_sidecar_client):
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    client = sync_sidecar_client(transport=transport, max_retries=0, breaker_threshold=5)
    for _ in range(4):
        with pytest.raises(httpx.HTTPStatusError):
            client.post_json("/palace/u/search", {"query": "x"})
        assert client.state == CircuitState.CLOSED
    with pytest.raises(httpx.HTTPStatusError):
        client.post_json("/palace/u/search", {"query": "x"})
    assert client.state == CircuitState.OPEN


def test_sync_client_reprobe_window_requires_N_failures_to_retrip(sync_sidecar_client):
    """D1/M resolution on sync path."""
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    client = sync_sidecar_client(
        transport=transport,
        max_retries=0,
        breaker_threshold=5,
        breaker_cooldown_s=30.0,
    )
    client._state = CircuitState.OPEN
    client._opened_at = time.monotonic() - 60.0
    client._consecutive_failures = 5
    for _ in range(4):
        with pytest.raises(httpx.HTTPStatusError):
            client.post_json("/palace/u/search", {"query": "x"})
        assert client.state == CircuitState.HALF_OPEN
    with pytest.raises(httpx.HTTPStatusError):
        client.post_json("/palace/u/search", {"query": "x"})
    assert client.state == CircuitState.OPEN

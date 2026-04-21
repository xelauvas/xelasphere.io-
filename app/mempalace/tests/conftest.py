"""Fixtures shared by app/mempalace/tests/.

Note on pytest-asyncio: version 1.3.0 is installed in the Flask venv
(verified at M0.14 pre-resolution). Because `asyncio_mode` is not set
to `auto` in pytest.ini, pytest-asyncio runs in strict mode: async
fixtures MUST use `@pytest_asyncio.fixture`, async tests MUST use
`@pytest.mark.asyncio`. Sync fixtures (the plain-`@pytest.fixture`
ones below) continue to work with async tests that consume them.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest
import pytest_asyncio


@pytest.fixture
def outbox_db_path(tmp_path):
    return tmp_path / "outbox.db"


@pytest.fixture
def fake_uds_path(tmp_path):
    return tmp_path / "sidecar.sock"


@pytest_asyncio.fixture
async def async_sidecar_client(fake_uds_path):
    """Factory for SidecarClient with guaranteed aclose on teardown.

    Usage in a test:
        client = await async_sidecar_client(transport=my_transport)
        await client.post_json(...)

    The fixture tracks every client it creates and awaits aclose() on
    each at teardown. SidecarClient.aclose is idempotent (per M0.3
    convention), so tests that manually aclose are safe.
    """
    from app.mempalace.client import SidecarClient

    created: list = []

    async def _factory(**kwargs):
        kwargs.setdefault("socket_path", str(fake_uds_path))
        kwargs.setdefault("token", "t" * 32)
        client = SidecarClient(**kwargs)
        created.append(client)
        return client

    yield _factory

    for client in created:
        await client.aclose()


@pytest.fixture
def sync_sidecar_client(fake_uds_path):
    """Factory for SyncSidecarClient with guaranteed close on teardown."""
    from app.mempalace.client import SyncSidecarClient

    created: list = []

    def _factory(**kwargs):
        kwargs.setdefault("socket_path", str(fake_uds_path))
        kwargs.setdefault("token", "t" * 32)
        client = SyncSidecarClient(**kwargs)
        created.append(client)
        return client

    yield _factory

    for client in created:
        client.close()


class CapturingMockTransport:
    """Sync/async MockTransport wrapper that records every request.

    D7 deviation: plan-sketch tests assumed `transport.last_request` as
    a built-in attribute on httpx.MockTransport. It is not — MockTransport
    takes a handler and does not expose inspection state. This wrapper
    captures every request via the handler closure and exposes
    `.requests` (list) and `.last_request` (the most recent).

    Works for both async and sync tests because httpx.MockTransport
    dispatches to the same handler under either protocol.
    """

    def __init__(
        self,
        response_factory: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._response_factory = response_factory
        self.transport = httpx.MockTransport(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response_factory(request)

    @property
    def last_request(self) -> httpx.Request | None:
        return self.requests[-1] if self.requests else None


@pytest.fixture
def capturing_mock_transport():
    """Factory for CapturingMockTransport. Use when test needs to inspect
    the request (method, path, headers, body) the client issued.
    """

    def _factory(response_factory: Callable[[httpx.Request], httpx.Response]):
        return CapturingMockTransport(response_factory)

    return _factory

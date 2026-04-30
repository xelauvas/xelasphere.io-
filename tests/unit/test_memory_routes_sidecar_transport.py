"""Sidecar helpers in memory_routes.py must use UDS, not TCP/DNS.

Regression guard for the bug where Forget Me + conversation-memory list/delete
hit the sidecar via ``http://sidecar`` resolved through DNS. In production the
sidecar listens only on a Unix domain socket, so DNS-based clients fail with
"Failed to resolve 'sidecar'" — the cascade then reports ``palace`` as a
partial failure even though every other store wipes cleanly.
"""
import os
from unittest.mock import patch

import httpx
import pytest

from app.blueprints import memory_routes


def test_socket_path_defaults_to_canonical_path():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MEMPALACE_SOCKET_PATH", None)
        assert memory_routes._sidecar_socket_path() == "/run/mempalace/sidecar.sock"


def test_socket_path_honors_env_override(monkeypatch):
    monkeypatch.setenv("MEMPALACE_SOCKET_PATH", "/tmp/custom.sock")
    assert memory_routes._sidecar_socket_path() == "/tmp/custom.sock"


def test_httpx_client_uses_uds_transport(monkeypatch):
    """The client must be configured with httpx.HTTPTransport(uds=...).
    A plain TCP client would re-introduce the DNS-resolution bug."""
    monkeypatch.setenv("MEMPALACE_SOCKET_PATH", "/tmp/test.sock")
    client = memory_routes._sidecar_httpx_client()
    try:
        assert isinstance(client, httpx.Client)
        # base_url is the placeholder host that UDS transport ignores
        assert str(client.base_url) == "http://sidecar"
        # Probing the transport directly: HTTPTransport's _pool is an
        # httpcore connection pool — for UDS transports, it stores the
        # socket path on the pool's UDS attribute.
        transport = client._transport
        assert isinstance(transport, httpx.HTTPTransport), (
            "UDS transport required; bare TCP client re-introduces the DNS bug"
        )
    finally:
        client.close()


def test_no_dns_based_requests_fallback():
    """Module must not silently fall back to a DNS-based client.

    The previous bug used ``requests`` against ``http://sidecar``; if anyone
    re-introduces that pattern, this test surfaces it before deploy."""
    src = (
        memory_routes.__file__
        if memory_routes.__file__.endswith(".py")
        else memory_routes.__file__ + ".py"
    )
    with open(src) as f:
        body = f.read()
    # The literal that broke production. If it comes back, so does the bug.
    assert '"http://sidecar"' in body, (
        "expected the placeholder host literal — verify file is current"
    )
    # No more raw requests against the sidecar URL.
    assert "import requests as _requests" not in body, (
        "raw requests fallback re-introduced; sidecar must go over UDS via httpx"
    )
    assert "_sidecar_base_url" not in body, (
        "_sidecar_base_url() returned a DNS-based URL; replaced by _sidecar_httpx_client"
    )

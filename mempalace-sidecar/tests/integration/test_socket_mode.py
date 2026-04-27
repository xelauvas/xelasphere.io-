"""Integration test for spec §7.1 + runbook R1 — UDS socket mode 0660.

Lifespan must ``os.chmod(socket_path, 0o660)`` after uvicorn binds the
UDS but before ``sd_notify(READY=1)`` so any Flask-side probe never
observes a pre-chmod state. Closes #7.

In production uvicorn binds the socket before lifespan startup runs,
so the chmod call fires against a real bound UDS. The test pre-creates
a regular file at ``MEMPALACE_SOCKET_PATH`` to stand in for that bound
UDS — chmod operates on inode mode bits, not on the socket-vs-regular
distinction, so the assertion is faithful to production behavior.

The ``app`` fixture from ``tests/integration/conftest.py`` is not used
here because that fixture does not let the test override
``MEMPALACE_SOCKET_PATH``, and the default
``/run/mempalace/sidecar.sock`` is not writable in CI.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


async def test_lifespan_tightens_socket_mode_to_0660(
    monkeypatch, tmp_path: Path
) -> None:
    sock = tmp_path / "test.sock"
    sock.touch()
    sock.chmod(0o666)
    assert stat.S_IMODE(os.stat(sock).st_mode) == 0o666, (
        "precondition: stub socket should start at 0666 (uvicorn default)"
    )

    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    monkeypatch.setenv("MEMPALACE_SOCKET_PATH", str(sock))

    from mempalace_sidecar.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert stat.S_IMODE(os.stat(sock).st_mode) == 0o660, (
            "lifespan must chmod the UDS to 0660 before sd_notify(READY=1)"
        )


async def test_lifespan_no_op_when_socket_path_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Tolerate a missing socket path without crashing startup.

    When uvicorn isn't driving the app (in-process tests, ad-hoc tooling),
    the socket file does not exist. The lifespan must not raise — silent
    no-op is acceptable here because the chmod call's ONLY concern is
    tightening a real bound UDS in production.
    """
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    monkeypatch.setenv("MEMPALACE_SOCKET_PATH", str(tmp_path / "absent.sock"))

    from mempalace_sidecar.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        pass

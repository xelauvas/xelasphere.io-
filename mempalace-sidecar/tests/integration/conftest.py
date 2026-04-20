"""Shared fixtures for integration tests (M0.5 onwards).

Fixtures:
  * ``token`` — the sidecar auth token (32 chars).
  * ``app`` — a fresh FastAPI app rooted at tmp_path.
  * ``auth`` — header dict containing the token.
  * ``client`` — AsyncClient wired to the app with lifespan driven
    explicitly (httpx 0.28 ASGITransport no longer drives lifespan).

These mirror the ones in ``test_ingest_idempotency.py`` so M0.5 / M0.6
tests can import them without re-declaring. ``test_ingest_idempotency``
still declares its own copies; we can unify after M0.5 settles.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def token() -> str:
    return "t" * 32


@pytest.fixture
def app(monkeypatch, tmp_path: Path, token: str):
    monkeypatch.setenv("MEMPALACE_TOKEN", token)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    from mempalace_sidecar.main import create_app

    return create_app()


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"X-Sidecar-Token": token}


@pytest.fixture
async def client(app):
    """AsyncClient with the app's lifespan_context driven explicitly.

    Required so ``app.state.registry`` and ``app.state.embedding_ready``
    land before any route fires. Mirrors the pattern in
    ``test_ingest_idempotency.py``.
    """
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            yield c

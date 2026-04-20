from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from mempalace_sidecar.main import create_app


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    return create_app()


def test_lifespan_notifies_ready(app):
    # NOTE: plan specified AsyncClient+ASGITransport, but httpx 0.28 ASGITransport
    # no longer drives the ASGI lifespan protocol. Starlette/FastAPI TestClient
    # (used as a context manager) does — same semantic, no new deps.
    with patch("mempalace_sidecar.main._sd_notify_ready") as mock_ready:
        with TestClient(app) as c:
            c.get("/healthz")
        mock_ready.assert_called_once()

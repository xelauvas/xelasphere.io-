import pytest
from httpx import ASGITransport, AsyncClient
from mempalace_sidecar.main import create_app
from mempalace_sidecar.config import Settings

@pytest.fixture
def token():
    return "t" * 32

@pytest.fixture
def app(monkeypatch, tmp_path, token):
    monkeypatch.setenv("MEMPALACE_TOKEN", token)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    return create_app()

async def test_healthz_skips_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200

async def test_palace_route_requires_token(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/palace/u/ingest", json={})
    assert r.status_code == 401

async def test_palace_route_rejects_wrong_token(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/palace/u/ingest", json={}, headers={"X-Sidecar-Token": "wrong"})
    assert r.status_code == 401

async def test_palace_route_accepts_correct_token(app, token):
    # stub palace route will 404 (not implemented yet); we only assert auth passes (not 401)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/palace/u/ingest", json={}, headers={"X-Sidecar-Token": token})
    assert r.status_code != 401

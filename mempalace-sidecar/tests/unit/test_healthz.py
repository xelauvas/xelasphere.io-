import pytest
from httpx import ASGITransport, AsyncClient
from mempalace_sidecar.main import create_app

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "d" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    return create_app()

async def test_healthz_ok(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["version"], str)
    assert isinstance(body["uptime_s"], (int, float))
    assert body["schema_version"] == 1

import pytest
from mempalace_sidecar.config import Settings

def test_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "deadbeef" * 4)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    s = Settings()
    assert s.socket_path == "/run/mempalace/sidecar.sock"
    assert s.cache_max_bytes == 2 * 1024**3
    assert s.idle_ttl_sec == 14400
    assert s.embed_model == "all-MiniLM-L6-v2"
    assert s.kg_daily_usd_cap == pytest.approx(5.0)
    assert s.shadow_sample_rate == pytest.approx(0.20)

def test_settings_requires_token(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMPALACE_TOKEN", raising=False)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    with pytest.raises(Exception):
        Settings()


def test_prometheus_multiproc_dir_from_bare_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_TOKEN", "d" * 32)
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_USER_ID_HASH_SALT", "x" * 16)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path / "prom"))
    s = Settings()
    assert str(s.prometheus_multiproc_dir) == str(tmp_path / "prom")

import pytest

@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "mempalace"
    data.mkdir()
    (data / "palaces").mkdir()
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(data))
    return data

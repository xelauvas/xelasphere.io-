import pytest

@pytest.fixture
def outbox_db_path(tmp_path):
    return tmp_path / "outbox.db"

@pytest.fixture
def fake_uds_path(tmp_path):
    return tmp_path / "sidecar.sock"

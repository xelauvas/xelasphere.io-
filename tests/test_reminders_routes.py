import pytest
from app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


class TestRemindersBlueprintRegistered:
    def test_blueprint_is_registered(self, app):
        assert "reminders" in app.blueprints

    def test_list_endpoint_exists_and_requires_login(self, client):
        # No session → 403 (login_required's response)
        resp = client.get("/reminders")
        assert resp.status_code == 403

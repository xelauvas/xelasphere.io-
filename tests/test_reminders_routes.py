"""Reminders CRUD route tests. Bypasses CSRF via monkeypatch (same pattern
as tests/conftest.py's mempalace_shadow fixture)."""
import sys
import pytest
from unittest.mock import patch, MagicMock

from app import create_app


@pytest.fixture(autouse=True)
def _bypass_csrf(monkeypatch):
    """Replace validate_csrf with a no-op so X-CSRFToken header presence is enough."""
    _limiter_mod = sys.modules["app.limiter"]
    monkeypatch.setattr(_limiter_mod, "validate_csrf", lambda *a, **kw: None)


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_session(client):
    with client.session_transaction() as sess:
        sess["user"] = {"uid": "u1", "email": "a@b.com"}
    return client


CSRF_HEADERS = {"X-CSRFToken": "test-bypass", "Content-Type": "application/json"}


class TestRemindersBlueprintRegistered:
    def test_blueprint_is_registered(self, app):
        assert "reminders" in app.blueprints

    def test_list_endpoint_requires_login(self, client):
        # No session → 403 from @login_required
        resp = client.get("/reminders")
        assert resp.status_code == 403


class TestCreateReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_create_persists_and_returns_id(self, mock_db, auth_session):
        fake_doc_ref = MagicMock()
        fake_doc_ref.id = "rem_abc"
        # mock chain: users/<uid>/reminders/<auto-id>
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = fake_doc_ref

        resp = auth_session.post(
            "/reminders",
            json={
                "name": "Call mom",
                "instructions": "from kitchen",
                "fireAtLocal": "2099-01-01T09:00:00",
                "timezone": "Europe/London",
            },
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["id"] == "rem_abc"
        assert body["reminder"]["id"] == "rem_abc"
        assert fake_doc_ref.set.called

    @patch("app.blueprints.reminders_routes.db")
    def test_create_rejects_missing_name(self, mock_db, auth_session):
        resp = auth_session.post(
            "/reminders",
            json={"fireAtLocal": "2099-01-01T09:00", "timezone": "Europe/London"},
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    @patch("app.blueprints.reminders_routes.db")
    def test_create_rejects_past_fire_time(self, mock_db, auth_session):
        resp = auth_session.post(
            "/reminders",
            json={"name": "x", "fireAtLocal": "2020-01-01T09:00", "timezone": "Europe/London"},
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 400

    @patch("app.blueprints.reminders_routes.db")
    @patch("app.blueprints.reminders_routes.Config")
    def test_create_returns_503_when_disabled(self, mock_cfg, mock_db, auth_session):
        mock_cfg.REMINDERS_ENABLED = False
        resp = auth_session.post(
            "/reminders",
            json={"name": "x", "fireAtLocal": "2099-01-01T09:00", "timezone": "Europe/London"},
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 503

    def test_create_requires_login(self, client):
        # No session
        resp = client.post(
            "/reminders",
            json={"name": "x", "fireAtLocal": "2099-01-01T09:00", "timezone": "Europe/London"},
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 403


class TestGetReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_get_returns_existing(self, mock_db, auth_session):
        snap = MagicMock(exists=True, id="rem_x")
        snap.to_dict.return_value = {"id": "rem_x", "name": "test"}
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        resp = auth_session.get("/reminders/rem_x")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["reminder"]["name"] == "test"
        assert body["reminder"]["id"] == "rem_x"

    @patch("app.blueprints.reminders_routes.db")
    def test_get_404_when_missing(self, mock_db, auth_session):
        snap = MagicMock(exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        resp = auth_session.get("/reminders/rem_missing")
        assert resp.status_code == 404


class TestListReminders:
    @patch("app.blueprints.reminders_routes.db")
    def test_list_returns_user_reminders(self, mock_db, auth_session):
        fake_docs = []
        for i in range(3):
            d = MagicMock()
            d.to_dict.return_value = {"name": f"r{i}", "status": "scheduled"}
            d.id = f"rem_{i}"
            fake_docs.append(d)

        query = mock_db.collection.return_value.document.return_value.collection.return_value
        query.order_by.return_value.limit.return_value.stream.return_value = fake_docs

        resp = auth_session.get("/reminders")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["reminders"]) == 3
        assert body["reminders"][0]["id"] == "rem_0"

    @patch("app.blueprints.reminders_routes.db")
    def test_list_with_status_filter(self, mock_db, auth_session):
        query = mock_db.collection.return_value.document.return_value.collection.return_value
        # status=delivered filter path
        filtered = query.where.return_value
        filtered.order_by.return_value.limit.return_value.stream.return_value = []

        resp = auth_session.get("/reminders?status=delivered")
        assert resp.status_code == 200
        # Verify the where() was called with the filter
        assert query.where.called
        args, _ = query.where.call_args
        assert args[0] == "status"
        assert args[2] == "delivered"


class TestPatchReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_patch_updates_name(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"id": "rem_x", "status": "scheduled", "name": "old", "timezone": "Europe/London"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"name": "new"}, headers=CSRF_HEADERS)
        assert resp.status_code == 200
        args, _ = doc_ref.update.call_args
        assert args[0]["name"] == "new"

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_can_pause(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"status": "scheduled"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"status": "paused"}, headers=CSRF_HEADERS)
        assert resp.status_code == 200
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "paused"

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_rejects_invalid_status(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"status": "scheduled"}
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"status": "frobnicated"}, headers=CSRF_HEADERS)
        assert resp.status_code == 400

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_rejects_unknown_fields(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"status": "scheduled"}
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"sneaky": "field"}, headers=CSRF_HEADERS)
        assert resp.status_code == 400
        assert "unknown" in resp.get_json()["error"].lower()

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_retry_resets_failed(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"status": "failed", "attemptCount": 3, "lastError": "503"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"_retry": True}, headers=CSRF_HEADERS)
        assert resp.status_code == 200
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "scheduled"
        assert args[0]["attemptCount"] == 0
        assert args[0]["lastError"] is None

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_404_when_missing(self, mock_db, auth_session):
        snap = MagicMock(exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        resp = auth_session.patch("/reminders/missing", json={"name": "x"}, headers=CSRF_HEADERS)
        assert resp.status_code == 404


class TestRecurrenceField:
    """recurrence field is accepted on POST + PATCH and validated against
    the preset enum. Unknown presets return 400."""

    @patch("app.blueprints.reminders_routes.db")
    def test_create_accepts_recurrence_daily(self, mock_db, auth_session):
        fake_doc_ref = MagicMock()
        fake_doc_ref.id = "rem_rec1"
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = fake_doc_ref

        payload = {
            "name": "Standup",
            "instructions": "10-minute team sync",
            "fireAtLocal": "2030-01-01T09:00:00",
            "timezone": "Europe/London",
            "recurrence": "DAILY",
        }
        resp = auth_session.post("/reminders", json=payload, headers=CSRF_HEADERS)
        assert resp.status_code == 201, resp.get_json()
        assert resp.get_json().get("reminder", {}).get("recurrence") == "DAILY"

    @patch("app.blueprints.reminders_routes.db")
    def test_create_rejects_unknown_recurrence(self, mock_db, auth_session):
        payload = {
            "name": "Standup",
            "fireAtLocal": "2030-01-01T09:00:00",
            "timezone": "Europe/London",
            "recurrence": "FORTNIGHTLY",
        }
        resp = auth_session.post("/reminders", json=payload, headers=CSRF_HEADERS)
        assert resp.status_code == 400
        assert "recurrence" in resp.get_json().get("error", "").lower()

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_updates_recurrence(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"id": "rem_x", "status": "scheduled", "name": "Standup", "timezone": "Europe/London"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"recurrence": "WEEKLY"}, headers=CSRF_HEADERS)
        assert resp.status_code == 200, resp.get_json()
        args, _ = doc_ref.update.call_args
        assert args[0]["recurrence"] == "WEEKLY"

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_can_clear_recurrence_with_null(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"id": "rem_x", "status": "scheduled", "name": "Standup", "timezone": "Europe/London", "recurrence": "DAILY"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch("/reminders/rem_x", json={"recurrence": None}, headers=CSRF_HEADERS)
        assert resp.status_code == 200, resp.get_json()
        args, _ = doc_ref.update.call_args
        assert args[0]["recurrence"] is None


class TestDeleteReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_delete_removes_doc(self, mock_db, auth_session):
        snap = MagicMock(exists=True)
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.delete("/reminders/rem_x", headers=CSRF_HEADERS)
        assert resp.status_code == 200
        assert doc_ref.delete.called

    @patch("app.blueprints.reminders_routes.db")
    def test_delete_404_when_missing(self, mock_db, auth_session):
        snap = MagicMock(exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        resp = auth_session.delete("/reminders/missing", headers=CSRF_HEADERS)
        assert resp.status_code == 404

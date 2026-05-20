"""Reminders CRUD route tests. Bypasses CSRF via monkeypatch (same pattern
as tests/conftest.py's mempalace_shadow fixture)."""
import sys
import pytest
from datetime import datetime, timezone, timedelta
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

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_reschedule_resurrects_delivered(self, mock_db, auth_session):
        # Rescheduling a delivered (terminal) reminder to a future time must
        # flip status back to scheduled — otherwise the worker, which only
        # queries status==scheduled, would never fire the rescheduled reminder.
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {
            "id": "rem_x", "status": "delivered", "name": "call mom",
            "timezone": "Europe/London", "fireAt": "2020-01-01T00:00:00+00:00",
        }
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch(
            "/reminders/rem_x",
            json={"fireAtLocal": "2099-01-01T09:00:00", "timezone": "Europe/London"},
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 200, resp.get_json()
        written = doc_ref.update.call_args[0][0]
        assert written["status"] == "scheduled"
        assert "fireAt" in written

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_reschedule_scheduled_keeps_status(self, mock_db, auth_session):
        # Rescheduling a still-scheduled reminder must NOT inject a status change.
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {
            "id": "rem_x", "status": "scheduled", "name": "standup",
            "timezone": "Europe/London", "fireAt": "2099-01-01T00:00:00+00:00",
        }
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = snap

        resp = auth_session.patch(
            "/reminders/rem_x",
            json={"fireAtLocal": "2099-06-01T09:00:00", "timezone": "Europe/London"},
            headers=CSRF_HEADERS,
        )
        assert resp.status_code == 200, resp.get_json()
        assert "status" not in doc_ref.update.call_args[0][0]


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


class TestApplySnooze:
    """The shared _apply_snooze helper that both POST and GET routes use."""

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_10m_sets_snoozed_until(self, mock_db):
        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "scheduled",
            "name": "test", "recurrence": None,
        }
        fake_doc_ref.get.return_value = fake_snap
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value = fake_doc_ref

        from app.blueprints.reminders_routes import _apply_snooze
        status, payload = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 200
        update_call = fake_doc_ref.update.call_args[0][0]
        snoozed_until = datetime.fromisoformat(update_call["snoozedUntil"])
        expected = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert abs((snoozed_until - expected).total_seconds()) < 5
        # A still-scheduled reminder keeps its status and natural fireAt;
        # only snoozedUntil moves. Resurrection logic must not touch these.
        assert "status" not in update_call
        assert "fireAt" not in update_call

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_tomorrow_sets_24h_later(self, mock_db):
        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "scheduled",
            "name": "test", "recurrence": None,
        }
        fake_doc_ref.get.return_value = fake_snap
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value = fake_doc_ref

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "tomorrow", "uid_xyz")
        assert status == 200
        snoozed_until = datetime.fromisoformat(
            fake_doc_ref.update.call_args[0][0]["snoozedUntil"]
        )
        expected = datetime.now(timezone.utc) + timedelta(hours=24)
        assert abs((snoozed_until - expected).total_seconds()) < 5

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_invalid_duration_returns_400(self, mock_db):
        from app.blueprints.reminders_routes import _apply_snooze
        status, payload = _apply_snooze("rid_abc", "banana", "uid_xyz")
        assert status == 400
        assert "dur" in payload.get("error", "").lower()

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_on_paused_returns_409(self, mock_db):
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "paused",
            "name": "test", "recurrence": None,
        }
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        from app.blueprints.reminders_routes import _apply_snooze
        status, payload = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 409
        assert "snooze" in payload.get("error", "").lower()

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_on_delivered_recurring_allowed(self, mock_db):
        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "delivered",
            "name": "test", "recurrence": "DAILY",
        }
        fake_doc_ref.get.return_value = fake_snap
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value = fake_doc_ref

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 200
        # delivered+recurring also resurrects back to scheduled.
        assert fake_doc_ref.update.call_args[0][0]["status"] == "scheduled"

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_on_delivered_one_off_resurrects(self, mock_db):
        # A delivered one-off IS snoozeable — deferring a reminder that just
        # fired is the whole point of snooze. The helper resurrects it back
        # to `scheduled` (so the worker re-fires it) and moves fireAt to the
        # snooze target (a one-off has no future natural fire time).
        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "delivered",
            "name": "test", "recurrence": None,
            "fireAt": "2020-01-01T00:00:00+00:00",
        }
        fake_doc_ref.get.return_value = fake_snap
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value = fake_doc_ref

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 200
        written = fake_doc_ref.update.call_args[0][0]
        assert written["status"] == "scheduled"
        assert written["fireAt"] == written["snoozedUntil"]

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_wrong_owner_returns_404(self, mock_db):
        # Simplification: we look up under users/<user_id>/reminders/<id>,
        # so a wrong-owner attempt reads as 404 (doc not found under uid_OTHER).
        # This also avoids leaking doc existence across accounts.
        fake_snap = MagicMock()
        fake_snap.exists = False
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_OTHER")
        assert status == 404

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_missing_doc_returns_404(self, mock_db):
        fake_snap = MagicMock()
        fake_snap.exists = False
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 404


class TestSnoozePostRoute:
    """POST /reminders/<id>/snooze — session-authenticated in-app surface."""

    @patch("app.blueprints.reminders_routes.Config")
    @patch("app.blueprints.reminders_routes.db")
    def test_post_happy_path_returns_200(self, mock_db, mock_config, client, auth_session):
        mock_config.REMINDERS_SNOOZE_ENABLED = True
        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_x", "userId": "u1",
            "status": "scheduled", "name": "t", "recurrence": None,
        }
        fake_doc_ref.get.return_value = fake_snap
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value = fake_doc_ref

        resp = client.post(
            "/reminders/rid_x/snooze",
            json={"dur": "10m"},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200, resp.get_json()
        assert "snoozedUntil" in resp.get_json()

    def test_post_unauthenticated_returns_401_or_redirect(self, client):
        resp = client.post(
            "/reminders/rid_x/snooze",
            json={"dur": "10m"},
            headers={"X-CSRFToken": "test"},
        )
        # @login_required typically returns 401 or 302 — accept either.
        assert resp.status_code in (401, 302, 403)

    @patch("app.blueprints.reminders_routes.Config")
    @patch("app.blueprints.reminders_routes.db")
    def test_post_invalid_dur_returns_400(self, mock_db, mock_config, client, auth_session):
        mock_config.REMINDERS_SNOOZE_ENABLED = True
        resp = client.post(
            "/reminders/rid_x/snooze",
            json={"dur": "banana"},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 400

    @patch("app.blueprints.reminders_routes.Config")
    def test_post_flag_off_returns_503(self, mock_config, client, auth_session):
        mock_config.REMINDERS_SNOOZE_ENABLED = False
        resp = client.post(
            "/reminders/rid_x/snooze",
            json={"dur": "10m"},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 503


class TestSnoozeGetRoute:
    """GET /reminders/<id>/snooze?token=... — signed-token auth from emails."""

    def _mint_token(self, rid, uid):
        from app.services.signed_links import make_snooze_token
        return make_snooze_token(rid, uid)

    @patch("app.blueprints.reminders_routes.Config")
    @patch("app.blueprints.reminders_routes.db")
    def test_get_happy_path_renders_ok_page(self, mock_db, mock_config, client):
        mock_config.REMINDERS_SNOOZE_ENABLED = True
        mock_config.APP_BASE_URL = "https://xelasphere.com"
        # Note: also need real SECRET_KEY for token signing — fetch from real Config
        from app.config import Config as RealConfig
        mock_config.SECRET_KEY = RealConfig.SECRET_KEY

        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_x", "userId": "uid_y",
            "status": "scheduled", "name": "t", "recurrence": None,
            "timezone": "Europe/London", "sourceChatId": "session_abc",
        }
        fake_doc_ref.get.return_value = fake_snap
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value = fake_doc_ref

        token = self._mint_token("rid_x", "uid_y")
        resp = client.get(f"/reminders/rid_x/snooze?dur=10m&token={token}")
        assert resp.status_code == 200, resp.data
        assert b"Snoozed" in resp.data
        assert b"session_abc" in resp.data  # chat_url link present

    @patch("app.blueprints.reminders_routes.Config")
    @patch("app.blueprints.reminders_routes.verify_snooze_token")
    def test_get_expired_token_returns_410(self, mock_verify, mock_config, client):
        mock_config.REMINDERS_SNOOZE_ENABLED = True
        from app.services.signed_links import SnoozeTokenExpired
        mock_verify.side_effect = SnoozeTokenExpired("token too old")

        resp = client.get("/reminders/rid_x/snooze?dur=10m&token=any-token")
        assert resp.status_code == 410
        assert b"expired" in resp.data.lower()

    @patch("app.blueprints.reminders_routes.Config")
    def test_get_garbage_token_returns_400(self, mock_config, client):
        mock_config.REMINDERS_SNOOZE_ENABLED = True
        resp = client.get("/reminders/rid_x/snooze?dur=10m&token=not-a-token")
        assert resp.status_code == 400
        assert b"invalid" in resp.data.lower()

    @patch("app.blueprints.reminders_routes.Config")
    @patch("app.blueprints.reminders_routes.db")
    def test_get_wrong_uid_returns_404(self, mock_db, mock_config, client):
        # Note: 404 (not 403) because we look up by user_id from token;
        # if doc doesn't exist under that uid, it's "not found".
        mock_config.REMINDERS_SNOOZE_ENABLED = True
        from app.config import Config as RealConfig
        mock_config.SECRET_KEY = RealConfig.SECRET_KEY

        fake_snap = MagicMock()
        fake_snap.exists = False  # doc not found under uid_ATTACKER
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        token = self._mint_token("rid_x", "uid_ATTACKER")
        resp = client.get(f"/reminders/rid_x/snooze?dur=10m&token={token}")
        assert resp.status_code == 404

    @patch("app.blueprints.reminders_routes.Config")
    def test_get_flag_off_returns_503(self, mock_config, client):
        mock_config.REMINDERS_SNOOZE_ENABLED = False
        resp = client.get("/reminders/rid_x/snooze?dur=10m&token=anything")
        assert resp.status_code == 503

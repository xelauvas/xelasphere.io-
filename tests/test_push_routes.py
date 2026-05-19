"""Integration tests for push subscribe / unsubscribe / vapid endpoints."""
import sys
import pytest
from unittest.mock import MagicMock, patch

from app import create_app


@pytest.fixture(autouse=True)
def _bypass_csrf(monkeypatch):
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
        sess["user"] = {"uid": "u1", "email": "test@example.com"}
    return client


class TestVapidPublicKey:
    @patch("app.blueprints.push_routes.Config")
    def test_returns_key_when_flag_on(self, mock_config, client):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PUBLIC_KEY = "BNc-test-key"
        resp = client.get("/push/vapid-public-key")
        assert resp.status_code == 200
        assert resp.get_json() == {"key": "BNc-test-key"}

    @patch("app.blueprints.push_routes.Config")
    def test_returns_503_when_flag_off(self, mock_config, client):
        mock_config.PUSH_ENABLED = False
        resp = client.get("/push/vapid-public-key")
        assert resp.status_code == 503


class TestSubscribe:
    @patch("app.blueprints.push_routes.db")
    @patch("app.blueprints.push_routes.Config")
    def test_subscribe_happy_path(self, mock_config, mock_db, client, auth_session):
        mock_config.PUSH_ENABLED = True
        # Mock: no existing subs (empty stream)
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.where.return_value.stream.return_value = []
        new_doc_ref = MagicMock()
        new_doc_ref.id = "sub_new"
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.document.return_value = new_doc_ref

        resp = auth_session.post(
            "/push/subscribe",
            json={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                "keys": {"p256dh": "p", "auth": "a"},
                "userAgent": "Mozilla/5.0 Chrome",
            },
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 201, resp.get_json()
        assert "subId" in resp.get_json()

    @patch("app.blueprints.push_routes.db")
    @patch("app.blueprints.push_routes.Config")
    def test_subscribe_idempotent_on_duplicate(self, mock_config, mock_db, client, auth_session):
        mock_config.PUSH_ENABLED = True
        existing = MagicMock()
        existing.id = "sub_existing"
        existing.reference = MagicMock()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.where.return_value.stream.return_value = [existing]

        resp = auth_session.post(
            "/push/subscribe",
            json={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                "keys": {"p256dh": "p", "auth": "a"},
                "userAgent": "ua",
            },
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        assert existing.reference.update.called

    @patch("app.blueprints.push_routes.Config")
    def test_subscribe_returns_503_when_flag_off(self, mock_config, client, auth_session):
        mock_config.PUSH_ENABLED = False
        resp = auth_session.post(
            "/push/subscribe",
            json={"endpoint": "x", "keys": {"p256dh": "p", "auth": "a"}},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 503

    @patch("app.blueprints.push_routes.Config")
    def test_subscribe_missing_endpoint_returns_400(self, mock_config, client, auth_session):
        mock_config.PUSH_ENABLED = True
        resp = auth_session.post(
            "/push/subscribe",
            json={"keys": {"p256dh": "p", "auth": "a"}},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 400


class TestUnsubscribe:
    @patch("app.blueprints.push_routes.db")
    @patch("app.blueprints.push_routes.Config")
    def test_unsubscribe_deletes_all_user_subs(self, mock_config, mock_db, client, auth_session):
        mock_config.PUSH_ENABLED = True
        sub1, sub2 = MagicMock(), MagicMock()
        sub1.reference = MagicMock()
        sub2.reference = MagicMock()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub1, sub2]

        resp = auth_session.delete("/push/subscribe", headers={"X-CSRFToken": "test"})
        assert resp.status_code == 200
        assert sub1.reference.delete.called
        assert sub2.reference.delete.called


class TestDismissPrompt:
    @patch("app.blueprints.push_routes.db")
    def test_dismiss_prompt_writes_timestamp(self, mock_db, client, auth_session):
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref

        resp = auth_session.post("/push/dismiss-prompt", headers={"X-CSRFToken": "test"})
        assert resp.status_code == 200
        update_call = doc_ref.set.call_args
        assert "pushPromptDismissedAt" in update_call.args[0]


class TestDismissStatus:
    @patch("app.blueprints.push_routes.db")
    def test_dismiss_status_returns_false_when_unset(self, mock_db, client, auth_session):
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {}
        mock_db.collection.return_value.document.return_value.get.return_value = snap
        resp = client.get("/push/dismiss-status")
        assert resp.status_code == 200
        assert resp.get_json() == {"dismissedRecently": False}

    @patch("app.blueprints.push_routes.db")
    def test_dismiss_status_returns_true_when_recent(self, mock_db, client, auth_session):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"pushPromptDismissedAt": recent}
        mock_db.collection.return_value.document.return_value.get.return_value = snap
        resp = client.get("/push/dismiss-status")
        assert resp.get_json() == {"dismissedRecently": True}

    @patch("app.blueprints.push_routes.db")
    def test_dismiss_status_returns_false_when_stale(self, mock_db, client, auth_session):
        from datetime import datetime, timezone, timedelta
        stale = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"pushPromptDismissedAt": stale}
        mock_db.collection.return_value.document.return_value.get.return_value = snap
        resp = client.get("/push/dismiss-status")
        assert resp.get_json() == {"dismissedRecently": False}

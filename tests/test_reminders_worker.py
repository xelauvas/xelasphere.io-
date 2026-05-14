"""Reminders worker tests. The worker only touches Firestore + SendGrid;
both are mocked via patch seams (get_db + send_transactional_email)."""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


def _make_doc(doc_id, data):
    snap = MagicMock()
    snap.id = doc_id
    snap.to_dict.return_value = data
    snap.reference = MagicMock()
    return snap


class TestBuildEmailBody:
    def test_uses_instructions_when_present(self):
        from tools.reminders_worker import _build_email_body
        body = _build_email_body({"name": "title", "instructions": "do the thing", "fireAt": "2099-01-01T00:00:00+00:00"})
        assert "do the thing" in body
        assert "— XelaSphere Reminders" in body

    def test_falls_back_to_name_when_no_instructions(self):
        from tools.reminders_worker import _build_email_body
        body = _build_email_body({"name": "just title", "fireAt": "2099-01-01T00:00:00+00:00"})
        assert "just title" in body

    def test_includes_late_banner_when_delay_over_threshold(self):
        from tools.reminders_worker import _build_email_body
        late_fire_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        body = _build_email_body({"name": "x", "instructions": "body", "fireAt": late_fire_at})
        assert "delayed" in body.lower()

    def test_omits_late_banner_when_on_time(self):
        from tools.reminders_worker import _build_email_body
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        body = _build_email_body({"name": "x", "instructions": "body", "fireAt": fresh})
        assert "delayed" not in body.lower()


class TestFetchDue:
    @patch("tools.reminders_worker.get_db")
    def test_fetch_due_returns_scheduled_and_past(self, mock_get_db):
        from tools.reminders_worker import fetch_due_reminders
        db = MagicMock()
        mock_get_db.return_value = db

        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        doc = _make_doc("rem_1", {"status": "scheduled", "fireAt": past.isoformat(), "userId": "u1"})
        db.collection_group.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = [doc]

        results = fetch_due_reminders(limit=100)
        assert len(results) == 1
        assert results[0].id == "rem_1"


class TestClaimReminder:
    def test_claim_wins_when_status_scheduled(self):
        from tools.reminders_worker import try_claim_reminder
        doc_ref = MagicMock()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"status": "scheduled"}

        result = try_claim_reminder(
            doc_ref, _now_iso="2026-05-15T08:00:00+00:00", _fake_snap=snap,
        )
        assert result is True
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "sending"
        assert args[0]["lockedAt"] == "2026-05-15T08:00:00+00:00"

    def test_claim_loses_when_status_not_scheduled(self):
        from tools.reminders_worker import try_claim_reminder
        doc_ref = MagicMock()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"status": "sending"}

        result = try_claim_reminder(
            doc_ref, _now_iso="2026-05-15T08:00:00+00:00", _fake_snap=snap,
        )
        assert result is False
        doc_ref.update.assert_not_called()

    def test_claim_loses_when_doc_missing(self):
        from tools.reminders_worker import try_claim_reminder
        doc_ref = MagicMock()
        snap = MagicMock()
        snap.exists = False

        result = try_claim_reminder(
            doc_ref, _now_iso="2026-05-15T08:00:00+00:00", _fake_snap=snap,
        )
        assert result is False
        doc_ref.update.assert_not_called()


class TestProcessReminder:
    @patch("app.services.email_sender.SendGridAPIClient")
    def test_success_marks_delivered(self, mock_client_cls):
        from tools.reminders_worker import process_reminder
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=202, body=b"", headers={})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.t"
            cfg.SENDGRID_FROM_EMAIL = "n@e.io"
            cfg.SENDGRID_FROM_NAME = "T"
            doc_ref = MagicMock()
            data = {
                "name": "test", "instructions": "body", "userId": "u1",
                "status": "sending", "attemptCount": 0, "channels": ["email"],
                "fireAt": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
                "timezone": "Europe/London",
            }
            process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "delivered"
        assert "deliveredAt" in args[0]
        assert args[0]["attemptCount"] == 1

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_retryable_under_cap_reverts_to_scheduled(self, mock_client_cls):
        from tools.reminders_worker import process_reminder
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=503, body=b"down", headers={})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.t"
            cfg.SENDGRID_FROM_EMAIL = "n@e.io"
            cfg.SENDGRID_FROM_NAME = "T"
            doc_ref = MagicMock()
            data = {
                "name": "x", "userId": "u1", "status": "sending", "attemptCount": 0,
                "channels": ["email"], "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "UTC",
            }
            process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "scheduled"
        assert args[0]["attemptCount"] == 1
        assert args[0]["lockedAt"] is None

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_retryable_at_cap_marks_failed(self, mock_client_cls):
        from tools.reminders_worker import process_reminder
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=503, body=b"down", headers={})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.t"
            cfg.SENDGRID_FROM_EMAIL = "n@e.io"
            cfg.SENDGRID_FROM_NAME = "T"
            doc_ref = MagicMock()
            data = {
                "name": "x", "userId": "u1", "status": "sending", "attemptCount": 2,
                "channels": ["email"], "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "UTC",
            }
            process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "failed"
        assert args[0]["attemptCount"] == 3

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_non_retryable_marks_failed_immediately(self, mock_client_cls):
        from tools.reminders_worker import process_reminder
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=403, body=b"forbidden", headers={})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.t"
            cfg.SENDGRID_FROM_EMAIL = "n@e.io"
            cfg.SENDGRID_FROM_NAME = "T"
            doc_ref = MagicMock()
            data = {
                "name": "x", "userId": "u1", "status": "sending", "attemptCount": 0,
                "channels": ["email"], "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "UTC",
            }
            process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "failed"
        assert "403" in args[0]["lastError"]


class TestStuckLockSweep:
    @patch("tools.reminders_worker.get_db")
    def test_sweep_reverts_old_sending_to_scheduled(self, mock_get_db):
        from tools.reminders_worker import sweep_stuck_locks
        db = MagicMock()
        mock_get_db.return_value = db

        stuck = _make_doc("rem_stuck", {
            "status": "sending",
            "lockedAt": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        })
        db.collection_group.return_value.where.return_value.where.return_value.limit.return_value.stream.return_value = [stuck]

        count = sweep_stuck_locks(stale_after_seconds=300)
        assert count == 1
        args, _ = stuck.reference.update.call_args
        assert args[0]["status"] == "scheduled"
        assert args[0]["lockedAt"] is None

    @patch("tools.reminders_worker.get_db")
    def test_sweep_returns_zero_when_no_stuck_docs(self, mock_get_db):
        from tools.reminders_worker import sweep_stuck_locks
        db = MagicMock()
        mock_get_db.return_value = db
        db.collection_group.return_value.where.return_value.where.return_value.limit.return_value.stream.return_value = []
        assert sweep_stuck_locks() == 0


class TestMainSkipsWhenDisabled:
    @patch("tools.reminders_worker.get_db")
    def test_main_exits_zero_when_disabled(self, mock_get_db):
        from tools.reminders_worker import main
        with patch("app.config.Config") as cfg:
            cfg.REMINDERS_ENABLED = False
            rc = main([])
        assert rc == 0
        # Never even called Firestore
        mock_get_db.assert_not_called()

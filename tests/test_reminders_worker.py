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
    @patch("tools.reminders_worker._try_revert_stuck_lock")
    @patch("tools.reminders_worker.get_db")
    def test_sweep_calls_transactional_revert_for_each_stuck(self, mock_get_db, mock_revert):
        from tools.reminders_worker import sweep_stuck_locks
        db = MagicMock()
        mock_get_db.return_value = db
        mock_revert.return_value = True  # claim won

        stuck = _make_doc("rem_stuck", {
            "status": "sending",
            "lockedAt": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        })
        db.collection_group.return_value.where.return_value.where.return_value.limit.return_value.stream.return_value = [stuck]

        count = sweep_stuck_locks(stale_after_seconds=300)
        assert count == 1
        # Verify the transactional helper was called with the doc's ref
        mock_revert.assert_called_once()
        args, _ = mock_revert.call_args
        assert args[0] == stuck.reference

    @patch("tools.reminders_worker._try_revert_stuck_lock")
    @patch("tools.reminders_worker.get_db")
    def test_sweep_skips_when_revert_loses_race(self, mock_get_db, mock_revert):
        from tools.reminders_worker import sweep_stuck_locks
        db = MagicMock()
        mock_get_db.return_value = db
        mock_revert.return_value = False  # lost the race to another sweep

        stuck = _make_doc("rem_x", {"status": "sending", "lockedAt": "2020-01-01T00:00:00+00:00"})
        db.collection_group.return_value.where.return_value.where.return_value.limit.return_value.stream.return_value = [stuck]

        count = sweep_stuck_locks(stale_after_seconds=300)
        assert count == 0  # didn't win, don't count

    @patch("tools.reminders_worker.get_db")
    def test_sweep_returns_zero_when_no_stuck_docs(self, mock_get_db):
        from tools.reminders_worker import sweep_stuck_locks
        db = MagicMock()
        mock_get_db.return_value = db
        db.collection_group.return_value.where.return_value.where.return_value.limit.return_value.stream.return_value = []
        assert sweep_stuck_locks() == 0


class TestTryRevertStuckLock:
    def test_reverts_when_stale_sending(self):
        from tools.reminders_worker import _try_revert_stuck_lock
        doc_ref = MagicMock()
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {
            "status": "sending",
            "lockedAt": "2020-01-01T00:00:00+00:00",  # ancient
        }
        ok = _try_revert_stuck_lock(doc_ref, "2099-01-01T00:00:00+00:00", _fake_snap=snap)
        assert ok is True
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "scheduled"
        assert args[0]["lockedAt"] is None

    def test_skips_when_status_already_changed(self):
        from tools.reminders_worker import _try_revert_stuck_lock
        doc_ref = MagicMock()
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {"status": "delivered", "lockedAt": None}
        ok = _try_revert_stuck_lock(doc_ref, "2099-01-01T00:00:00+00:00", _fake_snap=snap)
        assert ok is False
        doc_ref.update.assert_not_called()

    def test_skips_when_lockedAt_not_stale(self):
        from tools.reminders_worker import _try_revert_stuck_lock
        doc_ref = MagicMock()
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = {
            "status": "sending",
            "lockedAt": "2099-01-01T00:00:00+00:00",  # newer than cutoff
        }
        ok = _try_revert_stuck_lock(doc_ref, "2020-01-01T00:00:00+00:00", _fake_snap=snap)
        assert ok is False
        doc_ref.update.assert_not_called()


def test_process_reminder_advances_recurring_after_delivery(monkeypatch):
    """Recurring reminder transitions to scheduled with advanced fireAt
    instead of terminal delivered. Gated on REMINDERS_RECURRENCE_ENABLED."""
    from tools import reminders_worker
    from app.config import Config

    # Force flag on regardless of env
    monkeypatch.setattr(Config, "REMINDERS_RECURRENCE_ENABLED", True)

    # Mock SendGrid success
    from app.services import email_sender
    class _OkResult:
        status_code = 202
    monkeypatch.setattr(
        email_sender, "send_transactional_email",
        lambda **kw: _OkResult(),
    )

    # Capture updates to the doc
    updates = []
    class _FakeDocRef:
        def update(self, data): updates.append(data)
    doc_ref = _FakeDocRef()

    reminder = {
        "id": "rid_abc",
        "name": "Daily standup",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "timezone": "Europe/London",
        "recurrence": "DAILY",
        "attemptCount": 0,
        "deliveryCount": 0,
        "userId": "u1",
        "sourceChatId": "session_x",
    }

    reminders_worker.process_reminder(doc_ref, reminder, user_email="a@b.com")

    # The last update should be the success/advance, not a failure
    assert len(updates) >= 1
    final = updates[-1]
    assert final["status"] == "scheduled", (
        f"Expected status=scheduled after recurring delivery; got {final}"
    )
    assert "fireAt" in final, f"Recurring deliveries must advance fireAt; got {final}"
    assert final["fireAt"] == "2026-05-20T08:00:00+00:00"
    assert final["deliveryCount"] == 1
    assert final["lastFiredAt"] is not None
    assert final["snoozedUntil"] is None
    assert final["lockedAt"] is None


def test_process_reminder_skips_when_snoozed_in_future(monkeypatch):
    """A claimed reminder with snoozedUntil in the future releases the
    lock and skips. SendGrid is never called."""
    from tools import reminders_worker
    from datetime import datetime, timezone, timedelta

    # Track whether SendGrid was called
    sg_called = []
    from app.services import email_sender
    monkeypatch.setattr(
        email_sender, "send_transactional_email",
        lambda **kw: sg_called.append(kw) or type("R", (), {"status_code": 202})(),
    )

    updates = []
    class _FakeDocRef:
        def update(self, data): updates.append(data)
    doc_ref = _FakeDocRef()

    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    reminder = {
        "id": "rid_x", "name": "snoozed", "userId": "u1",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "snoozedUntil": future,
        "recurrence": None, "attemptCount": 0, "deliveryCount": 0,
        "sourceChatId": None, "timezone": "UTC",
    }

    reminders_worker.process_reminder(doc_ref, reminder, user_email="a@b.com")

    # Assert: SendGrid was NOT called
    assert sg_called == [], "Expected snoozed reminder to skip send"
    # Assert: doc was updated to release the claim
    assert len(updates) >= 1
    release_update = updates[-1]
    assert release_update.get("status") == "scheduled"
    assert release_update.get("lockedAt") is None


def test_process_reminder_proceeds_when_snoozed_in_past(monkeypatch):
    """A reminder with snoozedUntil in the past (already elapsed) delivers
    normally — snooze is a one-time override."""
    from tools import reminders_worker
    from datetime import datetime, timezone, timedelta

    sg_called = []
    from app.services import email_sender
    class _OkResult:
        status_code = 202
    monkeypatch.setattr(
        email_sender, "send_transactional_email",
        lambda **kw: sg_called.append(kw) or _OkResult(),
    )

    updates = []
    class _FakeDocRef:
        def update(self, data): updates.append(data)
    doc_ref = _FakeDocRef()

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    reminder = {
        "id": "rid_x", "name": "snooze-elapsed", "userId": "u1",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "snoozedUntil": past,
        "recurrence": None, "attemptCount": 0, "deliveryCount": 0,
        "sourceChatId": None, "timezone": "UTC",
    }

    reminders_worker.process_reminder(doc_ref, reminder, user_email="a@b.com")

    assert len(sg_called) == 1, "Expected delivery to proceed when snooze elapsed"
    # And the doc transitioned to delivered (terminal one-off)
    final = updates[-1]
    assert final.get("status") == "delivered"


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


def test_email_html_includes_snooze_block_when_enabled(monkeypatch):
    from tools.reminders_worker import _build_email_html
    from app.config import Config
    monkeypatch.setattr(Config, "REMINDERS_SNOOZE_ENABLED", True)
    # Make sure SECRET_KEY exists for token signing
    if not getattr(Config, "SECRET_KEY", None):
        monkeypatch.setattr(Config, "SECRET_KEY", "test-secret-for-tokens")

    reminder = {
        "id": "rid_x", "name": "test", "instructions": "body",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "timezone": "UTC", "userId": "u1",
        "sourceChatId": "session_abc",
    }
    html = _build_email_html(reminder)
    assert "Snooze 10 min" in html
    assert "Snooze 1 hour" in html
    assert "Snooze until tomorrow" in html
    # Each link points to /reminders/rid_x/snooze with dur + token
    assert "/reminders/rid_x/snooze?dur=10m&token=" in html
    assert "/reminders/rid_x/snooze?dur=1h&token=" in html
    assert "/reminders/rid_x/snooze?dur=tomorrow&token=" in html


def test_email_html_omits_snooze_block_when_disabled(monkeypatch):
    from tools.reminders_worker import _build_email_html
    from app.config import Config
    monkeypatch.setattr(Config, "REMINDERS_SNOOZE_ENABLED", False)

    reminder = {
        "id": "rid_x", "name": "test", "instructions": "body",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "timezone": "UTC", "userId": "u1",
        "sourceChatId": "session_abc",
    }
    html = _build_email_html(reminder)
    assert "Snooze" not in html


def test_email_text_includes_snooze_urls_when_enabled(monkeypatch):
    from tools.reminders_worker import _build_email_body
    from app.config import Config
    monkeypatch.setattr(Config, "REMINDERS_SNOOZE_ENABLED", True)
    if not getattr(Config, "SECRET_KEY", None):
        monkeypatch.setattr(Config, "SECRET_KEY", "test-secret-for-tokens")

    reminder = {
        "id": "rid_x", "name": "test", "instructions": "body",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "timezone": "UTC", "userId": "u1",
        "sourceChatId": None,
    }
    text = _build_email_body(reminder)
    assert "Snooze 10 min" in text
    assert "/reminders/rid_x/snooze?dur=10m&token=" in text

import pytest
from datetime import datetime, timezone
from app.services.reminder_model import (
    STATUS_SCHEDULED, STATUS_SENDING, STATUS_PAUSED,
    STATUS_DELIVERED, STATUS_FAILED, STATUS_CANCELLED,
    ALL_STATUSES,
    new_reminder_dict, validate_reminder_payload, compute_fire_at_utc,
)


class TestReminderModel:
    def test_statuses_exposed(self):
        assert STATUS_SCHEDULED == "scheduled"
        assert STATUS_DELIVERED == "delivered"
        assert "failed" in ALL_STATUSES

    def test_new_reminder_dict_defaults(self):
        d = new_reminder_dict(
            user_id="u1",
            name="Call mom",
            instructions="",
            fire_at_utc=datetime(2099, 5, 15, 8, 0, tzinfo=timezone.utc),
            timezone_str="Europe/London",
        )
        assert d["userId"] == "u1"
        assert d["name"] == "Call mom"
        assert d["status"] == STATUS_SCHEDULED
        assert d["attemptCount"] == 0
        assert d["channels"] == ["email"]
        assert d["createdBy"] == "user"
        assert d["lockedAt"] is None

    def test_validate_payload_requires_name(self):
        ok, err = validate_reminder_payload({"fireAtLocal": "2099-01-01T09:00", "timezone": "Europe/London"})
        assert not ok
        assert "name" in err.lower()

    def test_validate_payload_requires_fire_at_local(self):
        ok, err = validate_reminder_payload({"name": "x", "timezone": "Europe/London"})
        assert not ok

    def test_validate_payload_requires_timezone(self):
        ok, err = validate_reminder_payload({"name": "x", "fireAtLocal": "2099-01-01T09:00"})
        assert not ok

    def test_validate_payload_rejects_past_fire_time(self):
        ok, err = validate_reminder_payload({
            "name": "x",
            "fireAtLocal": "2020-01-01T00:00",
            "timezone": "Europe/London",
        })
        assert not ok
        assert "past" in err.lower() or "future" in err.lower()

    def test_validate_payload_accepts_valid(self):
        ok, err = validate_reminder_payload({
            "name": "x",
            "fireAtLocal": "2099-01-01T09:00",
            "timezone": "Europe/London",
        })
        assert ok
        assert err is None

    def test_compute_fire_at_utc_london_bst(self):
        # 2026-05-15 09:00 London is BST (UTC+1) → 08:00 UTC
        fire_at = compute_fire_at_utc("2026-05-15T09:00:00", "Europe/London")
        assert fire_at.hour == 8
        assert fire_at.tzinfo == timezone.utc

    def test_compute_fire_at_utc_already_aware(self):
        # If input is tz-aware ISO, just convert
        fire_at = compute_fire_at_utc("2026-05-15T09:00:00+01:00", "Europe/London")
        assert fire_at.hour == 8
        assert fire_at.tzinfo == timezone.utc

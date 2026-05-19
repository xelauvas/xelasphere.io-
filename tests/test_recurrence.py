"""Unit tests for app.services.recurrence.next_fire_at.

Pure function — no Firestore, no mocks. Each test asserts the UTC ISO
output for a known input. Includes DST + calendar edge cases."""
import pytest
from datetime import datetime, timezone
from app.services.recurrence import next_fire_at, RECURRENCE_PRESETS


def _r(rec, fire_at_iso, tz="Europe/London"):
    return {"recurrence": rec, "fireAt": fire_at_iso, "timezone": tz}


class TestPresetAdvance:
    def test_daily_advances_one_day(self):
        r = _r("DAILY", "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-05-20T08:00:00+00:00"

    def test_weekly_advances_seven_days(self):
        r = _r("WEEKLY", "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-05-26T08:00:00+00:00"

    def test_monthly_advances_one_month(self):
        # IMPORTANT: Both May and June 2026 are in BST (UTC+1).
        # May 19 08:00 UTC = May 19 09:00 BST.
        # +1 month in local time = June 19 09:00 BST = June 19 08:00 UTC.
        # So expected UTC = 08:00, NOT 07:00. Verify by running the snippet
        # below before asserting — the plan author left a stale value.
        r = _r("MONTHLY", "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-06-19T08:00:00+00:00"

    def test_yearly_advances_one_year(self):
        r = _r("YEARLY", "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2027-05-19T08:00:00+00:00"

    def test_unknown_preset_returns_none(self):
        r = _r("WEIRD", "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r) is None

    def test_no_recurrence_returns_none(self):
        r = _r(None, "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r) is None

    def test_missing_fire_at_returns_none(self):
        r = {"recurrence": "DAILY", "timezone": "Europe/London"}
        assert next_fire_at(r) is None

    def test_presets_constant_includes_five_values(self):
        assert RECURRENCE_PRESETS == {"DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY", "YEARLY"}

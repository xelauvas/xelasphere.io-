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


class TestEdgeCases:
    def test_weekdays_friday_skips_to_monday(self):
        # 2026-05-22 is Friday. Next weekday = Monday 2026-05-25.
        r = _r("WEEKDAYS", "2026-05-22T08:00:00+00:00")
        result = next_fire_at(r)
        # May 22 BST = 09:00 local. May 25 BST = 09:00 local = 08:00 UTC.
        assert result.isoformat() == "2026-05-25T08:00:00+00:00"

    def test_weekdays_thursday_advances_to_friday(self):
        # 2026-05-21 is Thursday. Next weekday = Friday 2026-05-22.
        r = _r("WEEKDAYS", "2026-05-21T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-05-22T08:00:00+00:00"

    def test_daily_dst_spring_forward_london(self):
        # 2026-03-28 09:00 London = GMT = 09:00 UTC (DST starts 2026-03-29 at 01:00 UTC).
        # 2026-03-29 09:00 London = BST = 08:00 UTC. Wall clock preserved.
        r = _r("DAILY", "2026-03-28T09:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-03-29T08:00:00+00:00"

    def test_daily_dst_fall_back_london(self):
        # 2026-10-24 09:00 London = BST = 08:00 UTC.
        # 2026-10-25 09:00 London = GMT = 09:00 UTC (DST ends 2026-10-25 at 01:00 UTC).
        r = _r("DAILY", "2026-10-24T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-10-25T09:00:00+00:00"

    def test_monthly_31st_falls_back_to_last_day(self):
        # 2026-01-31 (31-day month) → 2026-02-28 (last day of Feb in 2026, non-leap).
        r = _r("MONTHLY", "2026-01-31T09:00:00+00:00", tz="UTC")
        assert next_fire_at(r).isoformat() == "2026-02-28T09:00:00+00:00"

    def test_yearly_feb29_falls_back_to_feb28_in_non_leap(self):
        # 2024 was a leap year; 2025 is not.
        r = _r("YEARLY", "2024-02-29T09:00:00+00:00", tz="UTC")
        assert next_fire_at(r).isoformat() == "2025-02-28T09:00:00+00:00"

    def test_unparseable_fire_at_returns_none(self):
        r = {"recurrence": "DAILY", "fireAt": "not-a-date", "timezone": "UTC"}
        assert next_fire_at(r) is None

    def test_unknown_timezone_falls_back_to_utc(self):
        r = _r("DAILY", "2026-05-19T08:00:00+00:00", tz="Mars/Olympus_Mons")
        # Should not raise; falls back to UTC handling. Result = +1 day in UTC.
        assert next_fire_at(r).isoformat() == "2026-05-20T08:00:00+00:00"

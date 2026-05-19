"""Recurrence preset arithmetic for reminders.

Pure function — given a reminder doc with `recurrence`, `fireAt` (UTC ISO),
and `timezone` (IANA), returns the next UTC fireAt. Operates in local time
so DST and calendar edge cases (Feb 29, month-end on 31st) are correct.

This is M1.1. The five presets cover ~95% of use. Custom RRULE is deferred
to M5+ per the spec."""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

RECURRENCE_PRESETS = {"DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY", "YEARLY"}


def next_fire_at(reminder: dict) -> Optional[datetime]:
    """Compute the next UTC fireAt for a recurring reminder.

    Returns None when:
      - reminder is not recurring (`recurrence` is None or unknown preset)
      - `fireAt` is missing or unparseable

    Local-time arithmetic preserves wall-clock semantics across DST. For
    edge cases:
      - WEEKDAYS: skip Sat/Sun to the next Monday.
      - MONTHLY on the 31st in a 30-day month: dateutil falls back to last day.
      - YEARLY on Feb 29 in a non-leap year: dateutil falls back to Feb 28.
    """
    recurrence = reminder.get("recurrence")
    if recurrence not in RECURRENCE_PRESETS:
        return None

    fire_at_iso = reminder.get("fireAt")
    if not fire_at_iso:
        return None

    try:
        fire_at_utc = datetime.fromisoformat(fire_at_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if fire_at_utc.tzinfo is None:
        fire_at_utc = fire_at_utc.replace(tzinfo=dt_timezone.utc)

    tz_name = reminder.get("timezone") or "UTC"
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo("UTC")

    local = fire_at_utc.astimezone(local_tz)

    if recurrence == "DAILY":
        next_local = local + relativedelta(days=1)
    elif recurrence == "WEEKDAYS":
        next_local = local + relativedelta(days=1)
        while next_local.weekday() >= 5:  # 5=Sat, 6=Sun
            next_local = next_local + relativedelta(days=1)
    elif recurrence == "WEEKLY":
        next_local = local + relativedelta(weeks=1)
    elif recurrence == "MONTHLY":
        next_local = local + relativedelta(months=1)
    elif recurrence == "YEARLY":
        next_local = local + relativedelta(years=1)
    else:
        return None

    return next_local.astimezone(dt_timezone.utc)

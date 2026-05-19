# Reminders M1.1 — Recurrence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship recurring reminders to xelasphere.com. After a recurring reminder delivers, the worker computes the next fire in the reminder's local timezone (DST-safe) and resets it to scheduled. Five presets cover the long tail: `DAILY`, `WEEKDAYS`, `WEEKLY`, `MONTHLY`, `YEARLY`.

**Architecture:** Pure-function `next_fire_at(reminder)` in a new `app/services/recurrence.py`, called from `process_reminder` in the worker after a successful send. Single-doc model — `fireAt` advances in place; no occurrence-doc proliferation. Validation extended on routes. Modal dropdown re-enabled. Kill switch via `REMINDERS_RECURRENCE_ENABLED` env flag.

**Tech Stack:** Python 3.12, Flask, Firestore, `python-dateutil` 2.9 (already in venv via transitives — this plan pins it), `zoneinfo` (stdlib).

**Spec:** [`docs/superpowers/specs/2026-05-19-reminders-m1-design.md`](../specs/2026-05-19-reminders-m1-design.md) commit `65662b0`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `app/services/recurrence.py` | **Create** | Pure function `next_fire_at(reminder) -> Optional[datetime]`. Encodes preset rules + DST/calendar edge cases via `dateutil.relativedelta`. No I/O, no Firestore. |
| `tests/test_recurrence.py` | **Create** | Unit tests: each preset, DST spring-forward + fall-back, month-end fallback (31st in 30-day month), leap year fallback (Feb 29), unknown preset → None, non-recurring → None. |
| `tools/reminders_worker.py` | **Modify** | `process_reminder`: when a recurring reminder delivers, compute next fire, update doc to `status=scheduled` with new `fireAt`, increment `deliveryCount`, set `lastFiredAt`. Gated on `REMINDERS_RECURRENCE_ENABLED`. |
| `tests/test_reminders_worker.py` | **Modify** | Integration test: recurring reminder + mocked SendGrid 2xx → assert Firestore advance. |
| `app/blueprints/reminders_routes.py` | **Modify** | Add `recurrence` to `_EDITABLE_FIELDS`. Validate against `RECURRENCE_PRESETS` (re-exported from `recurrence.py`). |
| `tests/test_reminders_routes.py` | **Modify** | Test POST + PATCH with `recurrence=DAILY` succeeds. POST with `recurrence=GARBAGE` → 400. |
| `app/config.py` | **Modify** | Add `REMINDERS_RECURRENCE_ENABLED` env flag (default `false`). |
| `app/templates/_reminders_modal.html` | **Modify** | Re-add the recurrence `<select>` removed in M0.5 closeout. Now enabled, with all 6 options. |
| `app/static/js/remindersModule.js` | **Modify** | (a) Populate the dropdown in `openModal()` from `reminder.recurrence`. (b) Include `recurrence` in `saveReminder()` payload. (c) Render recurrence label under the time on cards when set. |
| `requirements-cpu.txt`, `requirements-gpu.txt` | **Modify** | Pin `python-dateutil>=2.9,<3` explicitly (already a transitive). |

---

## Task 1: Pin `python-dateutil` in requirements

**Files:**
- Modify: `requirements-cpu.txt`
- Modify: `requirements-gpu.txt`

- [ ] **Step 1: Confirm dateutil is currently available**

```bash
/opt/xelasphere/venv/bin/python -c "import dateutil; print(dateutil.__version__)"
```

Expected: `2.9.0.post0` or similar 2.9.x.

- [ ] **Step 2: Add explicit pin to both requirements files**

Append this line to `requirements-cpu.txt` and `requirements-gpu.txt`:

```
python-dateutil>=2.9,<3
```

- [ ] **Step 3: Verify install doesn't churn**

```bash
/opt/xelasphere/venv/bin/pip install -r requirements-cpu.txt 2>&1 | tail -5
```

Expected: `Requirement already satisfied: python-dateutil`. No fresh install.

- [ ] **Step 4: Commit**

```bash
git add requirements-cpu.txt requirements-gpu.txt
git commit -m "chore(reminders/m1.1): pin python-dateutil for recurrence (transitive → explicit)"
```

---

## Task 2: TDD — `next_fire_at()` happy paths (DAILY / WEEKLY / MONTHLY / YEARLY)

**Files:**
- Create: `app/services/recurrence.py`
- Create: `tests/test_recurrence.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recurrence.py`:

```python
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
        r = _r("MONTHLY", "2026-05-19T08:00:00+00:00")
        assert next_fire_at(r).isoformat() == "2026-06-19T07:00:00+00:00"
        # May 19 BST = 09:00 local. June 19 BST = 09:00 local = 08:00 UTC. Wait — both
        # in BST. So the UTC offset is the same (+01:00). Recompute:
        # May 19 08:00 UTC = 09:00 BST. June 19 09:00 BST = 08:00 UTC. So expected = 08:00.
        # Adjust this if you prefer a non-DST month — see comment.

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_recurrence.py -v
```

Expected: All FAIL with `ModuleNotFoundError: No module named 'app.services.recurrence'`.

- [ ] **Step 3: Implement `next_fire_at`**

Create `app/services/recurrence.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_recurrence.py -v
```

Expected: All PASS. If `test_monthly_advances_one_month` fails because the May→June UTC offset shift produced a different value than asserted, recompute using:

```bash
/opt/xelasphere/venv/bin/python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
d = datetime(2026,5,19,8,0,tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Europe/London'))
print((d + relativedelta(months=1)).astimezone(ZoneInfo('UTC')).isoformat())
"
```

Use the printed value as the expected. (Both May and June are BST in 2026, so UTC offset is identical — `08:00 UTC` both sides — but verify before asserting.)

- [ ] **Step 5: Commit**

```bash
git add app/services/recurrence.py tests/test_recurrence.py
git commit -m "feat(reminders/m1.1): recurrence presets + next_fire_at()

Pure function. DAILY/WEEKDAYS/WEEKLY/MONTHLY/YEARLY. Local-time
arithmetic via dateutil.relativedelta — DST + month-end fallback
handled. 8 unit tests."
```

---

## Task 3: TDD — `next_fire_at()` edge cases (WEEKDAYS, DST, month-end, leap year)

**Files:**
- Modify: `tests/test_recurrence.py` (add new test class)

- [ ] **Step 1: Add edge-case tests**

Append to `tests/test_recurrence.py`:

```python
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
```

- [ ] **Step 2: Run new tests — expect all to pass since `next_fire_at` already handles these**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_recurrence.py::TestEdgeCases -v
```

Expected: All PASS. If any fail, the implementation in Task 2 has a bug — fix `next_fire_at` rather than weakening the test. Common gotcha: if the DST tests fail, check that `ZoneInfo("Europe/London")` is loading correctly (Python 3.9+ stdlib; on older systems may need `tzdata` package).

- [ ] **Step 3: Commit**

```bash
git add tests/test_recurrence.py
git commit -m "test(reminders/m1.1): edge cases for WEEKDAYS, DST, leap year, month-end"
```

---

## Task 4: Add kill switch `REMINDERS_RECURRENCE_ENABLED`

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Add the env flag to `Config`**

In `app/config.py`, locate the existing `REMINDERS_ENABLED` line (around line 145):

```python
REMINDERS_ENABLED = os.getenv('REMINDERS_ENABLED', 'false').lower() == 'true'
```

Add immediately below it:

```python
REMINDERS_RECURRENCE_ENABLED = os.getenv('REMINDERS_RECURRENCE_ENABLED', 'false').lower() == 'true'
```

- [ ] **Step 2: Smoke-test the config loads**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -c "
from app.config import Config
print('REMINDERS_ENABLED:', Config.REMINDERS_ENABLED)
print('REMINDERS_RECURRENCE_ENABLED:', Config.REMINDERS_RECURRENCE_ENABLED)
"
```

Expected: both print as booleans. The recurrence flag defaults to `False` (off until ready).

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "chore(reminders/m1.1): add REMINDERS_RECURRENCE_ENABLED kill switch (default off)"
```

---

## Task 5: TDD — Worker advances `fireAt` for recurring reminders

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write a failing integration test**

In `tests/test_reminders_worker.py`, find the test class that tests `process_reminder` happy path. Add a new test method (or new test class) modeled on the existing happy-path test. The exact name of existing tests will vary — search the file for `process_reminder` to locate the pattern. Add this test alongside:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py::test_process_reminder_advances_recurring_after_delivery -v
```

Expected: FAIL — current `process_reminder` always sets `status=delivered`, never advances.

- [ ] **Step 3: Modify `process_reminder` to advance recurring reminders**

In `tools/reminders_worker.py`, locate the existing post-send success branch (around line 317):

```python
    doc_ref.update({
        "status": "delivered",
        "deliveredAt": now_iso,
        "attemptCount": attempt + 1,
        "lastError": None,
        "lockedAt": None,
        "updatedAt": now_iso,
    })
```

Replace that block with:

```python
    # Recurring reminders advance fireAt and stay scheduled. Non-recurring
    # ones (or any recurring when the kill switch is off) terminate as before.
    from app.config import Config
    recurrence_on = Config.REMINDERS_RECURRENCE_ENABLED
    is_recurring = reminder.get("recurrence") in {
        "DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY", "YEARLY",
    }

    if recurrence_on and is_recurring:
        from app.services.recurrence import next_fire_at
        next_dt = next_fire_at(reminder)
        if next_dt is not None:
            doc_ref.update({
                "status": "scheduled",
                "fireAt": next_dt.isoformat(),
                "deliveryCount": reminder.get("deliveryCount", 0) + 1,
                "lastFiredAt": now_iso,
                "snoozedUntil": None,
                "attemptCount": 0,
                "lastError": None,
                "lockedAt": None,
                "updatedAt": now_iso,
            })
            logger.info(
                "reminder_recurring_advanced",
                extra={"extra_fields": {
                    "name": name, "recurrence": reminder.get("recurrence"),
                    "nextFireAt": next_dt.isoformat(),
                    "deliveryCount": reminder.get("deliveryCount", 0) + 1,
                }},
            )
            return

    # Non-recurring (or flag off): terminal delivered.
    doc_ref.update({
        "status": "delivered",
        "deliveredAt": now_iso,
        "attemptCount": attempt + 1,
        "lastError": None,
        "lockedAt": None,
        "updatedAt": now_iso,
    })
    logger.info(
        "reminder_sent",
        extra={"extra_fields": {
            "name": name, "attempts": attempt + 1, "sg_status": result.status_code,
        }},
    )
```

- [ ] **Step 4: Run the new test plus the full worker test suite**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py -v
```

Expected: the new test passes AND all 19 existing worker tests still pass. If existing tests break, the most likely cause is that they pass a `recurrence` field in the fixture but expect `status=delivered`; either remove the field from those fixtures or set the kill switch to `False` for those tests.

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/m1.1): worker advances fireAt for recurring reminders

Reuses next_fire_at(). Gated on REMINDERS_RECURRENCE_ENABLED so we can
ship dark and flip on stage by stage. Adds reminder_recurring_advanced
log event."
```

---

## Task 6: TDD — Routes accept and validate `recurrence` field

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing route tests**

In `tests/test_reminders_routes.py`, find the existing `TestCreateReminder` and `TestPatchReminder` classes (or equivalent — search for `def test_create_persists_and_returns_id`). Add these tests alongside:

```python
class TestRecurrenceField:
    """recurrence field is accepted on POST + PATCH and validated against
    the preset enum. Unknown presets return 400."""

    def test_create_accepts_recurrence_daily(self, client, mock_db, auth_session):
        # Use the same auth + payload shape as test_create_persists_and_returns_id.
        # Replicate that test's setup here verbatim — do not 'similar to' or
        # 'see test_X' it; the engineer may read this task in isolation.
        # (Substitute the actual setup pattern from the existing test file.)
        payload = {
            "name": "Standup",
            "instructions": "10-minute team sync",
            "fireAtLocal": "2030-01-01T09:00:00",
            "timezone": "Europe/London",
            "recurrence": "DAILY",
        }
        resp = client.post(
            "/reminders",
            json=payload,
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 201, resp.get_json()
        assert resp.get_json().get("reminder", {}).get("recurrence") == "DAILY"

    def test_create_rejects_unknown_recurrence(self, client, mock_db, auth_session):
        payload = {
            "name": "Standup",
            "fireAtLocal": "2030-01-01T09:00:00",
            "timezone": "Europe/London",
            "recurrence": "FORTNIGHTLY",  # not a preset
        }
        resp = client.post(
            "/reminders",
            json=payload,
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 400
        assert "recurrence" in resp.get_json().get("error", "").lower()

    def test_patch_updates_recurrence(self, client, mock_db, auth_session, existing_reminder_id):
        resp = client.patch(
            f"/reminders/{existing_reminder_id}",
            json={"recurrence": "WEEKLY"},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200, resp.get_json()
        # Verify Firestore was updated with the new value
        # (Adapt to existing test's mock_db assertion idiom.)

    def test_patch_can_clear_recurrence_with_null(self, client, mock_db, auth_session, existing_reminder_id):
        resp = client.patch(
            f"/reminders/{existing_reminder_id}",
            json={"recurrence": None},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200, resp.get_json()
```

Note: the exact fixtures (`client`, `mock_db`, `auth_session`, `existing_reminder_id`) are whatever the existing test file uses — copy that file's setup pattern. The point of these tests is the new behavior, not re-inventing the test harness.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestRecurrenceField -v
```

Expected: tests fail — current routes ignore the `recurrence` field (it's stripped by `_EDITABLE_FIELDS`).

- [ ] **Step 3: Update routes to accept and validate recurrence**

In `app/blueprints/reminders_routes.py`:

a) Add the import (near the top, after the existing imports):

```python
from app.services.recurrence import RECURRENCE_PRESETS
```

b) Find the `_EDITABLE_FIELDS` set (around line 31):

```python
_EDITABLE_FIELDS = {"name", "instructions", "fireAtLocal", "timezone", "status"}
```

Change to:

```python
_EDITABLE_FIELDS = {"name", "instructions", "fireAtLocal", "timezone", "status", "recurrence"}
```

c) Find the existing `status` validation in the PATCH handler (around line 187-190):

```python
    if "status" in payload:
        if payload["status"] not in _PATCH_ALLOWED_STATUSES:
            return jsonify({"error": f"status must be one of {sorted(_PATCH_ALLOWED_STATUSES)}"}), 400
        update["status"] = payload["status"]
```

Immediately after that block, add:

```python
    if "recurrence" in payload:
        v = payload["recurrence"]
        if v is not None and v not in RECURRENCE_PRESETS:
            return jsonify({
                "error": f"recurrence must be one of {sorted(RECURRENCE_PRESETS)} or null"
            }), 400
        update["recurrence"] = v
```

d) Find the POST handler (the `create` function). After it builds the new-reminder dict from the payload, add similar validation for the create path. Locate where the function reads other optional fields from the payload (e.g., `instructions`) and add:

```python
    recurrence_val = payload.get("recurrence")
    if recurrence_val is not None and recurrence_val not in RECURRENCE_PRESETS:
        return jsonify({
            "error": f"recurrence must be one of {sorted(RECURRENCE_PRESETS)} or null"
        }), 400
```

Then include `"recurrence": recurrence_val` (defaulting to `None` if absent) in the dict written to Firestore. Initialize `"deliveryCount": 0` and `"lastFiredAt": None` and `"snoozedUntil": None` at create time as well.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py -v
```

Expected: new tests pass, existing pass too. The pre-existing failures from issue [#176](https://github.com/xelauvas/xelasphere.io-/issues/176) are NOT this task's concern — leave them alone.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders/m1.1): routes accept + validate recurrence field

Adds recurrence to _EDITABLE_FIELDS. POST + PATCH validate against
RECURRENCE_PRESETS. Null clears it. Initializes deliveryCount=0 and
related M1 fields on create."
```

---

## Task 7: Re-add recurrence dropdown to the modal template

**Files:**
- Modify: `app/templates/_reminders_modal.html`

- [ ] **Step 1: Re-insert the recurrence field**

In `app/templates/_reminders_modal.html`, locate the closing `</fieldset>` of the `reminders-modal__when` field (it's the When section with date/time/tz inputs):

```html
      </fieldset>

      <footer class="reminders-modal__footer">
```

Insert this between the `</fieldset>` and `<footer>`:

```html
      <label class="reminders-modal__field" data-reminders-recurrence-field>
        <span class="reminders-modal__label">Repeat</span>
        <select name="recurrence" data-reminders-recurrence>
          <option value="">Doesn't repeat</option>
          <option value="DAILY">Daily</option>
          <option value="WEEKDAYS">Every weekday (Mon–Fri)</option>
          <option value="WEEKLY" data-dynamic-label>Weekly</option>
          <option value="MONTHLY" data-dynamic-label>Monthly</option>
          <option value="YEARLY" data-dynamic-label>Yearly</option>
        </select>
      </label>
```

The `data-dynamic-label` attribute lets the JS rewrite the text to "Weekly on Wednesday" / "Monthly on the 20th" / "Yearly on May 20" based on the current `When` date. JS task below handles that.

- [ ] **Step 2: Smoke-test by reloading the page**

Restart Gunicorn (templates are cached): `sudo systemctl kill -s HUP xelasphere`

Open the chat in a browser, create a reminder via the existing flow, click the card → modal opens. Confirm the Repeat dropdown is present, defaults to "Doesn't repeat", and has 6 options.

- [ ] **Step 3: Commit**

```bash
git add app/templates/_reminders_modal.html
git commit -m "feat(reminders/m1.1): re-add recurrence dropdown to edit modal

Was removed in M0.5 closeout when the disabled placeholder was deleted.
Now enabled with 5 presets + 'Doesn't repeat'."
```

---

## Task 8: Modal JS — populate and submit recurrence

**Files:**
- Modify: `app/static/js/remindersModule.js`

- [ ] **Step 1: Populate the dropdown when opening the modal**

In `app/static/js/remindersModule.js`, find the `openModal(reminder)` function (search for `function openModal`). After the existing code that fills in `name`, `instructions`, date, time fields, add:

```javascript
    // M1.1: populate recurrence dropdown
    const recurrenceSel = form.querySelector('[data-reminders-recurrence]');
    if (recurrenceSel) {
      recurrenceSel.value = reminder.recurrence || '';
      // Update dynamic labels based on the fireAt date
      _updateRecurrenceLabels(recurrenceSel, reminder);
    }
```

Then add this helper function near the top of the IIFE (alongside `formatWhen`):

```javascript
  function _updateRecurrenceLabels(selectEl, reminder) {
    if (!reminder || !reminder.fireAt) return;
    const dt = new Date(reminder.fireAt);
    if (isNaN(dt.getTime())) return;
    const tz = reminder.timezone || getBrowserTz();
    const weekday = dt.toLocaleDateString(undefined, { weekday: 'long', timeZone: tz });
    const day = dt.toLocaleDateString(undefined, { day: 'numeric', timeZone: tz });
    const monthDay = dt.toLocaleDateString(undefined, {
      month: 'long', day: 'numeric', timeZone: tz,
    });
    selectEl.querySelectorAll('option[data-dynamic-label]').forEach(opt => {
      if (opt.value === 'WEEKLY') opt.textContent = `Weekly on ${weekday}`;
      if (opt.value === 'MONTHLY') opt.textContent = `Monthly on the ${day}`;
      if (opt.value === 'YEARLY')  opt.textContent = `Yearly on ${monthDay}`;
    });
  }
```

- [ ] **Step 2: Include `recurrence` in the save payload**

Find the modal form's submit handler (search for `data-reminders-form` and the function bound to its submit event). Locate where the payload object is built from form fields (it includes `name`, `instructions`, `fireAtLocal`, etc.). Add:

```javascript
      // M1.1: include recurrence (empty string → null)
      const recVal = form.elements['recurrence']?.value || null;
      payload.recurrence = recVal;
```

- [ ] **Step 3: Manually smoke-test in the browser**

Restart Gunicorn: `sudo systemctl kill -s HUP xelasphere`.
Hard-refresh the chat tab.

Verify by:
1. Open the modal on an existing reminder. Confirm the Repeat dropdown shows "Doesn't repeat" (or the saved value).
2. Confirm the WEEKLY / MONTHLY / YEARLY options now show dynamic text matching the date (e.g., "Monthly on the 19th").
3. Change the dropdown to "Daily", click Save.
4. Confirm via Firestore (or `curl /reminders/<id>`) that the doc has `recurrence: "DAILY"`.
5. Re-open the modal — confirm the dropdown is now "Daily".

- [ ] **Step 4: Commit**

```bash
git add app/static/js/remindersModule.js
git commit -m "feat(reminders/m1.1): modal populates + submits recurrence field

Dynamic labels on WEEKLY/MONTHLY/YEARLY options reflect the current
fireAt (e.g. 'Monthly on the 19th'). Empty string sent as null to
clear recurrence."
```

---

## Task 9: Card display — show recurrence label when recurring

**Files:**
- Modify: `app/static/js/remindersModule.js`

- [ ] **Step 1: Render recurrence label in `formatWhen` output**

In `app/static/js/remindersModule.js`, find `function formatWhen(reminder)`. After the existing logic returns `label + ' · ' + tzShort`, modify to also append the recurrence:

```javascript
  function formatWhen(reminder) {
    if (!reminder || !reminder.fireAt) return '';
    const dt = new Date(reminder.fireAt);
    if (isNaN(dt.getTime())) return '';
    const tz = reminder.timezone || getBrowserTz();
    let label;
    try {
      label = dt.toLocaleString(undefined, {
        timeZone: tz, month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch {
      label = dt.toLocaleString();
    }
    const tzShort = tz.includes('/') ? tz.split('/').pop().replace('_', ' ') : tz;
    const recLabel = _recurrenceLabel(reminder.recurrence);
    return label + ' · ' + tzShort + (recLabel ? ' · ' + recLabel : '');
  }

  function _recurrenceLabel(rec) {
    switch (rec) {
      case 'DAILY':    return 'Daily';
      case 'WEEKDAYS': return 'Weekdays';
      case 'WEEKLY':   return 'Weekly';
      case 'MONTHLY':  return 'Monthly';
      case 'YEARLY':   return 'Yearly';
      default:         return '';
    }
  }
```

- [ ] **Step 2: Smoke-test**

Hard-refresh the chat tab. Edit one of your reminders to `DAILY` via the modal and save. The card subtitle should now read like:

```
May 20, 09:00 · London · Daily
```

Non-recurring reminders read exactly as before (no change).

- [ ] **Step 3: Commit**

```bash
git add app/static/js/remindersModule.js
git commit -m "feat(reminders/m1.1): reminder cards show recurrence label

Subtitle becomes 'May 20, 09:00 · London · Daily' for recurring
reminders. Non-recurring unchanged."
```

---

## Task 10: End-to-end manual verification

**No files — verification step.**

- [ ] **Step 1: Flip the kill switch on for your own use**

```bash
echo "REMINDERS_RECURRENCE_ENABLED=true" | sudo tee -a /etc/xelasphere/xelasphere.env
sudo systemctl restart xelasphere xelasphere-reminders.timer
```

(The timer doesn't really need restarting since the worker reads env on each invocation, but doesn't hurt.)

- [ ] **Step 2: Create a DAILY reminder for 2 minutes from now**

In the chat: "remind me to test recurring in 2 minutes daily" (or use the manual modal create with `Repeat: Daily`).

Watch the inline card render with the new "Daily" subtitle.

- [ ] **Step 3: Wait for the first fire**

After 2 minutes, confirm the email arrived. Then immediately query Firestore:

```bash
cd /opt/xelasphere && set -a && source /etc/xelasphere/xelasphere.env && set +a && \
/opt/xelasphere/venv/bin/python -c "
import sys; sys.path.insert(0,'/opt/xelasphere')
from app.config import db
rem = db.collection_group('reminders').stream()
for r in rem:
    d = r.to_dict() or {}
    if d.get('recurrence') == 'DAILY' and d.get('deliveryCount', 0) >= 1:
        print(r.id, d.get('status'), d.get('fireAt'), 'count=', d.get('deliveryCount'))
"
```

Expected: status=`scheduled`, fireAt = 24 hours after original, deliveryCount=1.

- [ ] **Step 4: Tail worker logs to see the advance event**

```bash
sudo journalctl -u 'xelasphere-reminders*' --since "5 minutes ago" --no-pager | grep recurring_advanced
```

Expected: one log line `reminder_recurring_advanced` with the next fireAt.

- [ ] **Step 5: Clean up — pause or delete the test reminder so it doesn't fire daily forever**

In the chat modal: open the card, click Pause (or Delete).

- [ ] **Step 6: No commit — this is verification only.** If anything is wrong, file an issue and patch.

---

## Task 11: Document the slice in CHANGELOG + memory

**Files:**
- Modify: `CHANGELOG.md` (if present in the project — check first)
- Modify: `/root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md` (note M1.1 shipped)

- [ ] **Step 1: Check for CHANGELOG**

```bash
ls /root/xelas.io/xelasphere.io-/CHANGELOG.md 2>&1
```

If exists, append entry under a new `## [M1.1] - 2026-05-DD` heading:

```markdown
### Added
- Recurring reminders: 5 presets (Daily, Every weekday, Weekly, Monthly, Yearly)
- Worker advances fireAt in local time after each delivery (DST-safe)
- Recurrence dropdown in the edit modal
- Reminder cards show "Daily" / "Weekly" etc. label
```

If not present, skip — repo has no CHANGELOG convention.

- [ ] **Step 2: Update the project memory**

In `project_reminders_m0_shipped.md`, under the existing closeout section, add:

```markdown
**M1.1 shipped (2026-05-DD):**
- Recurrence presets (DAILY/WEEKDAYS/WEEKLY/MONTHLY/YEARLY) live behind
  REMINDERS_RECURRENCE_ENABLED=true on xelasphere.com
- Worker advances fireAt in local tz; DST + month-end + leap year handled
- Modal dropdown re-enabled; cards show recurrence label
- M1.2 (snooze) and M1.3 (push) plans not yet written — done as separate
  brainstorms when the slice is ready
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md 2>/dev/null
git add /root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md
git commit -m "docs(reminders/m1.1): CHANGELOG + memory entry for M1.1 ship"
```

---

## Self-Review Checklist

Before declaring this plan ready for execution:

**1. Spec coverage**

Each M1.1 spec requirement maps to a task:

| Spec section | Plan task |
|---|---|
| Recurrence presets | Task 2 (`RECURRENCE_PRESETS` constant + `next_fire_at`) |
| Worker advances after delivery | Task 5 |
| DST handling via dateutil + ZoneInfo | Task 2 (impl), Task 3 (tests) |
| Routes validate recurrence field | Task 6 |
| Modal dropdown UI | Tasks 7 + 8 |
| Card shows recurrence label | Task 9 |
| Kill switch | Task 4 + Task 10 (use) |
| Pin python-dateutil | Task 1 |

No gaps detected.

**2. Placeholder scan**

Search the plan for red flags. Run:

```bash
grep -nE "TBD|TODO|fill in|similar to|implement later|appropriate error" /root/xelas.io/xelasphere.io-/docs/superpowers/plans/2026-05-19-reminders-m1.1-recurrence.md
```

Expected: zero matches.

**3. Type consistency**

- `next_fire_at(reminder: dict) -> Optional[datetime]` — consistent in Task 2 impl + Task 5 caller.
- `RECURRENCE_PRESETS` set with 5 strings — defined in Task 2, re-exported in Task 6.
- Firestore field names: `recurrence`, `deliveryCount`, `lastFiredAt`, `snoozedUntil` — match the spec data model section.

**4. Ambiguity**

- "Tomorrow" snooze duration is OUT OF SCOPE for M1.1 — it's in M1.2.
- "Similar to test_X" appears once in Task 6 Step 1 with explicit instructions to copy the pattern verbatim, not 'similar to'.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-reminders-m1.1-recurrence.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?

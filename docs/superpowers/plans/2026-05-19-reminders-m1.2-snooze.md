# Reminders M1.2 — Snooze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship reminder snooze to xelasphere.com. Three durations (10m / 1h / Tomorrow), two surfaces (signed email links + in-app kebab submenu), one backend route. After a successful snooze the worker honors `snoozedUntil` by claiming the doc, seeing the future timestamp, releasing the lock, and skipping the tick.

**Architecture:** `signed_links.py` wraps `itsdangerous.URLSafeTimedSerializer` with a 24-hour max-age for email tokens. `POST /reminders/<id>/snooze` (session auth) and `GET /reminders/<id>/snooze?token=...` (signed-token auth) share one `_apply_snooze` helper. Worker `process_reminder` adds an effective-fire-at check after claiming the doc. Email builders append a snooze link block; kebab dropdown gets a Snooze submenu. Everything gated on `REMINDERS_SNOOZE_ENABLED`.

**Tech Stack:** Python 3.12, Flask, Firestore, `itsdangerous` (Flask transitive), Jinja2 (existing template engine).

**Spec:** [`docs/superpowers/specs/2026-05-19-reminders-m1.2-snooze-design.md`](../specs/2026-05-19-reminders-m1.2-snooze-design.md) commit `bbe6b36`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `app/services/signed_links.py` | **Create** | `make_snooze_token(rid, uid)` and `verify_snooze_token(token) -> dict`. Wraps `itsdangerous.URLSafeTimedSerializer` with salt `"reminder-snooze"` and 24h max-age. Raises domain exceptions on bad input. |
| `tests/test_signed_links.py` | **Create** | Token round-trip, expired, tampered, missing fields. |
| `app/blueprints/reminders_routes.py` | **Modify** | Add `_apply_snooze(reminder_id, dur, user_id) -> tuple[status_code, payload]` helper. Add `POST /reminders/<id>/snooze` (session). Add `GET /reminders/<id>/snooze` (token). |
| `tests/test_reminders_routes.py` | **Modify** | New `TestSnoozeRoute` class: POST happy/invalid-dur/wrong-state/flag-off; GET happy/expired/tampered/wrong-uid. |
| `app/templates/_snoozed_confirmation.html` | **Create** | Self-contained HTML page rendered by the GET route. Branches on `status` (ok/expired/invalid/wrong_state/not_found/flag_off). |
| `tools/reminders_worker.py` | **Modify** | `process_reminder`: snooze-skip check after the claim. Email builders (`_build_email_body`, `_build_email_html`): append snooze link block when kill switch is on. |
| `tests/test_reminders_worker.py` | **Modify** | Snooze-skip integration test (mock SendGrid never called). Email-builder tests confirm link presence/absence. |
| `app/static/js/remindersModule.js` | **Modify** | Add Snooze submenu to kebab dropdown (visible only on active states). POST handler on submenu click. Card subtitle shows `(Snoozed)` when `snoozedUntil > now`. |
| `app/config.py` | **Modify** | Add `REMINDERS_SNOOZE_ENABLED` env flag (default `false`). |
| `CHANGELOG.md` | **Modify** | `Unreleased > Reminders M1.2` entry. |

---

## Task 1: Add `REMINDERS_SNOOZE_ENABLED` kill switch

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Locate `REMINDERS_RECURRENCE_ENABLED` and add the new flag below it**

In `app/config.py`, find the existing line (around line 146):

```python
REMINDERS_RECURRENCE_ENABLED = os.getenv('REMINDERS_RECURRENCE_ENABLED', 'false').lower() == 'true'
```

Add immediately below it (same indentation):

```python
REMINDERS_SNOOZE_ENABLED = os.getenv('REMINDERS_SNOOZE_ENABLED', 'false').lower() == 'true'
```

- [ ] **Step 2: Smoke-test the config loads**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -c "
from app.config import Config
print('REMINDERS_SNOOZE_ENABLED:', Config.REMINDERS_SNOOZE_ENABLED)
"
```

Expected: `REMINDERS_SNOOZE_ENABLED: False`.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "chore(reminders/m1.2): add REMINDERS_SNOOZE_ENABLED kill switch (default off)"
```

---

## Task 2: TDD — `signed_links.py` token round-trip

**Files:**
- Create: `app/services/signed_links.py`
- Create: `tests/test_signed_links.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_signed_links.py`:

```python
"""Unit tests for app.services.signed_links.

Tokens are signed with Config.SECRET_KEY + salt 'reminder-snooze' and have
a 24-hour max-age. Verify round-trip, expiry, tampering, ownership."""
import pytest
import time
from app.services.signed_links import (
    make_snooze_token,
    verify_snooze_token,
    SnoozeTokenExpired,
    SnoozeTokenInvalid,
)


class TestRoundTrip:
    def test_make_and_verify(self):
        token = make_snooze_token("rid_abc", "uid_xyz")
        payload = verify_snooze_token(token)
        assert payload == {"rid": "rid_abc", "uid": "uid_xyz"}

    def test_tokens_for_different_reminders_are_different(self):
        t1 = make_snooze_token("rid_1", "uid_a")
        t2 = make_snooze_token("rid_2", "uid_a")
        assert t1 != t2


class TestRejection:
    def test_tampered_token_raises_invalid(self):
        token = make_snooze_token("rid_abc", "uid_xyz")
        tampered = token[:-4] + "AAAA"  # flip last 4 chars
        with pytest.raises(SnoozeTokenInvalid):
            verify_snooze_token(tampered)

    def test_garbage_token_raises_invalid(self):
        with pytest.raises(SnoozeTokenInvalid):
            verify_snooze_token("not-a-token")

    def test_empty_token_raises_invalid(self):
        with pytest.raises(SnoozeTokenInvalid):
            verify_snooze_token("")

    def test_expired_token_raises_expired(self, monkeypatch):
        # Mint a token, then move "now" forward past the max-age.
        from app.services import signed_links
        token = make_snooze_token("rid_abc", "uid_xyz")
        # itsdangerous uses time.time() internally; patch it forward 25h.
        real_time = time.time
        future = real_time() + (25 * 3600)
        monkeypatch.setattr(time, "time", lambda: future)
        with pytest.raises(SnoozeTokenExpired):
            verify_snooze_token(token)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_signed_links.py -v
```

Expected: All FAIL with `ModuleNotFoundError: No module named 'app.services.signed_links'`.

- [ ] **Step 3: Implement `signed_links.py`**

Create `app/services/signed_links.py`:

```python
"""Signed-link infrastructure for reminders.

Tokens carry {rid, uid} and expire after 24 hours. Used by the email
snooze flow: each snooze link in the email body is signed at send-time
with a token, validated on click, no session required.

Two domain exceptions are raised so routes can branch HTTP statuses
cleanly. itsdangerous's SignatureExpired / BadSignature are caught at
this boundary and re-raised as the domain types."""
from __future__ import annotations

from typing import Any

from itsdangerous import URLSafeTimedSerializer
from itsdangerous.exc import SignatureExpired, BadSignature

from app.config import Config

_SALT = "reminder-snooze"
_MAX_AGE_SECONDS = 24 * 3600  # 24 hours


class SnoozeTokenExpired(Exception):
    """Token is well-formed and was signed by us, but is older than 24h."""


class SnoozeTokenInvalid(Exception):
    """Token is malformed, tampered, or signed by a different secret."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(Config.SECRET_KEY, salt=_SALT)


def make_snooze_token(rid: str, uid: str) -> str:
    """Sign a {rid, uid} payload and return the URL-safe token string.

    Caller is responsible for including the token in a snooze URL like
    /reminders/{rid}/snooze?dur=10m&token={token}."""
    return _serializer().dumps({"rid": rid, "uid": uid})


def verify_snooze_token(token: str) -> dict[str, Any]:
    """Verify a token and return its payload {rid, uid}.

    Raises SnoozeTokenExpired if the token is older than 24h.
    Raises SnoozeTokenInvalid for any other failure (malformed, tampered,
    wrong signature, empty input)."""
    if not token:
        raise SnoozeTokenInvalid("empty token")
    try:
        payload = _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except SignatureExpired as e:
        raise SnoozeTokenExpired(str(e)) from e
    except BadSignature as e:
        raise SnoozeTokenInvalid(str(e)) from e
    except Exception as e:
        raise SnoozeTokenInvalid(str(e)) from e
    if not isinstance(payload, dict) or "rid" not in payload or "uid" not in payload:
        raise SnoozeTokenInvalid("payload missing rid/uid")
    return payload
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_signed_links.py -v
```

Expected: All 7 PASS.

If `test_expired_token_raises_expired` fails because `time.time` patching doesn't propagate into `itsdangerous`, adjust the test to use `itsdangerous`'s own `_get_timestamp` or pass `salt`+a fixed `max_age=0` to force expiry. Confirm the expiry path works one way or another before moving on.

- [ ] **Step 5: Commit**

```bash
git add app/services/signed_links.py tests/test_signed_links.py
git commit -m "feat(reminders/m1.2): signed-link infra (make+verify with 24h expiry)

itsdangerous URLSafeTimedSerializer wrapped behind domain exceptions
(SnoozeTokenExpired, SnoozeTokenInvalid) so routes can branch HTTP
status cleanly. 7 unit tests."
```

---

## Task 3: TDD — `_apply_snooze` helper

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing tests for the helper**

Add to `tests/test_reminders_routes.py`:

```python
from datetime import datetime, timezone, timedelta


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
        # Verify the update call included snoozedUntil set to ~now+10m.
        update_call = fake_doc_ref.update.call_args[0][0]
        snoozed_until = datetime.fromisoformat(update_call["snoozedUntil"])
        expected = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert abs((snoozed_until - expected).total_seconds()) < 5

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

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_on_delivered_one_off_returns_409(self, mock_db):
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "delivered",
            "name": "test", "recurrence": None,
        }
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 409

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_wrong_owner_returns_403(self, mock_db):
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_abc", "userId": "uid_xyz", "status": "scheduled",
            "name": "test", "recurrence": None,
        }
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_OTHER")
        assert status == 403

    @patch("app.blueprints.reminders_routes.db")
    def test_apply_missing_doc_returns_404(self, mock_db):
        fake_snap = MagicMock()
        fake_snap.exists = False
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        from app.blueprints.reminders_routes import _apply_snooze
        status, _ = _apply_snooze("rid_abc", "10m", "uid_xyz")
        assert status == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestApplySnooze -v
```

Expected: All 8 FAIL — `_apply_snooze` doesn't exist yet.

- [ ] **Step 3: Implement `_apply_snooze` in routes**

In `app/blueprints/reminders_routes.py`, add this helper near the top after the existing imports but before the blueprint routes. Add the needed imports first:

```python
from datetime import datetime, timezone, timedelta
```

(May already be imported — check before duplicating.)

Then add:

```python
# ==================== Snooze helper (M1.2) ====================
_SNOOZE_DURATIONS = {
    "10m":      timedelta(minutes=10),
    "1h":       timedelta(hours=1),
    "tomorrow": timedelta(hours=24),
}
_SNOOZE_ELIGIBLE_STATES = {"scheduled", "sending"}


def _apply_snooze(reminder_id: str, dur: str, user_id: str) -> tuple[int, dict]:
    """Validate + apply a snooze. Returns (http_status, response_payload).

    Used by both POST (session auth) and GET (signed-token auth). The
    only difference between the two is how the caller extracted user_id.
    Eligibility: scheduled, sending, OR delivered+recurring."""
    if dur not in _SNOOZE_DURATIONS:
        return 400, {"error": f"dur must be one of {sorted(_SNOOZE_DURATIONS)}"}

    doc_ref = (
        db.collection("users").document(user_id)
          .collection("reminders").document(reminder_id)
    )
    # Fetch via the user_id path. If the doc doesn't exist there it could
    # either be a typo or a cross-account attempt; we treat as 404 for the
    # owner path and 403 if the doc exists under a different uid (looked
    # up via collection_group below).
    snap = doc_ref.get()
    if not snap.exists:
        # Check whether the reminder exists at all (under any user).
        from google.cloud import firestore as _fs
        other = (
            db.collection_group("reminders")
              .where("id", "==", reminder_id)
              .limit(1).stream()
        )
        if any(True for _ in other):
            return 403, {"error": "not your reminder"}
        return 404, {"error": "reminder not found"}

    reminder = snap.to_dict() or {}
    if reminder.get("userId") != user_id:
        return 403, {"error": "not your reminder"}

    status = reminder.get("status")
    recurrence = reminder.get("recurrence")
    eligible = (
        status in _SNOOZE_ELIGIBLE_STATES
        or (status == "delivered" and recurrence is not None)
    )
    if not eligible:
        return 409, {"error": f"cannot snooze a reminder in state {status!r}"}

    now = datetime.now(timezone.utc)
    snoozed_until = now + _SNOOZE_DURATIONS[dur]
    doc_ref.update({
        "snoozedUntil": snoozed_until.isoformat(),
        "updatedAt":    now.isoformat(),
    })
    logger.info(
        "reminder_snoozed",
        extra={"extra_fields": {
            "rid": reminder_id, "duration": dur,
            "until": snoozed_until.isoformat(), "uid": user_id,
        }},
    )

    # Return updated payload for in-app callers; GET route ignores it
    # and renders a template instead.
    fresh = snap.to_dict() or {}
    fresh["snoozedUntil"] = snoozed_until.isoformat()
    return 200, {"reminder": fresh, "snoozedUntil": snoozed_until.isoformat()}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestApplySnooze -v
```

Expected: All 8 PASS. If `test_apply_missing_doc_returns_404` fails because of the collection_group fallback path, simplify the not-found branch — if the snap doesn't exist via the user_id path, return 404 (don't bother checking other users; the GET route already validated ownership via the token). Update the test if you simplify.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders/m1.2): _apply_snooze helper with state + ownership checks

Shared by POST (session) and GET (signed-token) routes. Validates
duration enum, owner, eligible states (scheduled/sending/delivered+recurring).
Writes snoozedUntil + logs reminder_snoozed. 8 unit tests."
```

---

## Task 4: TDD — POST `/reminders/<id>/snooze` route

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_reminders_routes.py`:

```python
class TestSnoozePostRoute:
    """POST /reminders/<id>/snooze — session-authenticated in-app surface."""

    @patch("app.blueprints.reminders_routes.db")
    def test_post_happy_path_returns_200(self, mock_db, client, auth_session):
        fake_doc_ref = MagicMock()
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_x", "userId": auth_session["user"]["uid"],
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
        assert resp.status_code in (401, 302)

    @patch("app.blueprints.reminders_routes.db")
    def test_post_invalid_dur_returns_400(self, mock_db, client, auth_session):
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestSnoozePostRoute -v
```

Expected: All FAIL — route doesn't exist.

- [ ] **Step 3: Implement the POST route**

In `app/blueprints/reminders_routes.py`, add the import:

```python
from app.config import Config
```

(Probably already imported; check first.)

Then add the route handler near the other routes:

```python
@reminders_bp.route("/reminders/<reminder_id>/snooze", methods=["POST"])
@login_required
@csrf_required  # whatever decorator name the existing routes use; check
def snooze_reminder(reminder_id: str):
    """In-app snooze (session-authenticated). JSON body: {"dur": "10m"|"1h"|"tomorrow"}."""
    if not Config.REMINDERS_SNOOZE_ENABLED:
        return jsonify({"error": "snooze disabled"}), 503

    payload = request.get_json(silent=True) or {}
    dur = payload.get("dur", "")

    user_id = session["user"]["uid"]
    status, body = _apply_snooze(reminder_id, dur, user_id)
    return jsonify(body), status
```

Note: copy the exact decorator names used by neighboring routes (e.g., `@login_required` vs `@require_login`, CSRF decorator vs `csrf_protect()`). Match the file's existing style verbatim.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestSnoozePostRoute -v
```

Expected: All 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders/m1.2): POST /reminders/<id>/snooze (session auth)"
```

---

## Task 5: Confirmation page template + GET route

**Files:**
- Create: `app/templates/_snoozed_confirmation.html`
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Create the template**

Create `app/templates/_snoozed_confirmation.html`:

```html
{# Snooze-link confirmation page. Rendered by GET /reminders/<id>/snooze.
   Self-contained: inline CSS, no JS, no external assets.
   Caller passes: status (ok|expired|invalid|wrong_state|not_found|flag_off),
                  until_local (string, only for status=ok),
                  chat_url (string, optional). #}
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>XelaSphere Reminders</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="margin:0;padding:24px;background:#F7F7F8;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
             color:#1a1a1a;">
  <div style="max-width:480px;margin:48px auto;background:#fff;
              border-radius:12px;padding:32px;box-shadow:0 1px 2px rgba(0,0,0,0.04);">
    <p style="margin:0 0 4px 0;font-size:12px;font-weight:600;
              text-transform:uppercase;letter-spacing:0.08em;color:#10A37F;">
      XelaSphere Reminders
    </p>

    {% if status == "ok" %}
      <h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">&#10003; Snoozed</h1>
      <p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;">
        We'll remind you at {{ until_local }}.
      </p>
    {% elif status == "expired" %}
      <h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">Link expired</h1>
      <p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;">
        This snooze link has expired. Open the reminder in chat to reschedule.
      </p>
    {% elif status == "invalid" %}
      <h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">Invalid link</h1>
      <p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;">
        This link looks invalid. Open the reminder in chat to reschedule.
      </p>
    {% elif status == "wrong_state" %}
      <h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">Can't snooze</h1>
      <p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;">
        This reminder can't be snoozed in its current state. Open chat to manage it.
      </p>
    {% elif status == "not_found" %}
      <h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">Reminder not found</h1>
      <p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;">
        We couldn't find this reminder. It may have been deleted.
      </p>
    {% elif status == "flag_off" %}
      <h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">Snooze unavailable</h1>
      <p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;">
        Snooze isn't available right now. Open chat to reschedule.
      </p>
    {% endif %}

    {% if chat_url %}
      <p style="margin:24px 0 0 0;">
        <a href="{{ chat_url }}"
           style="display:inline-block;padding:10px 20px;background:#10A37F;
                  color:#fff;text-decoration:none;border-radius:8px;
                  font-weight:600;font-size:14px;">Open chat</a>
      </p>
    {% endif %}
  </div>
</body>
</html>
```

- [ ] **Step 2: Write failing tests for GET route**

Add to `tests/test_reminders_routes.py`:

```python
class TestSnoozeGetRoute:
    """GET /reminders/<id>/snooze?token=... — signed-token auth from emails."""

    def _mint_token(self, rid, uid):
        from app.services.signed_links import make_snooze_token
        return make_snooze_token(rid, uid)

    @patch("app.blueprints.reminders_routes.db")
    def test_get_happy_path_renders_ok_page(self, mock_db, client):
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
        assert resp.status_code == 200
        assert b"Snoozed" in resp.data
        assert b"session_abc" in resp.data  # chat_url link present

    def test_get_expired_token_returns_410(self, client, monkeypatch):
        import time as _time
        token = self._mint_token("rid_x", "uid_y")
        future = _time.time() + (25 * 3600)
        monkeypatch.setattr(_time, "time", lambda: future)
        resp = client.get(f"/reminders/rid_x/snooze?dur=10m&token={token}")
        assert resp.status_code == 410
        assert b"expired" in resp.data.lower()

    def test_get_garbage_token_returns_400(self, client):
        resp = client.get("/reminders/rid_x/snooze?dur=10m&token=not-a-token")
        assert resp.status_code == 400
        assert b"invalid" in resp.data.lower()

    @patch("app.blueprints.reminders_routes.db")
    def test_get_wrong_uid_returns_403(self, mock_db, client):
        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {
            "id": "rid_x", "userId": "uid_REAL",
            "status": "scheduled", "name": "t", "recurrence": None,
        }
        mock_db.collection.return_value.document.return_value \
               .collection.return_value.document.return_value.get.return_value = fake_snap

        token = self._mint_token("rid_x", "uid_ATTACKER")
        resp = client.get(f"/reminders/rid_x/snooze?dur=10m&token={token}")
        assert resp.status_code == 403

    @patch("app.blueprints.reminders_routes.Config")
    def test_get_flag_off_returns_503(self, mock_config, client):
        mock_config.REMINDERS_SNOOZE_ENABLED = False
        resp = client.get("/reminders/rid_x/snooze?dur=10m&token=anything")
        assert resp.status_code == 503
```

- [ ] **Step 3: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestSnoozeGetRoute -v
```

Expected: All 5 FAIL — GET route doesn't exist.

- [ ] **Step 4: Implement the GET route**

In `app/blueprints/reminders_routes.py`, add the imports near the top:

```python
from flask import render_template
from app.services.signed_links import (
    verify_snooze_token, SnoozeTokenExpired, SnoozeTokenInvalid,
)
```

Then add the route. Place it BEFORE other `/<reminder_id>/...` routes to avoid path-matching collisions:

```python
@reminders_bp.route("/reminders/<reminder_id>/snooze", methods=["GET"])
def snooze_reminder_get(reminder_id: str):
    """Email signed-token snooze. Renders an HTML confirmation page."""
    if not Config.REMINDERS_SNOOZE_ENABLED:
        return render_template("_snoozed_confirmation.html",
                               status="flag_off", chat_url=None), 503

    token = request.args.get("token", "")
    dur = request.args.get("dur", "")

    try:
        token_payload = verify_snooze_token(token)
    except SnoozeTokenExpired:
        logger.info("reminder_snooze_rejected",
            extra={"extra_fields": {"rid": reminder_id, "reason": "token_expired"}})
        return render_template("_snoozed_confirmation.html",
                               status="expired", chat_url=None), 410
    except SnoozeTokenInvalid:
        logger.info("reminder_snooze_rejected",
            extra={"extra_fields": {"rid": reminder_id, "reason": "token_invalid"}})
        return render_template("_snoozed_confirmation.html",
                               status="invalid", chat_url=None), 400

    # URL id must match token rid (defense in depth)
    if token_payload.get("rid") != reminder_id:
        return render_template("_snoozed_confirmation.html",
                               status="invalid", chat_url=None), 400

    uid = token_payload["uid"]
    status_code, payload = _apply_snooze(reminder_id, dur, uid)

    # Look up chat_url for the Open chat button (best-effort)
    chat_url = None
    try:
        snap = (db.collection("users").document(uid)
                  .collection("reminders").document(reminder_id).get())
        if snap.exists:
            chat_id = (snap.to_dict() or {}).get("sourceChatId")
            if chat_id:
                chat_url = f"{Config.APP_BASE_URL}/c/{chat_id}"
    except Exception:
        pass

    if status_code == 200:
        from zoneinfo import ZoneInfo
        snoozed_until_iso = payload.get("snoozedUntil", "")
        tz_name = (snap.to_dict() or {}).get("timezone", "UTC") if snap and snap.exists else "UTC"
        try:
            dt = datetime.fromisoformat(snoozed_until_iso.replace("Z", "+00:00"))
            local = dt.astimezone(ZoneInfo(tz_name))
            tz_short = tz_name.split("/")[-1].replace("_", " ")
            until_local = local.strftime("%H:%M") + " " + tz_short
        except Exception:
            until_local = snoozed_until_iso
        return render_template("_snoozed_confirmation.html",
                               status="ok", until_local=until_local, chat_url=chat_url), 200

    template_status = {
        400: "invalid",
        403: "invalid",  # don't leak ownership info via different message
        404: "not_found",
        409: "wrong_state",
    }.get(status_code, "invalid")
    logger.info("reminder_snooze_rejected",
        extra={"extra_fields": {"rid": reminder_id, "reason": f"http_{status_code}"}})
    return render_template("_snoozed_confirmation.html",
                           status=template_status, chat_url=chat_url), status_code
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_routes.py::TestSnoozeGetRoute -v
```

Expected: All 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/_snoozed_confirmation.html app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders/m1.2): GET /reminders/<id>/snooze with signed-token auth

Renders confirmation HTML page. Branches: ok / expired / invalid /
wrong_state / not_found / flag_off. Reuses _apply_snooze helper from
Task 3. 5 integration tests."
```

---

## Task 6: Worker effective-fire-at skip

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing integration test**

Add to `tests/test_reminders_worker.py`:

```python
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
```

- [ ] **Step 2: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py::test_process_reminder_skips_when_snoozed_in_future tests/test_reminders_worker.py::test_process_reminder_proceeds_when_snoozed_in_past -v
```

Expected: BOTH FAIL — `test_process_reminder_skips_when_snoozed_in_future` because SendGrid IS called today (no skip logic exists yet).

- [ ] **Step 3: Add the skip block to `process_reminder`**

In `tools/reminders_worker.py`, find `process_reminder`. Locate the existing claim block (the transactional `scheduled → sending` flip). Immediately AFTER the claim succeeds but BEFORE the `send_transactional_email` call, add:

```python
    # M1.2: honor snoozedUntil — release the claim and skip if not yet due.
    snoozed_until = reminder.get("snoozedUntil")
    if snoozed_until:
        try:
            snoozed_dt = datetime.fromisoformat(snoozed_until.replace("Z", "+00:00"))
            if snoozed_dt.tzinfo is None:
                snoozed_dt = snoozed_dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            snoozed_dt = None
        if snoozed_dt and snoozed_dt > datetime.now(timezone.utc):
            doc_ref.update({
                "status": "scheduled",
                "lockedAt": None,
                "updatedAt": now_iso,
            })
            logger.info(
                "reminder_snooze_skip",
                extra={"extra_fields": {
                    "name": name, "until": snoozed_until, "rid": reminder.get("id"),
                }},
            )
            return
```

Place this BEFORE the existing `try: result = send_transactional_email(...)` block. The exact insertion line depends on what `process_reminder` looks like after M1.1; search for `send_transactional_email` to find the spot.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py -v
```

Expected: ALL tests pass (the 20 from M1.1 + 2 new from M1.2 = 22 total in test_reminders_worker.py).

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/m1.2): worker honors snoozedUntil

After claiming the doc (scheduled→sending) and before SendGrid, check
snoozedUntil. If still in the future, release the lock back to scheduled
and skip this tick. Logs reminder_snooze_skip event. 2 integration tests."
```

---

## Task 7: Email body snooze link block

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_reminders_worker.py`:

```python
def test_email_html_includes_snooze_block_when_enabled(monkeypatch):
    from tools.reminders_worker import _build_email_html
    from app.config import Config
    monkeypatch.setattr(Config, "REMINDERS_SNOOZE_ENABLED", True)
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
```

- [ ] **Step 2: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py -k "snooze_block or snooze_urls" -v
```

Expected: All 3 FAIL — neither body builder mentions snooze yet.

- [ ] **Step 3: Add the snooze block to both builders**

In `tools/reminders_worker.py`, add a helper near `_build_chat_url`:

```python
def _build_snooze_links(reminder: dict) -> dict:
    """Returns a dict {dur: url} for the 3 snooze durations, or empty dict
    if the kill switch is off or sourceChatId is missing (we still mint
    the token though — chat_id only affects the confirmation page)."""
    from app.config import Config
    if not Config.REMINDERS_SNOOZE_ENABLED:
        return {}
    rid = reminder.get("id")
    uid = reminder.get("userId")
    if not rid or not uid:
        return {}
    from app.services.signed_links import make_snooze_token
    token = make_snooze_token(rid, uid)
    base = f"{Config.APP_BASE_URL}/reminders/{rid}/snooze"
    return {
        "10m": f"{base}?dur=10m&token={token}",
        "1h": f"{base}?dur=1h&token={token}",
        "tomorrow": f"{base}?dur=tomorrow&token={token}",
    }
```

Then in `_build_email_body`, after the existing `link_line` and BEFORE the final `return f"{body}{banner}..."`, add:

```python
    snooze_urls = _build_snooze_links(reminder)
    snooze_block = ""
    if snooze_urls:
        snooze_block = (
            "\n\nNeed more time?\n"
            f" - Snooze 10 min:        {snooze_urls['10m']}\n"
            f" - Snooze 1 hour:        {snooze_urls['1h']}\n"
            f" - Snooze until tomorrow: {snooze_urls['tomorrow']}"
        )
    return f"{body}{banner}{snooze_block}\n\n— XelaSphere Reminders\nScheduled for: {fire_at_iso}{link_line}\n"
```

And in `_build_email_html`, build the snooze HTML similarly and insert it BEFORE the closing signature `<hr>`:

```python
    snooze_urls = _build_snooze_links(reminder)
    snooze_html = ""
    if snooze_urls:
        snooze_html = (
            '<p style="margin:24px 0 0 0;font-size:13px;color:#666;">Need more time?</p>'
            '<p style="margin:6px 0 0 0;">'
            f'<a href="{_html.escape(snooze_urls["10m"])}" '
            'style="display:inline-block;padding:8px 14px;background:#F0F0F0;color:#333;'
            'text-decoration:none;border-radius:6px;font-size:13px;margin-right:6px;">Snooze 10 min</a>'
            f'<a href="{_html.escape(snooze_urls["1h"])}" '
            'style="display:inline-block;padding:8px 14px;background:#F0F0F0;color:#333;'
            'text-decoration:none;border-radius:6px;font-size:13px;margin-right:6px;">Snooze 1 hour</a>'
            f'<a href="{_html.escape(snooze_urls["tomorrow"])}" '
            'style="display:inline-block;padding:8px 14px;background:#F0F0F0;color:#333;'
            'text-decoration:none;border-radius:6px;font-size:13px;">Snooze until tomorrow</a>'
            '</p>'
        )
```

Insert `{snooze_html}` into the final return string BEFORE the `<hr>` line. The full final return becomes:

```python
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:24px;'
        "background:#F7F7F8;font-family:-apple-system,BlinkMacSystemFont,"
        "'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;\">"
        '<div style="max-width:560px;margin:0 auto;background:#fff;'
        'border-radius:12px;padding:32px;box-shadow:0 1px 2px rgba(0,0,0,0.04);">'
        '<p style="margin:0 0 4px 0;font-size:12px;font-weight:600;'
        'text-transform:uppercase;letter-spacing:0.08em;color:#10A37F;">Reminder</p>'
        f'<h1 style="margin:0 0 16px 0;font-size:20px;font-weight:600;line-height:1.3;">{name_safe}</h1>'
        f'{banner_html}'
        f'<p style="margin:0;font-size:15px;line-height:1.55;color:#3a3a3a;white-space:pre-wrap;">{body_safe}</p>'
        f'{button_html}'
        f'{snooze_html}'
        '<hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0 16px 0;">'
        f'<p style="margin:0;font-size:12px;color:#888;">— XelaSphere Reminders &middot; Scheduled for {fire_at_iso_safe}</p>'
        '</div></body></html>'
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py -v
```

Expected: All pass. Run the full worker suite to confirm no regressions.

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/m1.2): snooze links in email bodies (HTML + plain text)

Tokens minted at send-time per recipient. Block omitted when kill
switch is off. 3 builder tests."
```

---

## Task 8: In-app kebab Snooze submenu + card subtitle

**Files:**
- Modify: `app/static/js/remindersModule.js`

- [ ] **Step 1: Read the current kebab dropdown structure**

Open `app/static/js/remindersModule.js` and find the function that builds the kebab dropdown. After M0 polish there's a function around the area `_buildKebabDropdown` or similar (search for "Edit details" string to locate). Note the existing items: Edit, Pause/Resume, Delete.

- [ ] **Step 2: Add the Snooze submenu**

In the dropdown-building function, AFTER the Pause/Resume entry and BEFORE the Delete entry, insert a Snooze submenu. The pattern depends on the existing implementation — adapt to match. Conceptually:

```javascript
    // M1.2: Snooze submenu (active states only)
    const status = reminder.status || 'scheduled';
    const recurrence = reminder.recurrence;
    const snoozeEligible = (
      status === 'scheduled' ||
      status === 'sending' ||
      (status === 'delivered' && recurrence)
    );
    const snoozeEnabled = window.RemindersModule && window.RemindersModule.config &&
                          window.RemindersModule.config.snoozeEnabled;

    if (snoozeEligible && snoozeEnabled) {
      const snoozeItem = document.createElement('div');
      snoozeItem.className = 'kebab-item kebab-item-with-submenu';
      snoozeItem.textContent = 'Snooze ▸';
      snoozeItem.addEventListener('click', (e) => {
        e.stopPropagation();
        _openSnoozeSubmenu(snoozeItem, reminder);
      });
      dropdown.appendChild(snoozeItem);
    }
```

And the submenu helper near the top of the IIFE:

```javascript
  function _openSnoozeSubmenu(anchorEl, reminder) {
    const sub = document.createElement('div');
    sub.className = 'kebab-dropdown kebab-submenu';

    [
      ['10m', '10 minutes'],
      ['1h', '1 hour'],
      ['tomorrow', 'Tomorrow at this time'],
    ].forEach(([dur, label]) => {
      const item = document.createElement('div');
      item.className = 'kebab-item';
      item.textContent = label;
      item.addEventListener('click', async (e) => {
        e.stopPropagation();
        sub.remove();
        await _doSnooze(reminder.id, dur, reminder);
      });
      sub.appendChild(item);
    });

    // Position to the right of the anchor
    const rect = anchorEl.getBoundingClientRect();
    sub.style.position = 'fixed';
    sub.style.left = (rect.right + 4) + 'px';
    sub.style.top = rect.top + 'px';
    document.body.appendChild(sub);

    // Close on outside click
    setTimeout(() => {
      document.addEventListener('click', function close() {
        sub.remove();
        document.removeEventListener('click', close);
      }, { once: true });
    }, 0);
  }

  async function _doSnooze(rid, dur, reminder) {
    try {
      const resp = await fetch(`/reminders/${rid}/snooze`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf(),
        },
        body: JSON.stringify({ dur }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        console.warn('snooze failed', resp.status, data);
        return;
      }
      if (data.reminder) {
        applyReminderToCards(data.reminder);
      }
    } catch (err) {
      console.error('snooze error', err);
    }
  }
```

- [ ] **Step 3: Update `formatWhen` to show `(Snoozed)`**

Find the existing `formatWhen` function (extended in M1.1 to show recurrence). Modify the final return:

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

    // M1.2: if snoozed and snoozedUntil is in the future, show that time + (Snoozed)
    const snoozedUntil = reminder.snoozedUntil;
    if (snoozedUntil) {
      const sdt = new Date(snoozedUntil);
      if (!isNaN(sdt.getTime()) && sdt.getTime() > Date.now()) {
        let snoozeLabel;
        try {
          snoozeLabel = sdt.toLocaleString(undefined, {
            timeZone: tz, month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
          });
        } catch {
          snoozeLabel = sdt.toLocaleString();
        }
        return snoozeLabel + ' · ' + tzShort + (recLabel ? ' · ' + recLabel : '') + ' (Snoozed)';
      }
    }

    return label + ' · ' + tzShort + (recLabel ? ' · ' + recLabel : '');
  }
```

- [ ] **Step 4: Expose the snoozeEnabled config**

The frontend needs to know whether snooze is enabled. There's likely an existing config-injection mechanism — search for `RemindersModule.config` or similar in `app/templates/index.html`. Add `snoozeEnabled: {{ 'true' if config.REMINDERS_SNOOZE_ENABLED else 'false' }}` to the config object. (If no existing config-injection point exists, add a `<script>` block in `index.html` near the existing reminders setup that sets `window.RemindersModule.config = { snoozeEnabled: ... };`.)

- [ ] **Step 5: Manual smoke-test in browser**

```bash
sudo kill -HUP $(sudo systemctl show xelasphere -p MainPID --value)
```

Open `https://xelasphere.com`, hard-refresh. Open the kebab on a scheduled reminder. With flag off (default), Snooze should NOT appear. Set `REMINDERS_SNOOZE_ENABLED=true` in `/etc/xelasphere/xelasphere.env`, restart, refresh. Snooze should appear with submenu opening to 10m/1h/Tomorrow.

- [ ] **Step 6: Commit**

```bash
git add app/static/js/remindersModule.js app/templates/index.html
git commit -m "feat(reminders/m1.2): kebab Snooze submenu + card '(Snoozed)' label

Submenu visible only on scheduled/sending/delivered+recurring AND when
the snoozeEnabled config flag is true. Card subtitle replaces fire time
with snoozedUntil when set, appends '(Snoozed)'."
```

---

## Task 9: End-to-end manual verification

**No files — verification step.**

- [ ] **Step 1: Sync files to /opt/xelasphere and enable the flag**

```bash
sudo cp /root/xelas.io/xelasphere.io-/app/services/signed_links.py /opt/xelasphere/app/services/signed_links.py
sudo cp /root/xelas.io/xelasphere.io-/app/blueprints/reminders_routes.py /opt/xelasphere/app/blueprints/reminders_routes.py
sudo cp /root/xelas.io/xelasphere.io-/app/templates/_snoozed_confirmation.html /opt/xelasphere/app/templates/_snoozed_confirmation.html
sudo cp /root/xelas.io/xelasphere.io-/tools/reminders_worker.py /opt/xelasphere/tools/reminders_worker.py
sudo cp /root/xelas.io/xelasphere.io-/app/static/js/remindersModule.js /opt/xelasphere/app/static/js/remindersModule.js
sudo cp /root/xelas.io/xelasphere.io-/app/config.py /opt/xelasphere/app/config.py
# index.html if you edited it for the config injection
sudo cp /root/xelas.io/xelasphere.io-/app/templates/index.html /opt/xelasphere/app/templates/index.html

if grep -q "REMINDERS_SNOOZE_ENABLED" /etc/xelasphere/xelasphere.env; then
  sudo sed -i 's/^REMINDERS_SNOOZE_ENABLED=.*/REMINDERS_SNOOZE_ENABLED=true/' /etc/xelasphere/xelasphere.env
else
  echo "REMINDERS_SNOOZE_ENABLED=true" | sudo tee -a /etc/xelasphere/xelasphere.env > /dev/null
fi
sudo kill -HUP $(sudo systemctl show xelasphere -p MainPID --value)
```

- [ ] **Step 2: In-app snooze test**

In the browser (hard-refresh), find an existing scheduled reminder (or create one for 5 minutes from now). Open kebab → Snooze → 10 minutes. Card subtitle should update to "(Snoozed)". The reminder's `snoozedUntil` in Firestore should be ~10m from now.

Wait 10 minutes. Email should arrive on time. Verify worker log:

```bash
sudo journalctl -u 'xelasphere-reminders*' --since "12 minutes ago" --no-pager | grep -E "snooze_skip|reminder_sent|reminder_snoozed"
```

Expected: multiple `reminder_snooze_skip` events while snoozed, then a `reminder_sent` once snoozedUntil elapsed.

- [ ] **Step 3: Email signed-link snooze test**

Create another reminder for 2 minutes from now. When the email arrives, click "Snooze 1 hour". Confirm:
- Browser shows the green `✓ Snoozed` page with "we'll remind you at HH:MM"
- Firestore doc's `snoozedUntil` is updated to ~1h from now
- Worker logs `reminder_snoozed` event with `source: "email"`

Don't wait the full hour — clean up by editing the reminder (or deleting it).

- [ ] **Step 4: Negative-case checks**

Click the same email link a second time after the first snooze. Should also succeed (token still valid 24h, last-click-wins).

Manually craft an invalid token URL: `https://xelasphere.com/reminders/{any}/snooze?dur=10m&token=garbage`. Should render the "Invalid link" page with 400 status.

- [ ] **Step 5: Clean up — delete any test reminders**

In the chat modal, open and delete the test reminders.

- [ ] **Step 6: No commit — verification only.**

---

## Task 10: CHANGELOG entry + memory update

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `/root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md`

- [ ] **Step 1: Append M1.2 to CHANGELOG.md**

Find the existing `## Unreleased` section. Insert a new `### Added — Reminders M1.2 (snooze)` subsection BEFORE the existing `### Added — Reminders M1.1 (recurrence)` block:

```markdown
### Added — Reminders M1.2 (snooze)

Snooze on xelasphere.com (behind `REMINDERS_SNOOZE_ENABLED=true`).

- Three durations: **Snooze 10 min**, **Snooze 1 hour**, **Snooze until tomorrow** (= now + 24h).
- Two surfaces: signed-link buttons in delivered email + Snooze submenu in the inline-card kebab dropdown. Both hit the same `_apply_snooze` helper.
- Email tokens signed with `itsdangerous.URLSafeTimedSerializer` (24h max-age, salt `reminder-snooze`).
- Worker honors `snoozedUntil`: claims doc, sees future timestamp, releases the lock and skips. Logs `reminder_snooze_skip`.
- Eligible states: `scheduled`, `sending`, and `delivered`+recurring. Paused/failed/cancelled/delivered-one-off hide the option.
- Confirmation page (`_snoozed_confirmation.html`) handles all 6 outcomes (ok / expired / invalid / wrong_state / not_found / flag_off).
- Card subtitle shows the snoozed time and appends `(Snoozed)` until the snooze elapses.
- `REMINDERS_SNOOZE_ENABLED=false` (default) hides the kebab item, omits the email link block, returns 503 from both routes.
```

- [ ] **Step 2: Update project memory**

In `/root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md`, AFTER the existing M1.1 section, add:

```markdown
**M1.2 shipped (2026-05-DD):**
- Snooze live on xelasphere.com behind `REMINDERS_SNOOZE_ENABLED=true`
- 3 durations × 2 surfaces (email signed-link + in-app dropdown), one shared `_apply_snooze` helper
- Email tokens via itsdangerous, 24h validity
- Worker claim-then-skip pattern for snoozedUntil
- ~20 new tests across `tests/test_signed_links.py`, `tests/test_reminders_routes.py`, `tests/test_reminders_worker.py`
- M1.3 (web push) NOT yet — separate brainstorm
- Branch `feat/reminders-m1.2-snooze`. Plan: `docs/superpowers/plans/2026-05-19-reminders-m1.2-snooze.md`
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git add /root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md
git commit -m "docs(reminders/m1.2): CHANGELOG + memory entry for M1.2 ship"
```

---

## Self-Review Checklist

**1. Spec coverage**

| Spec section | Plan task |
|---|---|
| `signed_links.py` (make + verify, 24h max-age) | Task 2 |
| `_apply_snooze` helper (states, durations, ownership) | Task 3 |
| POST route (session auth) | Task 4 |
| GET route (signed token, HTML confirmation page) | Task 5 |
| Confirmation template (6 status branches) | Task 5 |
| Worker effective-fire-at skip | Task 6 |
| Email body snooze block (HTML + plain text) | Task 7 |
| In-app kebab Snooze submenu | Task 8 |
| Card subtitle `(Snoozed)` indicator | Task 8 |
| Kill switch `REMINDERS_SNOOZE_ENABLED` | Task 1 (added first so everything else can read it) |
| CHANGELOG + memory | Task 10 |
| E2E live verification | Task 9 |

No gaps.

**2. Placeholder scan**

```bash
grep -nE "TBD|TODO|fill in|implement later|similar to|appropriate error" \
  /root/xelas.io/xelasphere.io-/docs/superpowers/plans/2026-05-19-reminders-m1.2-snooze.md
```

Expected: zero matches (excluding this self-review section).

**3. Type consistency**

- `_apply_snooze(reminder_id, dur, user_id) -> tuple[int, dict]` — defined in Task 3, called by Task 4 and Task 5.
- `make_snooze_token(rid, uid) -> str` — defined Task 2, used Task 5 (verify) and Task 7 (mint).
- `verify_snooze_token(token) -> dict` — defined Task 2, used Task 5.
- `_SNOOZE_DURATIONS` keys: `"10m"`, `"1h"`, `"tomorrow"` — consistent across routes (Task 3), email links (Task 7), and JS submenu (Task 8).
- Firestore field name `snoozedUntil` — consistent: M1.1 init, M1.2 write (route), M1.2 read (worker), JS read (formatWhen).
- Log event names: `reminder_snoozed`, `reminder_snooze_skip`, `reminder_snooze_rejected` — match spec.

**4. Ambiguity**

- "Tomorrow" = exactly `now + 24h` (settled in M1 spec self-review). Test in Task 3 verifies.
- Email link `dur` query param uses string values, NOT numeric (matches `_SNOOZE_DURATIONS` keys).
- Worker reads `snoozedUntil` once (snapshot at claim time); no need to re-read inside the skip-block.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-reminders-m1.2-snooze.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task + two-stage review. Same pattern as M1.1.
2. **Inline Execution** — execute tasks in this session using executing-plans with checkpoints.

Which approach?

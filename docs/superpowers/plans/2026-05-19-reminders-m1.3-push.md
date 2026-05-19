# Reminders M1.3 — Web Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship browser push notifications for reminders. After the worker delivers the email, it also fans out an encrypted push to every browser the user has subscribed. Each notification carries two snooze action buttons ("10m", "1h") that reuse M1.2's snooze backend route.

**Architecture:** `push_dispatcher.py` does the fan-out with per-sub error isolation (410 → delete sub, 413 → truncate + retry). `push_routes.py` exposes subscribe/unsubscribe/vapid-public-key endpoints. `sw.js` is a self-contained service worker handling `push` (show notification) and `notificationclick` (snooze action or open chat). `pushSetup.js` handles SW registration, permission flow, and POST /push/subscribe. All gated on `PUSH_ENABLED` env flag. VAPID keypair is generated once per server (manual ops step + docs).

**Tech Stack:** Python 3.12, Flask, Firestore, `pywebpush>=2.0`, `py-vapid>=1.9`, vanilla JS for service worker + frontend.

**Spec:** [`docs/superpowers/specs/2026-05-19-reminders-m1.3-push-design.md`](../specs/2026-05-19-reminders-m1.3-push-design.md) commit `2c788a2`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `requirements-cpu.txt`, `requirements-gpu.txt` | **Modify** | Pin `pywebpush>=2.0,<3` and `py-vapid>=1.9,<2`. |
| `app/config.py` | **Modify** | Add `PUSH_ENABLED`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_CLAIMS_SUB`. |
| `docs/ops/push-vapid-setup.md` | **Create** | One-page procedure: generate keypair via `py_vapid`, install in env, restart. For incident response (key rotation). |
| `app/services/push_dispatcher.py` | **Create** | `send_push_for_reminder(reminder, user_id)`. Iterates subs, builds payload, calls `pywebpush.send`. Handles 410/413/429. No-op when flag off or VAPID missing. |
| `tests/test_push_dispatcher.py` | **Create** | Payload shape, truncation, 410/413 handling, no-op cases. |
| `app/blueprints/push_routes.py` | **Create** | `POST /push/subscribe`, `DELETE /push/subscribe`, `GET /push/vapid-public-key`, `POST /push/dismiss-prompt`. |
| `tests/test_push_routes.py` | **Create** | Subscribe (happy + idempotent), unsubscribe-all, vapid-public-key, flag-off (503), dismiss-prompt. |
| `app/__init__.py` | **Modify** | Register the new push blueprint. |
| `app/static/js/sw.js` | **Create** | Service worker. `push` + `notificationclick` events. Self-contained. |
| `app/static/js/pushSetup.js` | **Create** | Browser-support detection, SW registration, permission flow, subscribe call, banner trigger. |
| `app/templates/_push_permission_banner.html` | **Create** | Inline banner partial. |
| `app/templates/_settings_notifications_pane.html` | **Create** | Settings → Notifications tab content. Single toggle. |
| `app/templates/_settings_modal.html` | **Modify** | Add the Notifications tab + its content include. |
| `app/templates/index.html` | **Modify** | Load `pushSetup.js`. Inject `pushEnabled` config flag. |
| `app/static/js/remindersModule.js` | **Modify** | Banner show/hide logic post-first-reminder + "Not now" handler. |
| `tools/reminders_worker.py` | **Modify** | After successful SendGrid send, call `push_dispatcher.send_push_for_reminder(reminder, user_id)`. |
| `tests/test_reminders_worker.py` | **Modify** | Add test: worker calls push_dispatcher when subs exist; doesn't when flag off. |
| `CHANGELOG.md` | **Modify** | `Unreleased > Reminders M1.3` entry. |

---

## Task 1: Add dependency pins + kill switch + VAPID env scaffolding

**Files:**
- Modify: `requirements-cpu.txt`, `requirements-gpu.txt`
- Modify: `app/config.py`

- [ ] **Step 1: Pin the new deps**

Append to BOTH `requirements-cpu.txt` and `requirements-gpu.txt`:

```
pywebpush>=2.0,<3
py-vapid>=1.9,<2
```

- [ ] **Step 2: Install the deps**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/pip install pywebpush py-vapid 2>&1 | tail -5
```

Expected: "Successfully installed pywebpush-..." (first time) or "Requirement already satisfied" (if already there).

- [ ] **Step 3: Verify imports work**

```bash
/opt/xelasphere/venv/bin/python -c "
import pywebpush, py_vapid
print('pywebpush:', pywebpush.__version__ if hasattr(pywebpush, '__version__') else 'installed')
print('py_vapid: installed')
"
```

Expected: both imports succeed with no error.

- [ ] **Step 4: Add env flags to Config**

In `app/config.py`, find the existing `REMINDERS_SNOOZE_ENABLED` line (added in M1.2) and append the four new env vars BELOW it:

```python
PUSH_ENABLED = os.getenv('PUSH_ENABLED', 'false').lower() == 'true'
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
VAPID_CLAIMS_SUB = os.getenv('VAPID_CLAIMS_SUB', 'mailto:noreply@xelasphere.com')
```

- [ ] **Step 5: Smoke-test config loads**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -c "
from app.config import Config
print('PUSH_ENABLED:', Config.PUSH_ENABLED)
print('VAPID_PRIVATE_KEY set?', bool(Config.VAPID_PRIVATE_KEY))
print('VAPID_PUBLIC_KEY set?', bool(Config.VAPID_PUBLIC_KEY))
print('VAPID_CLAIMS_SUB:', Config.VAPID_CLAIMS_SUB)
"
```

Expected: `PUSH_ENABLED: False`, VAPID keys empty (default), `VAPID_CLAIMS_SUB: mailto:noreply@xelasphere.com`.

- [ ] **Step 6: Commit**

```bash
git add requirements-cpu.txt requirements-gpu.txt app/config.py
git commit -m "chore(reminders/m1.3): pin pywebpush + py-vapid; add PUSH_ENABLED + VAPID env scaffolding"
```

---

## Task 2: VAPID keypair generation procedure (docs)

**File:** Create `docs/ops/push-vapid-setup.md`

- [ ] **Step 1: Write the procedure doc**

Create `docs/ops/push-vapid-setup.md`:

````markdown
# Web Push — VAPID Keypair Setup

Web Push requires a VAPID keypair so push services (FCM, Mozilla, Apple)
can verify our sends. This is a one-time setup per server install. Document
this in your password manager / ops notebook because if `VAPID_PRIVATE_KEY`
leaks, you'll need to rotate (which invalidates every user's subscription).

## Generate the keypair

On the server (or any Python environment with `py_vapid` installed):

```bash
/opt/xelasphere/venv/bin/python <<'PY'
from py_vapid import Vapid01
v = Vapid01()
v.generate_keys()
# Encode keys as base64url strings (the format pywebpush expects)
import base64
priv_pem = v.private_pem().decode()
pub_raw  = v.public_key.public_numbers()
# Easier: use py_vapid's built-in encoder for the public key
from py_vapid.utils import b64urlencode
# Private: keep the PEM. Public: derive raw uncompressed point.
pub_bytes = v.public_key.public_bytes(
    encoding=__import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding']).Encoding.X962,
    format=__import__('cryptography.hazmat.primitives.serialization', fromlist=['PublicFormat']).PublicFormat.UncompressedPoint,
)
print("=== VAPID_PRIVATE_KEY (PEM, paste between quotes including the BEGIN/END lines) ===")
print(priv_pem)
print("=== VAPID_PUBLIC_KEY (base64url, single line) ===")
print(b64urlencode(pub_bytes).decode())
PY
```

Copy both outputs.

## Install in env

Edit `/etc/xelasphere/xelasphere.env`:

```
PUSH_ENABLED=false
VAPID_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
MII...
-----END PRIVATE KEY-----"
VAPID_PUBLIC_KEY=BNc...
VAPID_CLAIMS_SUB=mailto:noreply@xelasphere.com
```

The private key is multi-line PEM — quote it. Public key is single-line
base64url.

## Restart Gunicorn

```bash
sudo systemctl restart xelasphere
```

`HUP` alone is not enough — new env vars need a full restart per the
established pattern (see memory `feedback_systemd_env_reload.md`).

## Verify

```bash
curl -s https://xelasphere.com/push/vapid-public-key
```

Expected: `{"key": "BNc..."}` matching the value you set.

## Key rotation (incident response)

If `VAPID_PRIVATE_KEY` leaks or you suspect compromise:

1. Generate a new keypair (above).
2. Replace `VAPID_PRIVATE_KEY` and `VAPID_PUBLIC_KEY` in env.
3. `systemctl restart xelasphere`.
4. All existing user subscriptions become invalid. The next reminder for
   each subscribed user will surface as `push_send_error` with status 401.
5. Users must Settings → Notifications → Off, then On to re-subscribe.
6. Communicate the rotation (email blast or in-app banner).

There is no automated rotation flow in M1.3. Defer to M5+ if rotation
becomes frequent.

## Why VAPID

`VAPID_CLAIMS_SUB` (a `mailto:` URI) is a contact address push services
may reach if our sends start hitting their abuse thresholds. Keep it
monitored or at least forwardable.
````

- [ ] **Step 2: Commit**

```bash
git add docs/ops/push-vapid-setup.md
git commit -m "docs(reminders/m1.3): VAPID keypair setup + rotation procedure"
```

---

## Task 3: TDD — `push_dispatcher.py` payload building + truncation

**Files:**
- Create: `app/services/push_dispatcher.py`
- Create: `tests/test_push_dispatcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_push_dispatcher.py`:

```python
"""Unit tests for app.services.push_dispatcher.

Pure tests of payload building and truncation. The actual send logic
is tested as an integration test in Task 4."""
import json
import pytest


class TestBuildPayload:
    def test_payload_has_required_fields(self):
        from app.services.push_dispatcher import build_payload
        reminder = {
            "id": "rid_abc", "name": "Standup", "instructions": "10-min sync",
            "sourceChatId": "session_xyz",
        }
        payload = json.loads(build_payload(reminder, snooze_token="tok_123"))
        assert payload["title"] == "Standup"
        assert payload["body"] == "10-min sync"
        assert payload["data"]["rid"] == "rid_abc"
        assert payload["data"]["chat"] == "session_xyz"
        assert payload["data"]["snoozeToken"] == "tok_123"
        assert len(payload["actions"]) == 2
        assert payload["actions"][0]["action"] == "snooze_10m"
        assert payload["actions"][1]["action"] == "snooze_1h"

    def test_body_falls_back_to_name_when_no_instructions(self):
        from app.services.push_dispatcher import build_payload
        reminder = {"id": "rid_abc", "name": "Just a title", "sourceChatId": None}
        payload = json.loads(build_payload(reminder, snooze_token="tok"))
        assert payload["body"] == "Just a title"

    def test_chat_defaults_to_null_when_no_sourceChatId(self):
        from app.services.push_dispatcher import build_payload
        reminder = {"id": "rid_abc", "name": "x"}
        payload = json.loads(build_payload(reminder, snooze_token="tok"))
        assert payload["data"]["chat"] is None


class TestTruncate:
    def test_short_body_unchanged(self):
        from app.services.push_dispatcher import _truncate
        assert _truncate("hello", 100) == "hello"

    def test_long_body_truncated_with_ellipsis(self):
        from app.services.push_dispatcher import _truncate
        text = "x" * 100
        result = _truncate(text, 50)
        assert len(result) <= 50
        assert result.endswith("…")

    def test_truncate_at_exact_limit(self):
        from app.services.push_dispatcher import _truncate
        assert _truncate("abcdef", 6) == "abcdef"

    def test_truncate_handles_multibyte_emoji(self):
        from app.services.push_dispatcher import _truncate
        # '👨‍👩‍👧' is a multi-codepoint family emoji. Truncation should not
        # produce a malformed string (no unpaired surrogates).
        text = "Hello 👨‍👩‍👧 there!"
        result = _truncate(text, 8)
        assert len(result) <= 8
        # No errors when encoding the result as UTF-8
        result.encode("utf-8")
```

- [ ] **Step 2: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_dispatcher.py -v
```

Expected: ModuleNotFoundError on `app.services.push_dispatcher`.

- [ ] **Step 3: Implement payload + truncation**

Create `app/services/push_dispatcher.py`:

```python
"""Web Push fan-out for reminder deliveries.

After the worker successfully sends the email, it calls
send_push_for_reminder(reminder, user_id) which iterates the user's
pushSubscriptions and sends an encrypted notification to each.

Per-sub error isolation: one bad subscription doesn't fail the whole
reminder. 410 Gone deletes the sub. 413 Payload Too Large retries once
with a shorter body. Other errors are logged and skipped.

The notification payload includes a freshly-minted snooze token so the
two action buttons ("10m", "1h") work without a session. Action click
in the service worker fires GET /reminders/<id>/snooze?dur=...&token=...
— exactly the M1.2 route email links also use.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Web Push spec caps encrypted payloads at ~4KB. We aim well under to
# leave room for the JSON envelope + encryption overhead.
PAYLOAD_BODY_LIMIT = 2048
PAYLOAD_BODY_RETRY_LIMIT = 256


def _truncate(text: str, limit: int) -> str:
    """Truncate to at most `limit` characters, appending '…' if cut."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def build_payload(reminder: dict, *, snooze_token: str, body_limit: int = PAYLOAD_BODY_LIMIT) -> str:
    """Build the JSON payload string the service worker will receive on push.

    Shape (kept stable — sw.js depends on this):
        {
          "title": str,
          "body":  str,
          "data": {"rid": str, "chat": str|null, "snoozeToken": str},
          "actions": [{"action": "snooze_10m", "title": "10m"},
                       {"action": "snooze_1h",  "title": "1h"}]
        }
    """
    name = reminder.get("name") or ""
    instructions = (reminder.get("instructions") or "").strip()
    body = _truncate(instructions if instructions else name, body_limit)
    return json.dumps({
        "title": name,
        "body": body,
        "data": {
            "rid": reminder.get("id"),
            "chat": reminder.get("sourceChatId"),
            "snoozeToken": snooze_token,
        },
        "actions": [
            {"action": "snooze_10m", "title": "10m"},
            {"action": "snooze_1h", "title": "1h"},
        ],
    })


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# send_push_for_reminder is implemented in Task 4 (uses Firestore + pywebpush).
```

- [ ] **Step 4: Run tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_dispatcher.py -v
```

Expected: All 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/push_dispatcher.py tests/test_push_dispatcher.py
git commit -m "feat(reminders/m1.3): push payload builder + truncation

Pure functions: build_payload returns the JSON the service worker will
receive on push. _truncate caps body to PAYLOAD_BODY_LIMIT (2KB) with
'…' suffix. 8 unit tests."
```

---

## Task 4: TDD — `send_push_for_reminder` fan-out with 410/413/no-op handling

**Files:**
- Modify: `app/services/push_dispatcher.py`
- Modify: `tests/test_push_dispatcher.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/test_push_dispatcher.py`:

```python
from unittest.mock import MagicMock, patch


class TestSendPushForReminder:
    """send_push_for_reminder iterates the user's pushSubscriptions and
    calls pywebpush.send for each. Tests cover the success path, 410 cleanup,
    413 retry, and no-op when flag off / VAPID missing."""

    def _reminder(self):
        return {
            "id": "rid_x", "name": "Standup", "instructions": "team sync",
            "sourceChatId": "session_abc",
        }

    def _sub_doc(self, subId="sub_1", endpoint="https://fcm.../send/abc"):
        snap = MagicMock()
        snap.id = subId
        snap.exists = True
        snap.to_dict.return_value = {
            "endpoint": endpoint,
            "keys": {"p256dh": "p", "auth": "a"},
        }
        snap.reference = MagicMock()
        return snap

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.Config")
    def test_no_op_when_flag_off(self, mock_config, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = False
        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")
        mock_pywebpush.webpush.assert_not_called()

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.Config")
    def test_no_op_when_vapid_missing(self, mock_config, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = ""  # missing
        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")
        mock_pywebpush.webpush.assert_not_called()

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.make_snooze_token")
    @patch("app.services.push_dispatcher.Config")
    def test_happy_path_sends_to_each_sub(self, mock_config, mock_token, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = "PEM"
        mock_config.VAPID_CLAIMS_SUB = "mailto:test@x.io"
        mock_token.return_value = "tok_xyz"

        sub1 = self._sub_doc(subId="sub_1")
        sub2 = self._sub_doc(subId="sub_2", endpoint="https://updates.../sub2")
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub1, sub2]

        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")

        assert mock_pywebpush.webpush.call_count == 2
        # Each sub's reference.update is called with lastSeenAt
        assert sub1.reference.update.called
        assert sub2.reference.update.called

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.make_snooze_token")
    @patch("app.services.push_dispatcher.Config")
    def test_410_deletes_subscription(self, mock_config, mock_token, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = "PEM"
        mock_config.VAPID_CLAIMS_SUB = "mailto:t@x.io"
        mock_token.return_value = "tok"

        sub = self._sub_doc()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub]

        # Mock pywebpush.webpush raising 410
        class _FakeResponse:
            status_code = 410
        class _FakeException(Exception):
            response = _FakeResponse()
        mock_pywebpush.WebPushException = _FakeException
        mock_pywebpush.webpush.side_effect = _FakeException("Gone")

        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")

        # The sub's reference.delete should have been called
        assert sub.reference.delete.called

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.make_snooze_token")
    @patch("app.services.push_dispatcher.Config")
    def test_413_retries_with_truncated_body(self, mock_config, mock_token, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = "PEM"
        mock_config.VAPID_CLAIMS_SUB = "mailto:t@x.io"
        mock_token.return_value = "tok"

        sub = self._sub_doc()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub]

        class _FakeResponse413:
            status_code = 413
        class _FakeException(Exception):
            response = _FakeResponse413()
        mock_pywebpush.WebPushException = _FakeException
        # First call raises 413, second call succeeds
        mock_pywebpush.webpush.side_effect = [_FakeException("Too Large"), None]

        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")

        # webpush was called twice — first with full body, second with truncated
        assert mock_pywebpush.webpush.call_count == 2
        # Second call's data argument should be shorter than first
        first_data = mock_pywebpush.webpush.call_args_list[0].kwargs.get("data", "")
        second_data = mock_pywebpush.webpush.call_args_list[1].kwargs.get("data", "")
        assert len(second_data) < len(first_data)
```

- [ ] **Step 2: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_dispatcher.py::TestSendPushForReminder -v
```

Expected: All 5 FAIL because `send_push_for_reminder` doesn't exist yet.

- [ ] **Step 3: Implement `send_push_for_reminder`**

Append to `app/services/push_dispatcher.py`:

```python
import pywebpush
from app.config import Config
from app.config import db
from app.services.signed_links import make_snooze_token


def send_push_for_reminder(reminder: dict, user_id: str) -> None:
    """Fan out a push notification to every browser subscribed under user_id.

    No-op when PUSH_ENABLED is False or VAPID_PRIVATE_KEY is missing — the
    email path is unaffected. Per-sub errors are isolated; one bad sub
    doesn't stop the worker."""
    if not Config.PUSH_ENABLED:
        return
    if not Config.VAPID_PRIVATE_KEY:
        logger.warning(
            "push_disabled_no_vapid",
            extra={"extra_fields": {"rid": reminder.get("id"), "uid": user_id}},
        )
        return

    subs = list(
        db.collection("users").document(user_id)
          .collection("pushSubscriptions").stream()
    )
    if not subs:
        return  # email-only user

    token = make_snooze_token(reminder.get("id"), user_id)
    payload = build_payload(reminder, snooze_token=token)
    payload_short = build_payload(
        reminder, snooze_token=token, body_limit=PAYLOAD_BODY_RETRY_LIMIT
    )

    successes = expired = errors = 0
    for snap in subs:
        data = snap.to_dict() or {}
        endpoint = data.get("endpoint")
        keys = data.get("keys")
        if not endpoint or not keys:
            errors += 1
            continue

        try:
            pywebpush.webpush(
                subscription_info={"endpoint": endpoint, "keys": keys},
                data=payload,
                vapid_private_key=Config.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": Config.VAPID_CLAIMS_SUB},
            )
            successes += 1
            try:
                snap.reference.update({"lastSeenAt": _now_iso(), "lastError": None})
            except Exception:
                pass  # diagnostic write; not critical
        except pywebpush.WebPushException as exc:
            status = exc.response.status_code if getattr(exc, "response", None) else 0
            if status == 410:
                try:
                    snap.reference.delete()
                except Exception:
                    pass
                expired += 1
                logger.info(
                    "push_subscription_expired",
                    extra={"extra_fields": {
                        "subId": snap.id, "uid": user_id,
                        "userAgent": data.get("userAgent", ""),
                    }},
                )
            elif status == 413:
                try:
                    pywebpush.webpush(
                        subscription_info={"endpoint": endpoint, "keys": keys},
                        data=payload_short,
                        vapid_private_key=Config.VAPID_PRIVATE_KEY,
                        vapid_claims={"sub": Config.VAPID_CLAIMS_SUB},
                    )
                    successes += 1
                    logger.info(
                        "push_payload_too_large",
                        extra={"extra_fields": {
                            "rid": reminder.get("id"), "subId": snap.id,
                            "originalBytes": len(payload),
                            "truncatedBytes": len(payload_short),
                        }},
                    )
                except Exception as exc2:
                    errors += 1
                    logger.warning(
                        "push_send_error",
                        extra={"extra_fields": {
                            "rid": reminder.get("id"), "subId": snap.id,
                            "status": 413, "error": str(exc2)[:200],
                        }},
                    )
            else:
                errors += 1
                logger.warning(
                    "push_send_error",
                    extra={"extra_fields": {
                        "rid": reminder.get("id"), "subId": snap.id,
                        "status": status, "error": str(exc)[:200],
                    }},
                )
                try:
                    snap.reference.update({"lastError": str(exc)[:200]})
                except Exception:
                    pass
        except Exception as exc:
            errors += 1
            logger.warning(
                "push_send_error",
                extra={"extra_fields": {
                    "rid": reminder.get("id"), "subId": snap.id,
                    "status": 0, "error": str(exc)[:200],
                }},
            )

    logger.info(
        "push_dispatched",
        extra={"extra_fields": {
            "rid": reminder.get("id"), "uid": user_id,
            "subCount": len(subs),
            "successes": successes, "expired": expired, "failures": errors,
        }},
    )
```

- [ ] **Step 4: Run tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_dispatcher.py -v
```

Expected: All tests pass (8 from Task 3 + 5 new = 13 total).

The 410 and 413 tests rely on monkeypatched `pywebpush.WebPushException`. The `except pywebpush.WebPushException` in the implementation needs to catch the class the test sets. The pattern `mock_pywebpush.WebPushException = _FakeException` makes the test work, but the implementation has `except pywebpush.WebPushException` which references the live class. Verify that the patched module's `WebPushException` is used in the except clause at test time — it is, because the import `import pywebpush` is module-level and `pywebpush.WebPushException` is looked up at exception-catch time.

- [ ] **Step 5: Commit**

```bash
git add app/services/push_dispatcher.py tests/test_push_dispatcher.py
git commit -m "feat(reminders/m1.3): send_push_for_reminder fan-out with 410/413 handling

Per-sub try/except; 410 deletes sub; 413 retries with truncated body.
Logs reminder_dispatched + push_subscription_expired + push_payload_too_large
+ push_send_error. No-op when PUSH_ENABLED off or VAPID missing. 5 tests."
```

---

## Task 5: TDD — POST/DELETE /push/subscribe + GET /push/vapid-public-key + dismiss-prompt routes

**Files:**
- Create: `app/blueprints/push_routes.py`
- Create: `tests/test_push_routes.py`
- Modify: `app/__init__.py` (register blueprint)

- [ ] **Step 1: Write failing tests**

Create `tests/test_push_routes.py`:

```python
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
    return {"user": {"uid": "u1"}}


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

        resp = client.post(
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

        resp = client.post(
            "/push/subscribe",
            json={
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                "keys": {"p256dh": "p", "auth": "a"},
                "userAgent": "ua",
            },
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        # existing sub's reference.update was called (lastSeenAt refresh)
        assert existing.reference.update.called

    @patch("app.blueprints.push_routes.Config")
    def test_subscribe_returns_503_when_flag_off(self, mock_config, client, auth_session):
        mock_config.PUSH_ENABLED = False
        resp = client.post(
            "/push/subscribe",
            json={"endpoint": "x", "keys": {"p256dh": "p", "auth": "a"}},
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 503

    @patch("app.blueprints.push_routes.Config")
    def test_subscribe_missing_endpoint_returns_400(self, mock_config, client, auth_session):
        mock_config.PUSH_ENABLED = True
        resp = client.post(
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

        resp = client.delete("/push/subscribe", headers={"X-CSRFToken": "test"})
        assert resp.status_code == 200
        assert sub1.reference.delete.called
        assert sub2.reference.delete.called


class TestDismissPrompt:
    @patch("app.blueprints.push_routes.db")
    def test_dismiss_prompt_writes_timestamp(self, mock_db, client, auth_session):
        doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = doc_ref

        resp = client.post("/push/dismiss-prompt", headers={"X-CSRFToken": "test"})
        assert resp.status_code == 200
        update_call = doc_ref.set.call_args
        # Verify the call included pushPromptDismissedAt
        assert "pushPromptDismissedAt" in update_call.args[0]
```

- [ ] **Step 2: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_routes.py -v
```

Expected: ModuleNotFoundError on `app.blueprints.push_routes`.

- [ ] **Step 3: Create the blueprint**

Create `app/blueprints/push_routes.py`:

```python
"""Web Push routes for subscribe / unsubscribe / VAPID key + prompt dismiss."""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from flask import Blueprint, jsonify, request, session

from app.config import Config, db
from app.blueprints.shared import login_required, error_handler, csrf_required

logger = logging.getLogger(__name__)
push_bp = Blueprint("push", __name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@push_bp.route("/push/vapid-public-key", methods=["GET"])
@error_handler
def vapid_public_key():
    """Returns the base64url-encoded VAPID public key for the frontend
    to use with pushManager.subscribe(). Unauthenticated by design — the
    key is meant to be public."""
    if not Config.PUSH_ENABLED:
        return jsonify({"error": "push disabled"}), 503
    return jsonify({"key": Config.VAPID_PUBLIC_KEY}), 200


@push_bp.route("/push/subscribe", methods=["POST"])
@error_handler
@login_required
@csrf_required
def subscribe():
    """Register the current browser's push subscription. Idempotent on
    duplicate endpoints (updates lastSeenAt instead of creating a second
    doc)."""
    if not Config.PUSH_ENABLED:
        return jsonify({"error": "push disabled"}), 503

    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    if not endpoint or not isinstance(keys, dict) or "p256dh" not in keys or "auth" not in keys:
        return jsonify({"error": "endpoint and keys.{p256dh,auth} required"}), 400

    uid = session["user"]["uid"]
    subs_coll = db.collection("users").document(uid).collection("pushSubscriptions")

    # Idempotent — if a sub with this endpoint exists, update lastSeenAt.
    existing = list(subs_coll.where("endpoint", "==", endpoint).stream())
    now = _now_iso()
    if existing:
        ref = existing[0].reference
        ref.update({
            "lastSeenAt": now,
            "userAgent": payload.get("userAgent", ""),
            "keys": keys,  # refresh in case the user agent re-rotated keys
        })
        return jsonify({"subId": existing[0].id, "updated": True}), 200

    doc_ref = subs_coll.document()
    doc_ref.set({
        "endpoint": endpoint,
        "keys": keys,
        "userAgent": payload.get("userAgent", ""),
        "createdAt": now,
        "lastSeenAt": now,
        "lastError": None,
    })
    logger.info(
        "push_subscribed",
        extra={"extra_fields": {"subId": doc_ref.id, "uid": uid, "ua": payload.get("userAgent", "")}},
    )
    return jsonify({"subId": doc_ref.id, "updated": False}), 201


@push_bp.route("/push/subscribe", methods=["DELETE"])
@error_handler
@login_required
@csrf_required
def unsubscribe_all():
    """Delete ALL of the current user's push subscriptions. Used by the
    Settings toggle Off."""
    if not Config.PUSH_ENABLED:
        return jsonify({"error": "push disabled"}), 503
    uid = session["user"]["uid"]
    subs = list(
        db.collection("users").document(uid).collection("pushSubscriptions").stream()
    )
    count = 0
    for snap in subs:
        try:
            snap.reference.delete()
            count += 1
        except Exception:
            pass
    logger.info(
        "push_unsubscribed_all",
        extra={"extra_fields": {"uid": uid, "count": count}},
    )
    return jsonify({"deleted": count}), 200


@push_bp.route("/push/dismiss-prompt", methods=["POST"])
@error_handler
@login_required
@csrf_required
def dismiss_prompt():
    """Mark that the user dismissed the inline 'Enable push?' banner.
    Banner re-shows after 30 days if still no subscriptions."""
    uid = session["user"]["uid"]
    db.collection("users").document(uid).set(
        {"pushPromptDismissedAt": _now_iso()}, merge=True,
    )
    return jsonify({"ok": True}), 200
```

- [ ] **Step 4: Register the blueprint**

In `app/__init__.py`, find where other blueprints are registered (search for `register_blueprint`). Add:

```python
from app.blueprints.push_routes import push_bp
app.register_blueprint(push_bp)
```

Place it next to the existing reminders blueprint registration.

- [ ] **Step 5: Run tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_routes.py -v 2>&1 | tail -15
```

Expected: All 8 PASS.

If the `_bypass_csrf` fixture pattern doesn't match what's already used by `test_reminders_routes.py`, copy that test file's exact fixture setup (`auth_session` fixture etc.) verbatim. Don't invent a new pattern.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/push_routes.py tests/test_push_routes.py app/__init__.py
git commit -m "feat(reminders/m1.3): push routes (subscribe, unsubscribe, vapid-key, dismiss-prompt)

POST /push/subscribe idempotent on duplicate endpoint. DELETE deletes all.
GET /push/vapid-public-key is public (key is meant to be discoverable).
POST /push/dismiss-prompt sets users/{uid}.pushPromptDismissedAt.
All gated on PUSH_ENABLED. 8 integration tests."
```

---

## Task 6: Worker integration — call `send_push_for_reminder` after SendGrid send

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/test_reminders_worker.py`:

```python
def test_process_reminder_calls_push_dispatcher_on_success(monkeypatch):
    """After successful SendGrid send (non-recurring, non-snoozed), the
    worker calls push_dispatcher.send_push_for_reminder."""
    from tools import reminders_worker

    # Mock SendGrid success
    from app.services import email_sender
    class _OkResult:
        status_code = 202
    monkeypatch.setattr(email_sender, "send_transactional_email", lambda **kw: _OkResult())

    # Spy on push_dispatcher
    push_calls = []
    from app.services import push_dispatcher
    monkeypatch.setattr(
        push_dispatcher, "send_push_for_reminder",
        lambda reminder, user_id: push_calls.append((reminder, user_id)),
    )

    updates = []
    class _FakeDocRef:
        def update(self, data): updates.append(data)
    doc_ref = _FakeDocRef()

    reminder = {
        "id": "rid_p", "name": "with push", "userId": "u_push",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "snoozedUntil": None,
        "recurrence": None,
        "attemptCount": 0, "deliveryCount": 0,
        "sourceChatId": None, "timezone": "UTC",
    }
    reminders_worker.process_reminder(doc_ref, reminder, user_email="a@b.com")

    assert len(push_calls) == 1
    assert push_calls[0][1] == "u_push"


def test_process_reminder_does_not_call_push_dispatcher_when_skipped(monkeypatch):
    """When the reminder is skipped due to snoozedUntil, the worker does
    NOT call push_dispatcher."""
    from tools import reminders_worker
    from datetime import datetime, timezone, timedelta

    # SendGrid would succeed if called — but it should NOT be called
    sg_called = []
    from app.services import email_sender
    monkeypatch.setattr(
        email_sender, "send_transactional_email",
        lambda **kw: sg_called.append(kw) or type("R", (), {"status_code": 202})(),
    )

    push_calls = []
    from app.services import push_dispatcher
    monkeypatch.setattr(
        push_dispatcher, "send_push_for_reminder",
        lambda reminder, user_id: push_calls.append((reminder, user_id)),
    )

    updates = []
    class _FakeDocRef:
        def update(self, data): updates.append(data)
    doc_ref = _FakeDocRef()

    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    reminder = {
        "id": "rid_snooze", "name": "snoozed", "userId": "u_x",
        "fireAt": "2026-05-19T08:00:00+00:00",
        "snoozedUntil": future,
        "recurrence": None,
        "attemptCount": 0, "deliveryCount": 0,
        "sourceChatId": None, "timezone": "UTC",
    }
    reminders_worker.process_reminder(doc_ref, reminder, user_email="a@b.com")

    assert push_calls == [], "Expected push to be skipped along with email"
    assert sg_called == [], "Expected SendGrid to be skipped too"
```

- [ ] **Step 2: Run failing tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py::test_process_reminder_calls_push_dispatcher_on_success tests/test_reminders_worker.py::test_process_reminder_does_not_call_push_dispatcher_when_skipped -v
```

Expected: first FAILS (push_dispatcher not called yet), second PASSES (skip path doesn't reach SendGrid OR push).

- [ ] **Step 3: Wire the push call into `process_reminder`**

In `tools/reminders_worker.py`, find the success path inside `process_reminder` — after `result = send_transactional_email(...)` and the M1.1 recurring-advance branch checks. Specifically, the push call should happen AFTER SendGrid succeeded and BEFORE the recurring-advance OR terminal-delivered Firestore writes. Insert:

```python
    # M1.3: fan out push notifications to all subscribed browsers.
    # Per-sub errors are isolated inside the dispatcher; this call cannot
    # fail the worker.
    try:
        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(reminder, reminder.get("userId"))
    except Exception as exc:
        logger.warning(
            "push_dispatch_exception",
            extra={"extra_fields": {"name": name, "rid": reminder.get("id"), "error": str(exc)[:200]}},
        )
```

Place this AFTER the `try: result = send_transactional_email(...)` succeeds (i.e., after the except block returns/the success path is confirmed), but BEFORE the recurring-advance check. Search for `from app.services.recurrence import next_fire_at` to find the right spot — push call goes IMMEDIATELY BEFORE that import block.

The outer try/except is defense-in-depth: even if `push_dispatcher` somehow blows up at the module level (e.g., circular import surfacing), the worker still completes the email delivery.

- [ ] **Step 4: Run tests + full worker suite**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_reminders_worker.py -v 2>&1 | tail -10
```

Expected: All worker tests pass (the M1.1 advance, M1.2 snooze, and new M1.3 push tests).

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/m1.3): worker fans out push after successful email send

After SendGrid success, calls send_push_for_reminder. Skip path
(snooze/etc.) does not reach push dispatcher. Worker is unaffected by
push errors (dispatcher isolates them internally). 2 integration tests."
```

---

## Task 7: Service worker `sw.js`

**File:** Create `app/static/js/sw.js`

- [ ] **Step 1: Create the service worker**

Create `app/static/js/sw.js`:

```javascript
// Reminders M1.3 — Service Worker for web push.
//
// Two events handled:
//   - 'push': render a native notification with title/body/data/actions
//   - 'notificationclick': either snooze (action button) or open chat (body)
//
// All other fetches pass through (no caching, no interception).
// The SW is registered at root scope by pushSetup.js.

self.addEventListener('push', (event) => {
    let payload;
    try {
        payload = event.data ? event.data.json() : {};
    } catch (e) {
        payload = { title: 'Reminder', body: 'You have a reminder.' };
    }

    const title = payload.title || 'Reminder';
    const options = {
        body: payload.body || '',
        icon: '/static/img/icon-192.png',
        badge: '/static/img/icon-192.png',
        data: payload.data || {},
        actions: payload.actions || [],
        // Keep the notification visible until the user interacts.
        // requireInteraction is supported by Chrome/Edge; Firefox/Safari ignore.
        requireInteraction: true,
    };

    event.waitUntil(self.registration.showNotification(title, options));
});


self.addEventListener('notificationclick', (event) => {
    const notif = event.notification;
    const data = notif.data || {};
    const action = event.action;

    // Always close the notification on click.
    notif.close();

    // Action buttons: snooze 10m / 1h — fire GET to the M1.2 snooze route.
    if (action === 'snooze_10m' || action === 'snooze_1h') {
        const dur = action === 'snooze_10m' ? '10m' : '1h';
        const url = `/reminders/${encodeURIComponent(data.rid)}/snooze?dur=${dur}&token=${encodeURIComponent(data.snoozeToken)}`;
        event.waitUntil(
            fetch(url, { method: 'GET', credentials: 'omit' })
                .catch((err) => console.warn('snooze fetch failed', err))
        );
        return;
    }

    // Body click (no action): open or focus the chat tab.
    const chatId = data.chat;
    const target = chatId ? `/c/${encodeURIComponent(chatId)}` : '/';
    event.waitUntil(
        (async () => {
            const allClients = await self.clients.matchAll({
                type: 'window',
                includeUncontrolled: true,
            });
            for (const client of allClients) {
                if (client.url.includes(target) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (self.clients.openWindow) {
                return self.clients.openWindow(target);
            }
        })()
    );
});


// On install, activate immediately. Don't wait for the next page load.
self.addEventListener('install', (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});
```

- [ ] **Step 2: Verify it parses**

```bash
cd /root/xelas.io/xelasphere.io- && node -c app/static/js/sw.js && echo "syntax OK"
```

Expected: `syntax OK`. If `node` isn't available, do a brace-balance check:

```bash
/opt/xelasphere/venv/bin/python -c "
with open('/root/xelas.io/xelasphere.io-/app/static/js/sw.js') as f:
    c = f.read()
print('braces:', c.count('{'), '==', c.count('}'))
print('parens:', c.count('('), '==', c.count(')'))
"
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/sw.js
git commit -m "feat(reminders/m1.3): service worker (push + notificationclick handlers)

push event → showNotification with actions. notificationclick →
snooze action fires GET to M1.2 route, body click opens chat deeplink.
requireInteraction=true; skipWaiting + clients.claim on install."
```

---

## Task 8: `pushSetup.js` frontend lifecycle

**File:** Create `app/static/js/pushSetup.js`

- [ ] **Step 1: Create the frontend bootstrap**

Create `app/static/js/pushSetup.js`:

```javascript
// Reminders M1.3 — Push notification frontend bootstrap.
//
// Runs on DOMContentLoaded. Detects browser support, registers the
// service worker if needed, and exposes `window.RemindersModule.push`
// with helper methods used by the inline banner + Settings toggle.

(function () {
    'use strict';

    function getCsrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        const input = document.querySelector('input[name="csrf_token"]');
        if (input) return input.value;
        const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function urlBase64ToUint8Array(base64String) {
        // Web Push spec wants the VAPID public key as a Uint8Array.
        // Convert base64url → Uint8Array.
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        const raw = window.atob(base64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; ++i) {
            arr[i] = raw.charCodeAt(i);
        }
        return arr;
    }

    function isIOSSafariNonPWA() {
        const ua = navigator.userAgent;
        const isIOS = /iPad|iPhone|iPod/.test(ua);
        const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                             window.navigator.standalone === true;
        return isIOS && !isStandalone;
    }

    function isSupported() {
        return ('Notification' in window) &&
               ('serviceWorker' in navigator) &&
               ('PushManager' in window) &&
               !isIOSSafariNonPWA();
    }

    async function getRegistration() {
        if (!('serviceWorker' in navigator)) return null;
        let reg = await navigator.serviceWorker.getRegistration('/');
        if (!reg) {
            reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        }
        return reg;
    }

    async function isSubscribed() {
        if (!isSupported()) return false;
        const reg = await getRegistration();
        if (!reg) return false;
        const sub = await reg.pushManager.getSubscription();
        return sub !== null;
    }

    async function subscribe() {
        if (!isSupported()) return { ok: false, reason: 'unsupported' };

        const perm = await Notification.requestPermission();
        if (perm !== 'granted') return { ok: false, reason: 'denied' };

        const reg = await getRegistration();
        const vapidResp = await fetch('/push/vapid-public-key', { credentials: 'same-origin' });
        if (!vapidResp.ok) return { ok: false, reason: 'vapid-unavailable' };
        const { key } = await vapidResp.json();
        if (!key) return { ok: false, reason: 'vapid-missing' };

        let sub;
        try {
            sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(key),
            });
        } catch (err) {
            console.warn('pushManager.subscribe failed', err);
            return { ok: false, reason: 'subscribe-failed', error: String(err) };
        }

        const payload = sub.toJSON();
        const resp = await fetch('/push/subscribe', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
            body: JSON.stringify({
                endpoint: sub.endpoint,
                keys: payload.keys,
                userAgent: navigator.userAgent,
            }),
        });
        if (!resp.ok) return { ok: false, reason: 'server', status: resp.status };
        return { ok: true, subId: (await resp.json()).subId };
    }

    async function unsubscribeAll() {
        // Server-side: delete all subscription docs.
        await fetch('/push/subscribe', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': getCsrf() },
        });
        // Browser-side: unsubscribe this device's PushSubscription too.
        if ('serviceWorker' in navigator) {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (reg) {
                const sub = await reg.pushManager.getSubscription();
                if (sub) await sub.unsubscribe();
            }
        }
    }

    async function dismissPrompt() {
        await fetch('/push/dismiss-prompt', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': getCsrf() },
        });
    }

    // Expose on the existing RemindersModule namespace.
    window.RemindersModule = window.RemindersModule || {};
    window.RemindersModule.push = {
        isSupported,
        isSubscribed,
        subscribe,
        unsubscribeAll,
        dismissPrompt,
    };

    // On DOMContentLoaded, pre-register the SW for users who already have
    // permission granted — so notifications can fire even before they
    // hit Settings. No-op otherwise.
    document.addEventListener('DOMContentLoaded', async () => {
        if (!isSupported()) return;
        if (Notification.permission !== 'granted') return;
        try {
            await getRegistration();
        } catch (err) {
            console.warn('SW pre-register failed', err);
        }
    });
})();
```

- [ ] **Step 2: Syntax check**

```bash
cd /root/xelas.io/xelasphere.io- && node -c app/static/js/pushSetup.js && echo "syntax OK"
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/pushSetup.js
git commit -m "feat(reminders/m1.3): pushSetup.js — browser-support detection + subscribe flow

Exposes window.RemindersModule.push.{isSupported, isSubscribed,
subscribe, unsubscribeAll, dismissPrompt}. Includes urlBase64ToUint8Array
helper for VAPID key conversion. Detects iOS Safari (non-PWA) and exits
silently."
```

---

## Task 9: Permission banner + Settings → Notifications pane

**Files:**
- Create: `app/templates/_push_permission_banner.html`
- Create: `app/templates/_settings_notifications_pane.html`
- Modify: `app/templates/_settings_modal.html` (add tab + include)
- Modify: `app/templates/index.html` (load pushSetup.js, inject pushEnabled config)
- Modify: `app/static/js/remindersModule.js` (banner show/hide post-first-reminder)

- [ ] **Step 1: Create the banner partial**

Create `app/templates/_push_permission_banner.html`:

```html
{# M1.3 — Inline push permission banner. Rendered conditionally by JS
   immediately after the user creates their first reminder. The visibility
   gate is in remindersModule.js (eligibility + subscription check + 30-day
   re-prompt). #}
<div class="push-permission-banner" data-push-banner hidden>
  <div class="push-permission-banner__icon" aria-hidden="true">🔔</div>
  <div class="push-permission-banner__body">
    <p class="push-permission-banner__title">Want a browser ping too?</p>
    <p class="push-permission-banner__sub">Get notified even when this tab isn't open.</p>
  </div>
  <div class="push-permission-banner__actions">
    <button type="button" class="btn btn--primary" data-push-banner-enable>Enable push</button>
    <button type="button" class="btn btn--ghost" data-push-banner-dismiss>Not now</button>
    <button type="button" class="push-permission-banner__close" data-push-banner-close aria-label="Dismiss">✕</button>
  </div>
</div>
```

- [ ] **Step 2: Create the Settings notifications pane**

Create `app/templates/_settings_notifications_pane.html`:

```html
{# M1.3 — Settings → Notifications pane. Single toggle. When toggled ON
   triggers the same subscribe flow as the inline banner. When toggled
   OFF deletes ALL user's pushSubscriptions. #}
<div class="settings-pane" data-pane="notifications" hidden>
  <div class="settings-pane__field">
    <label class="settings-toggle">
      <span class="settings-toggle__label">Browser notifications</span>
      <input type="checkbox" data-push-settings-toggle>
      <span class="settings-toggle__slider" aria-hidden="true"></span>
    </label>
  </div>
  <p class="settings-pane__hint">
    When ON: this browser receives push notifications for your reminders.<br>
    When OFF: all subscribed devices are removed; no push is sent.
  </p>
</div>
```

- [ ] **Step 3: Add the Notifications tab to the settings modal**

In `app/templates/_settings_modal.html`, find the existing tab list (search for `data-tab="schedules"` or similar Schedules-tab markup). Add a new tab BEFORE or AFTER Schedules:

```html
<button class="settings-modal__tab-btn" data-tab="notifications" type="button">Notifications</button>
```

And include the pane content where the other panes are rendered:

```html
{% include "_settings_notifications_pane.html" %}
```

The exact insertion depends on the existing tab pattern. Mirror what `_settings_schedules_pane.html` does (if it exists), or copy the include for whatever existing pane uses the same pattern.

- [ ] **Step 4: Load pushSetup.js + inject pushEnabled config in index.html**

In `app/templates/index.html`, find where `remindersModule.js` is loaded. ADD `pushSetup.js` to load BEFORE it (so `window.RemindersModule.push` exists when remindersModule.js initializes):

```html
<script src="{{ url_for('static', filename='js/pushSetup.js') }}"></script>
<script defer src="{{ url_for('static', filename='js/remindersModule.js') }}"></script>
```

Find the existing `snoozeEnabled` config-injection script (added in M1.2 Task 8). Add `pushEnabled` next to it:

```html
<script>
  window.RemindersModule = window.RemindersModule || {};
  window.RemindersModule.config = {
    snoozeEnabled: {{ 'true' if config.REMINDERS_SNOOZE_ENABLED else 'false' }},
    pushEnabled:   {{ 'true' if config.PUSH_ENABLED else 'false' }}
  };
</script>
```

- [ ] **Step 5: Banner show/hide + Settings toggle wiring in remindersModule.js**

In `app/static/js/remindersModule.js`, add a new function near `renderCardForMessage` that mounts the banner if eligible. Add this near the top of the IIFE:

```javascript
  async function _maybeShowPushBanner(messageEl) {
    // Eligibility gate: only show if push enabled, browser supports it,
    // and the user has no subscription yet and hasn't recently dismissed.
    if (!window.RemindersModule?.config?.pushEnabled) return;
    if (!window.RemindersModule?.push?.isSupported) return;
    if (!window.RemindersModule.push.isSupported()) return;
    try {
      if (await window.RemindersModule.push.isSubscribed()) return;
    } catch { return; }

    // Server-side check: has the user dismissed the prompt within 30 days?
    // We piggyback on a quick GET; if the route doesn't exist or 5xx, we
    // err on the side of "do not show" to avoid spam.
    try {
      const resp = await fetch('/push/dismiss-status', { credentials: 'same-origin' });
      if (resp.ok) {
        const { dismissedRecently } = await resp.json();
        if (dismissedRecently) return;
      }
    } catch { /* ignore — show banner */ }

    // Mount the banner after the new card.
    const tmpl = document.createElement('template');
    // Inline the partial markup here so we don't need a server fetch.
    tmpl.innerHTML = `
<div class="push-permission-banner" data-push-banner>
  <div class="push-permission-banner__icon" aria-hidden="true">🔔</div>
  <div class="push-permission-banner__body">
    <p class="push-permission-banner__title">Want a browser ping too?</p>
    <p class="push-permission-banner__sub">Get notified even when this tab isn't open.</p>
  </div>
  <div class="push-permission-banner__actions">
    <button type="button" class="btn btn--primary" data-push-banner-enable>Enable push</button>
    <button type="button" class="btn btn--ghost" data-push-banner-dismiss>Not now</button>
    <button type="button" class="push-permission-banner__close" data-push-banner-close aria-label="Dismiss">✕</button>
  </div>
</div>`.trim();
    const banner = tmpl.content.firstElementChild;

    banner.querySelector('[data-push-banner-enable]').addEventListener('click', async () => {
      const r = await window.RemindersModule.push.subscribe();
      banner.remove();
      if (!r.ok) {
        console.warn('push subscribe failed', r);
      }
    });
    banner.querySelectorAll('[data-push-banner-dismiss], [data-push-banner-close]').forEach(el => {
      el.addEventListener('click', async () => {
        try { await window.RemindersModule.push.dismissPrompt(); } catch {}
        banner.remove();
      });
    });

    messageEl.appendChild(banner);
  }
```

Then in `renderCardForMessage` (which exists after M1.1), CALL `_maybeShowPushBanner(messageEl)` after the card is appended. Search for `messageEl.appendChild(card)` in renderCardForMessage and add right after:

```javascript
    // M1.3 — opportunistically prompt for push permission after a reminder is created
    _maybeShowPushBanner(messageEl);
```

For Settings toggle wiring — find where settings tabs are initialized (probably `settingsModal.js`). Add a handler:

```javascript
  // M1.3 — push notifications toggle in Settings → Notifications.
  const pushToggle = document.querySelector('[data-push-settings-toggle]');
  if (pushToggle && window.RemindersModule?.push) {
    // Initial state
    window.RemindersModule.push.isSubscribed().then(sub => { pushToggle.checked = sub; });

    pushToggle.addEventListener('change', async () => {
      if (pushToggle.checked) {
        const r = await window.RemindersModule.push.subscribe();
        if (!r.ok) pushToggle.checked = false;
      } else {
        if (confirm('Disable push and remove all subscribed devices?')) {
          await window.RemindersModule.push.unsubscribeAll();
        } else {
          pushToggle.checked = true;  // revert
        }
      }
    });
  }
```

Place this inside whatever initialization function fires when the settings modal opens — or, if simpler, run it in a `DOMContentLoaded` handler that no-ops when the toggle isn't present.

- [ ] **Step 6: Add a `GET /push/dismiss-status` route**

The banner JS hits `/push/dismiss-status` to learn whether to re-show. Add the route in `app/blueprints/push_routes.py`:

```python
@push_bp.route("/push/dismiss-status", methods=["GET"])
@error_handler
@login_required
def dismiss_status():
    """Returns whether the user has dismissed the push prompt recently
    (within the last 30 days). Used by the frontend banner gate."""
    from datetime import timedelta
    uid = session["user"]["uid"]
    snap = db.collection("users").document(uid).get()
    if not snap.exists:
        return jsonify({"dismissedRecently": False}), 200
    dismissed = (snap.to_dict() or {}).get("pushPromptDismissedAt")
    if not dismissed:
        return jsonify({"dismissedRecently": False}), 200
    try:
        dt = datetime.fromisoformat(dismissed.replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        recently = (datetime.now(timezone.utc) - dt) < timedelta(days=30)
    except Exception:
        recently = False
    return jsonify({"dismissedRecently": recently}), 200
```

Add a test for it in `tests/test_push_routes.py`:

```python
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
```

- [ ] **Step 7: Run all tests**

```bash
cd /root/xelas.io/xelasphere.io- && /opt/xelasphere/venv/bin/python -m pytest tests/test_push_routes.py tests/test_push_dispatcher.py tests/test_reminders_worker.py -v 2>&1 | tail -15
```

Expected: all push tests + worker tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/templates/_push_permission_banner.html \
        app/templates/_settings_notifications_pane.html \
        app/templates/_settings_modal.html \
        app/templates/index.html \
        app/static/js/remindersModule.js \
        app/blueprints/push_routes.py \
        tests/test_push_routes.py
git commit -m "feat(reminders/m1.3): permission banner + Settings notifications toggle + dismiss-status route

Banner mounts post-first-reminder when eligible (supported browser,
not subscribed, not dismissed in last 30 days). Settings toggle wires
subscribe/unsubscribe-all. GET /push/dismiss-status feeds the banner
gate. 3 new tests."
```

---

## Task 10: Notification icon asset

**File:** Create `app/static/img/icon-192.png`

- [ ] **Step 1: Check if an icon already exists**

```bash
ls /root/xelas.io/xelasphere.io-/app/static/img/icon-192.png 2>&1
ls /root/xelas.io/xelasphere.io-/app/static/images/ 2>&1 | head -5
```

- [ ] **Step 2: Provide an icon**

If `icon-192.png` doesn't exist:

Option A — copy an existing brand icon:
```bash
# Find an existing 192+ pixel PNG to repurpose
find /root/xelas.io/xelasphere.io-/app/static -name "*.png" -size +1k 2>/dev/null | head -5
# Pick one and copy:
cp /root/xelas.io/xelasphere.io-/app/static/images/<existing>.png \
   /root/xelas.io/xelasphere.io-/app/static/img/icon-192.png
mkdir -p /root/xelas.io/xelasphere.io-/app/static/img
```

Option B — generate a simple placeholder PNG with ImageMagick:
```bash
mkdir -p /root/xelas.io/xelasphere.io-/app/static/img
convert -size 192x192 xc:'#10A37F' \
        -fill white -gravity center -pointsize 100 -annotate +0+0 '🔔' \
        /root/xelas.io/xelasphere.io-/app/static/img/icon-192.png
```

If neither tool is available, skip the icon — browsers fall back to the favicon. Note in commit message.

- [ ] **Step 3: Verify served**

```bash
curl -sI https://xelasphere.com/static/img/icon-192.png | grep -iE "content-type|content-length" | head -3
```

Expected: `Content-Type: image/png`, non-zero content-length.

- [ ] **Step 4: Commit**

```bash
git add app/static/img/icon-192.png
git commit -m "feat(reminders/m1.3): notification icon 192x192 PNG"
```

If you skipped the asset, skip this commit and note in the PR body that browsers will fall back to favicon.

---

## Task 11: End-to-end manual verification

**No files — verification step.**

- [ ] **Step 1: Sync files to /opt/xelasphere**

```bash
sudo cp /root/xelas.io/xelasphere.io-/app/services/push_dispatcher.py /opt/xelasphere/app/services/
sudo cp /root/xelas.io/xelasphere.io-/app/blueprints/push_routes.py /opt/xelasphere/app/blueprints/
sudo cp /root/xelas.io/xelasphere.io-/app/__init__.py /opt/xelasphere/app/
sudo cp /root/xelas.io/xelasphere.io-/app/config.py /opt/xelasphere/app/
sudo cp /root/xelas.io/xelasphere.io-/app/static/js/sw.js /opt/xelasphere/app/static/js/
sudo cp /root/xelas.io/xelasphere.io-/app/static/js/pushSetup.js /opt/xelasphere/app/static/js/
sudo cp /root/xelas.io/xelasphere.io-/app/static/js/remindersModule.js /opt/xelasphere/app/static/js/
sudo cp /root/xelas.io/xelasphere.io-/app/templates/index.html /opt/xelasphere/app/templates/
sudo cp /root/xelas.io/xelasphere.io-/app/templates/_settings_modal.html /opt/xelasphere/app/templates/
sudo cp /root/xelas.io/xelasphere.io-/app/templates/_push_permission_banner.html /opt/xelasphere/app/templates/
sudo cp /root/xelas.io/xelasphere.io-/app/templates/_settings_notifications_pane.html /opt/xelasphere/app/templates/
sudo cp /root/xelas.io/xelasphere.io-/tools/reminders_worker.py /opt/xelasphere/tools/
mkdir -p /opt/xelasphere/app/static/img
sudo cp /root/xelas.io/xelasphere.io-/app/static/img/icon-192.png /opt/xelasphere/app/static/img/ 2>/dev/null || true
```

- [ ] **Step 2: Install deps in prod venv**

```bash
sudo /opt/xelasphere/venv/bin/pip install pywebpush py-vapid 2>&1 | tail -3
```

- [ ] **Step 3: Generate VAPID keypair (one-time, per `docs/ops/push-vapid-setup.md`)**

```bash
/opt/xelasphere/venv/bin/python -c "
from py_vapid import Vapid01
from py_vapid.utils import b64urlencode
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
v = Vapid01()
v.generate_keys()
priv_pem = v.private_pem().decode()
pub_bytes = v.public_key.public_bytes(
    encoding=Encoding.X962,
    format=PublicFormat.UncompressedPoint,
)
print('--- PRIVATE KEY (PEM, copy entirely) ---')
print(priv_pem)
print('--- PUBLIC KEY (base64url) ---')
print(b64urlencode(pub_bytes).decode())
"
```

Copy both into `/etc/xelasphere/xelasphere.env`:

```
VAPID_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
...
-----END PRIVATE KEY-----"
VAPID_PUBLIC_KEY=BNc...
VAPID_CLAIMS_SUB=mailto:noreply@xelasphere.com
PUSH_ENABLED=true
```

- [ ] **Step 4: Restart Gunicorn** (new env vars + needs full restart)

```bash
sudo systemctl restart xelasphere
sleep 3
```

- [ ] **Step 5: Verify routes are reachable**

```bash
curl -s "https://xelasphere.com/push/vapid-public-key" | head -3
```

Expected: `{"key": "BNc..."}` matching the VAPID public key from Step 3.

- [ ] **Step 6: In your browser — subscribe**

Open https://xelasphere.com in Chrome desktop. Hard-refresh. Open DevTools → Application → Service Workers; should be empty initially.

Create a new reminder via chat ("remind me to test push in 2 min"). The inline banner should appear below the card. Click "Enable push". Browser prompts for permission; accept.

In DevTools → Application → Service Workers: `sw.js` should now appear as "activated". In Application → Storage → IndexedDB: there should be a push subscription registered.

Confirm via Firestore that `users/<your-uid>/pushSubscriptions/<auto-id>` exists with `endpoint`, `keys.p256dh`, `keys.auth`.

- [ ] **Step 7: Wait for the reminder to fire**

After ~2 min, the worker fires it. You should see:
- Email arrives in your inbox (M0 path, unchanged)
- Native notification appears on your desktop with title, body, "10m" and "1h" buttons
- Notification icon shows the 192x192 PNG (or fallback)

- [ ] **Step 8: Click notification body — opens chat**

Click the body area of the notification. A new tab opens at `/c/<sourceChatId>`, focused on your chat with the reminder visible.

- [ ] **Step 9: Create another reminder and test snooze action**

Create another reminder for 2 min. When it fires, click "10m" action button. The notification dismisses. Verify worker log shows `reminder_snooze_skip` followed by delivery 10 min later:

```bash
sudo journalctl -u 'xelasphere-reminders*' --since "12 minutes ago" --no-pager | grep -E "snooze|push_dispatched|reminder_sent"
```

Expected output sequence:
- `push_dispatched {successes:1, ...}` at first fire
- `reminder_snoozed {source:"email"}` — wait no, source should be from sw.js fetch which is token-based GET, so logs as `reminder_snoozed {source:?, duration:"10m", ...}`. The route doesn't distinguish — fine.
- `reminder_snooze_skip` events for 10 min
- `reminder_sent` at the new delivery time

- [ ] **Step 10: Verify Settings toggle**

Open Settings → Notifications. Toggle should show ON. Toggle OFF. Confirm dialog. All your pushSubscriptions docs are deleted. Confirm in Firestore.

Toggle ON again. Permission flow re-fires (browser remembers granted state — no new prompt). New subscription created.

- [ ] **Step 11: Clean up test reminders**

Delete the test reminders via the in-app modal so they don't fire indefinitely.

- [ ] **Step 12: No commit — verification only.**

---

## Task 12: CHANGELOG + memory update

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `/root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md`

- [ ] **Step 1: Append M1.3 to CHANGELOG**

Insert at top of `## Unreleased` (BEFORE the M1.2 section):

```markdown
### Added — Reminders M1.3 (web push)

Browser push notifications for reminders (behind `PUSH_ENABLED=true`).

- After the worker sends the email, it also fans out an encrypted push notification to every browser the user has subscribed.
- Two snooze action buttons ("10m", "1h") in each notification; tapping fires the M1.2 `/reminders/<id>/snooze` GET route with the same token system email links use.
- Browser support: Chrome / Firefox / Edge / Safari (macOS 13+) on desktop; Chrome / Firefox on Android. iOS Safari deferred.
- Per-device subscription model at `users/{uid}/pushSubscriptions/{subId}`. 410 Gone deletes the sub automatically. 413 Payload Too Large retries once with a shorter body.
- Inline permission banner shown once after the user's first reminder. Dismissible; re-shows after 30 days if still no subscription.
- Settings → Notifications has a single ON/OFF toggle. OFF deletes all subscribed devices.
- Service worker at `/sw.js` handles `push` (renders native notification) and `notificationclick` (snooze action → backend, body → chat deeplink).
- VAPID keypair generated once per server install per `docs/ops/push-vapid-setup.md`. Manual rotation procedure documented.
- Default `PUSH_ENABLED=false` hides the banner, returns 503 from all push routes, and makes the worker fan-out a no-op. Email still delivers.

M1 complete (recurrence + snooze + push). Future work tracked in M5+.
```

- [ ] **Step 2: Update project memory**

In `/root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md`, add ABOVE the existing M1.2 section:

```markdown
**M1.3 shipped (2026-05-DD):** M1 complete.
- Web push live on xelasphere.com behind `PUSH_ENABLED=true` + VAPID env vars set
- Per-device subscriptions, 410 cleanup, 413 truncate-retry
- Notification action snooze (10m/1h) reuses M1.2 backend route via signed token in payload
- iOS Safari deferred (PWA install required — M5+ scope)
- ~16 new tests across `test_push_dispatcher.py` + `test_push_routes.py` + worker integration
- One-time ops step: VAPID keypair generated per `docs/ops/push-vapid-setup.md`
- Branch `feat/reminders-m1.3-push` (pending PR). Plan: `docs/superpowers/plans/2026-05-19-reminders-m1.3-push.md`
```

Also update the index `MEMORY.md` if appropriate (e.g., add a "Reminders M1 complete" line).

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md \
        /root/.claude/projects/-root-xelas-io-xelasphere-io-/memory/project_reminders_m0_shipped.md
git commit -m "docs(reminders/m1.3): CHANGELOG + memory entry for M1 closeout"
```

---

## Self-Review Checklist

**1. Spec coverage**

| Spec section | Plan task |
|---|---|
| `pywebpush` + `py-vapid` deps | Task 1 |
| `PUSH_ENABLED` + VAPID env flags | Task 1 |
| One-time VAPID keypair generation procedure | Task 2 (docs) |
| `push_dispatcher.build_payload` + `_truncate` | Task 3 |
| `push_dispatcher.send_push_for_reminder` (410, 413, no-op cases) | Task 4 |
| Routes: subscribe (idempotent), unsubscribe-all, vapid-public-key, dismiss-prompt | Task 5 |
| Dismiss-status GET for banner gate | Task 9 step 6 |
| Worker integration | Task 6 |
| Service worker (`push` + `notificationclick`) | Task 7 |
| Frontend lifecycle (`pushSetup.js`) | Task 8 |
| Inline banner + Settings toggle | Task 9 |
| Notification icon | Task 10 |
| E2E live verification | Task 11 |
| CHANGELOG + memory | Task 12 |

No gaps.

**2. Placeholder scan**

```bash
grep -nE "TBD|TODO|fill in|implement later" /root/xelas.io/xelasphere.io-/docs/superpowers/plans/2026-05-19-reminders-m1.3-push.md | grep -v self-review
```

Expected: zero matches (excluding this section's grep example).

**3. Type consistency**

- `build_payload(reminder, *, snooze_token, body_limit=PAYLOAD_BODY_LIMIT) -> str` — used by Task 3 (definition) and Task 4 (caller). Consistent.
- `send_push_for_reminder(reminder, user_id) -> None` — defined Task 4, called by Task 6.
- `make_snooze_token(rid, uid) -> str` — defined in M1.2 (`signed_links.py`), used here in Task 4.
- Firestore field names: `endpoint`, `keys.{p256dh,auth}`, `userAgent`, `createdAt`, `lastSeenAt`, `lastError`, `pushPromptDismissedAt` — consistent across routes (Task 5) + dispatcher (Task 4) + frontend (Task 8).
- Notification action ids: `snooze_10m`, `snooze_1h` — consistent in `build_payload` (Task 3) + `sw.js` (Task 7).
- Frontend namespace: `window.RemindersModule.push.{isSupported, isSubscribed, subscribe, unsubscribeAll, dismissPrompt}` — defined Task 8, used by Task 9.

**4. Ambiguity**

- "Sync to /opt/xelasphere" — explicit file list in Task 11 Step 1.
- VAPID generation: explicit Python snippet in Task 11 Step 3 + docs in Task 2.
- "Mirror the existing fixture/pattern" appears in Task 5 Step 5 — qualified with "exactly verbatim, don't invent a new pattern". Acceptable given the existing test file is the canonical reference.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-reminders-m1.3-push.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, spec-then-quality review between tasks. Same pattern as M1.1 + M1.2.
2. **Inline Execution** — execute tasks in this session using executing-plans with checkpoints.

Which approach?

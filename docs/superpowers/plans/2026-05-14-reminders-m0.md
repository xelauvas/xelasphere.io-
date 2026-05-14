# Reminders M0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the smallest end-to-end reminders loop in XelaSphere — chat utterance → systemd-timer poller → SendGrid email + inline card + Schedules tab — email-only, English-only, no recurrence, login-required.

**Architecture:** Hybrid intent detection (regex pre-filter + cheap LLM JSON extraction). Reminders persist to Firestore at `users/{uid}/reminders/{id}`. A separate CLI poller, run by a systemd timer every 60s, queries due reminders, takes a Firestore-transaction lock, sends via SendGrid, and flips status. Frontend uses the existing artifact-sentinel-tag idiom — server emits `<reminder id="..." />` in the assistant stream, frontend swaps it for an inline card. Edit modal mirrors ChatGPT's shape.

**Tech Stack:** Python 3 + Flask + Gunicorn (existing), Firestore (existing), Redis (existing, unused by this feature), SendGrid (new), systemd timer/service units (new), vanilla JS + CSS modules (existing pattern), pytest with Firestore mocks.

**Reference Spec:** `docs/superpowers/specs/2026-05-14-reminders-m0-design.md` (commit `b6dfc30`)

---

## File Structure

**New files:**
- `app/blueprints/reminders_routes.py` — REST CRUD blueprint
- `app/services/reminder_intent.py` — `is_reminder_request()` + `extract_reminder()`
- `app/services/reminder_model.py` — pure data helpers (factory, validation, status enum)
- `app/services/email_sender.py` — thin SendGrid wrapper
- `tools/reminders_worker.py` — CLI poller, idempotent
- `deploy/xelasphere-reminders.service` — systemd unit
- `deploy/xelasphere-reminders.timer` — systemd timer (every minute)
- `app/static/js/remindersModule.js` — card render + modal + Schedules tab
- `app/static/css/reminders.css` — card + modal styles
- `app/templates/_reminders_modal.html` — edit modal markup partial
- `tests/test_reminder_intent.py`
- `tests/test_reminder_model.py`
- `tests/test_reminders_routes.py`
- `tests/test_email_sender.py`
- `tests/test_reminders_worker.py`

**Modified files:**
- `app/__init__.py` — register `reminders_bp`
- `app/blueprints/chat_routes.py` — wire intent detection into both stream paths (around lines 2114 and 4019, where `is_email_request` is already called)
- `app/templates/_settings_modal.html` — replace "Schedules" `<p class="settings-pane-empty">Coming Soon.</p>` with full pane
- `app/templates/index.html` — include `reminders.css` + `remindersModule.js`
- `firestore.rules` — add `users/{uid}/reminders/{rid}` ownership rule
- `requirements-cpu.txt` + `requirements-gpu.txt` — add `sendgrid`
- `app/config.py` — add `REMINDERS_ENABLED`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, `SENDGRID_FROM_NAME` config

**Reasoning on decomposition:**
- `reminder_model.py` is split from `reminders_routes.py` so the worker can build/validate reminders without importing Flask.
- `email_sender.py` is its own module so future transactional mail (verification, password reset) can reuse it without depending on the reminder domain.
- `reminder_intent.py` mirrors the placement of `is_email_request` (currently in `email_routes.py`) but isolates it from CRUD because the chat path imports it and we don't want chat routes pulling in HTTP-only code.

---

## Milestone 0 — Scaffolding (pre-Stage-1 foundations)

Foundation work that's not part of the rollout but unblocks every stage.

### Task 0.1: Add sendgrid dependency

**Files:**
- Modify: `requirements-cpu.txt`
- Modify: `requirements-gpu.txt`

- [ ] **Step 1: Find current dep file format**

Run: `head -5 requirements-cpu.txt`
Expected: pinned versions like `flask==3.0.0`

- [ ] **Step 2: Add sendgrid line to both files**

In both `requirements-cpu.txt` and `requirements-gpu.txt`, append:

```
sendgrid==6.11.0
```

- [ ] **Step 3: Install in dev venv**

Run: `./venv/bin/pip install sendgrid==6.11.0`
Expected: "Successfully installed sendgrid-6.11.0 ..."

- [ ] **Step 4: Smoke-import**

Run: `./venv/bin/python -c "from sendgrid import SendGridAPIClient; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add requirements-cpu.txt requirements-gpu.txt
git commit -m "deps: add sendgrid for reminders M0"
```

---

### Task 0.2: Feature flag + config keys

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Locate where other env-backed config keys are defined**

Run: `grep -n "os.environ.get\|os.getenv" app/config.py | head -20`
Note the pattern used (likely class attributes or module-level constants).

- [ ] **Step 2: Add reminders config keys**

Append (or insert next to other transactional config) in `app/config.py`:

```python
# Reminders M0
REMINDERS_ENABLED = os.environ.get("REMINDERS_ENABLED", "false").lower() == "true"
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "noreply@xelasphere.io")
SENDGRID_FROM_NAME = os.environ.get("SENDGRID_FROM_NAME", "XelaSphere Reminders")
```

If `config.py` uses a `Config` class, add them as class attributes inside it instead. Match the file's existing convention exactly.

- [ ] **Step 3: Verify load**

Run: `./venv/bin/python -c "from app.config import Config; print(Config.REMINDERS_ENABLED, Config.SENDGRID_FROM_EMAIL)"`
Expected: `False noreply@xelasphere.io`

- [ ] **Step 4: Commit**

```bash
git add app/config.py
git commit -m "config: add REMINDERS_ENABLED + sendgrid keys"
```

---

### Task 0.3: Reminder model module (pure data, no Flask)

**Files:**
- Create: `app/services/reminder_model.py`
- Create: `tests/test_reminder_model.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reminder_model.py`:

```python
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
            fire_at_utc=datetime(2026, 5, 15, 8, 0, tzinfo=timezone.utc),
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
        ok, err = validate_reminder_payload({"fireAtLocal": "2026-05-15T09:00", "timezone": "Europe/London"})
        assert not ok
        assert "name" in err.lower()

    def test_validate_payload_requires_fire_at_local(self):
        ok, err = validate_reminder_payload({"name": "x", "timezone": "Europe/London"})
        assert not ok

    def test_validate_payload_requires_timezone(self):
        ok, err = validate_reminder_payload({"name": "x", "fireAtLocal": "2026-05-15T09:00"})
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

    def test_compute_fire_at_utc_handles_dst(self):
        # 2026-03-29 02:30 London is in the DST gap — accept either side
        fire_at = compute_fire_at_utc("2026-05-15T09:00:00", "Europe/London")
        # London is UTC+1 in May (BST)
        assert fire_at.hour == 8
        assert fire_at.tzinfo == timezone.utc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/pytest tests/test_reminder_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.reminder_model'`

- [ ] **Step 3: Implement reminder_model.py**

Create `app/services/reminder_model.py`:

```python
"""Pure data helpers for reminders. No Flask, no Firestore imports.

Used by reminders_routes (request validation) and reminders_worker
(constructing/validating dicts written back to Firestore).
"""
from datetime import datetime, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

STATUS_SCHEDULED = "scheduled"
STATUS_SENDING = "sending"
STATUS_PAUSED = "paused"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES = frozenset({
    STATUS_SCHEDULED, STATUS_SENDING, STATUS_PAUSED,
    STATUS_DELIVERED, STATUS_FAILED, STATUS_CANCELLED,
})


def compute_fire_at_utc(fire_at_local_iso: str, timezone_str: str) -> datetime:
    """Convert a naive local ISO datetime + IANA tz into a UTC datetime."""
    naive = datetime.fromisoformat(fire_at_local_iso)
    if naive.tzinfo is not None:
        # Already tz-aware; just convert
        return naive.astimezone(timezone.utc)
    local = naive.replace(tzinfo=ZoneInfo(timezone_str))
    return local.astimezone(timezone.utc)


def validate_reminder_payload(payload: dict) -> Tuple[bool, Optional[str]]:
    """Validate a create-reminder request payload. Returns (ok, error_or_None)."""
    name = (payload.get("name") or "").strip()
    if not name:
        return False, "name is required"
    fire_at_local = payload.get("fireAtLocal")
    if not fire_at_local:
        return False, "fireAtLocal is required"
    timezone_str = payload.get("timezone")
    if not timezone_str:
        return False, "timezone is required"
    try:
        fire_at_utc = compute_fire_at_utc(fire_at_local, timezone_str)
    except (ValueError, KeyError) as exc:
        return False, f"invalid fireAtLocal or timezone: {exc}"
    if fire_at_utc <= datetime.now(timezone.utc):
        return False, "fireAtLocal must be in the future"
    return True, None


def new_reminder_dict(
    *,
    user_id: str,
    name: str,
    instructions: str,
    fire_at_utc: datetime,
    timezone_str: str,
    source_chat_id: Optional[str] = None,
    source_message_id: Optional[str] = None,
    created_by: str = "user",
) -> dict:
    """Build the canonical reminder dict for Firestore write.

    `id` is left for the caller to set (so it can match the doc ID).
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "userId": user_id,
        "name": name,
        "instructions": instructions or "",
        "fireAt": fire_at_utc.isoformat(),
        "timezone": timezone_str,
        "createdAt": now,
        "updatedAt": now,
        "status": STATUS_SCHEDULED,
        "deliveredAt": None,
        "attemptCount": 0,
        "lastError": None,
        "lockedAt": None,
        "sourceChatId": source_chat_id,
        "sourceMessageId": source_message_id,
        "createdBy": created_by,
        "channels": ["email"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/test_reminder_model.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/reminder_model.py tests/test_reminder_model.py
git commit -m "feat(reminders): pure-data reminder model + validation"
```

---

## Milestone 1 — Backend-only, admin via curl (Stage 1)

The full server-side loop: blueprint CRUD, email sender, worker, systemd units. By the end, you can `curl` a reminder into existence as admin and an email shows up at the scheduled time.

### Task 1.1: Reminders blueprint scaffold

**Files:**
- Create: `app/blueprints/reminders_routes.py`
- Modify: `app/__init__.py`
- Create: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing scaffold test**

Create `tests/test_reminders_routes.py`:

```python
import pytest
from app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


class TestRemindersBlueprintRegistered:
    def test_blueprint_is_registered(self, app):
        assert "reminders" in app.blueprints

    def test_list_endpoint_exists_and_requires_login(self, client):
        # No session → 401 or 302 (whatever login_required returns)
        resp = client.get("/reminders")
        assert resp.status_code in (302, 401)
```

- [ ] **Step 2: Run test, expect blueprint-missing failure**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestRemindersBlueprintRegistered -v`
Expected: FAIL — `KeyError: 'reminders'`

- [ ] **Step 3: Create the blueprint module**

Create `app/blueprints/reminders_routes.py`:

```python
"""Reminders CRUD blueprint. M0: email-only, login-required."""
import logging
from flask import Blueprint, jsonify

from app.blueprints.shared import login_required, error_handler

logger = logging.getLogger(__name__)

reminders_bp = Blueprint("reminders", __name__)


@reminders_bp.route("/reminders", methods=["GET"])
@error_handler
@login_required
def list_reminders():
    """Stub — returns empty list. Real impl in Task 1.4."""
    return jsonify({"reminders": []})
```

- [ ] **Step 4: Register blueprint**

In `app/__init__.py`, find the import block near line 189:

```python
from app.blueprints.routes import (
    weather_bp, email_bp, account_bp, memory_bp, session_bp,
    pages_bp, artifacts_bp, media_bp, upload_bp, chat_bp
)
```

Add `reminders_bp` to the import (adjust import path if blueprints are imported individually — match existing style). Then after `app.register_blueprint(chat_bp)`, add:

```python
from app.blueprints.reminders_routes import reminders_bp
app.register_blueprint(reminders_bp)
```

- [ ] **Step 5: Run tests, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestRemindersBlueprintRegistered -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/reminders_routes.py app/__init__.py tests/test_reminders_routes.py
git commit -m "feat(reminders): blueprint scaffold + GET /reminders stub"
```

---

### Task 1.2: POST /reminders (create)

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reminders_routes.py`:

```python
from unittest.mock import patch, MagicMock


class TestCreateReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_create_persists_and_returns_id(self, mock_db, client, app):
        # Logged-in session
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_doc_ref = MagicMock()
        fake_doc_ref.id = "rem_abc"
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = fake_doc_ref

        resp = client.post(
            "/reminders",
            json={
                "name": "Call mom",
                "instructions": "from kitchen",
                "fireAtLocal": "2099-01-01T09:00:00",
                "timezone": "Europe/London",
            },
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["id"] == "rem_abc"
        assert fake_doc_ref.set.called

    def test_create_rejects_missing_name(self, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}
        resp = client.post("/reminders", json={"fireAtLocal": "2099-01-01T09:00", "timezone": "Europe/London"})
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    def test_create_rejects_past_fire_time(self, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}
        resp = client.post(
            "/reminders",
            json={"name": "x", "fireAtLocal": "2020-01-01T09:00", "timezone": "Europe/London"},
        )
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestCreateReminder -v`
Expected: FAIL — endpoint returns 405 (no POST handler)

- [ ] **Step 3: Implement POST handler**

In `app/blueprints/reminders_routes.py`, add at top:

```python
from flask import request, session
from app.config import db, Config
from app.limiter import limiter, csrf_required
from app.services.reminder_model import (
    new_reminder_dict, validate_reminder_payload, compute_fire_at_utc,
)
```

Then append:

```python
@reminders_bp.route("/reminders", methods=["POST"])
@error_handler
@login_required
@csrf_required
@limiter.limit("60 per hour")
def create_reminder():
    if not Config.REMINDERS_ENABLED:
        return jsonify({"error": "Reminders are not enabled."}), 503

    payload = request.get_json(silent=True) or {}
    ok, err = validate_reminder_payload(payload)
    if not ok:
        return jsonify({"error": err}), 400

    user_id = session["user"]["uid"]
    fire_at_utc = compute_fire_at_utc(payload["fireAtLocal"], payload["timezone"])
    reminder = new_reminder_dict(
        user_id=user_id,
        name=payload["name"].strip(),
        instructions=(payload.get("instructions") or "").strip(),
        fire_at_utc=fire_at_utc,
        timezone_str=payload["timezone"],
        source_chat_id=payload.get("sourceChatId"),
        source_message_id=payload.get("sourceMessageId"),
        created_by=payload.get("createdBy", "user"),
    )

    doc_ref = db.collection("users").document(user_id).collection("reminders").document()
    reminder["id"] = doc_ref.id
    doc_ref.set(reminder)

    # Also persist defaultTimezone on user doc if not already set
    user_doc = db.collection("users").document(user_id)
    user_doc.set({"defaultTimezone": payload["timezone"]}, merge=True)

    logger.info(f"[reminders] created {doc_ref.id} for {user_id} fire_at={reminder['fireAt']}")
    return jsonify({"id": doc_ref.id, "reminder": reminder}), 201
```

Toggle `REMINDERS_ENABLED=true` in your test env so the 503 doesn't fire:

In `conftest.py` at the top with the other `os.environ` lines, add:
```python
os.environ['REMINDERS_ENABLED'] = 'true'
```

- [ ] **Step 4: Run tests, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestCreateReminder -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py conftest.py
git commit -m "feat(reminders): POST /reminders persists to Firestore"
```

---

### Task 1.3: GET /reminders/:id (single)

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_reminders_routes.py`:

```python
class TestGetReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_get_returns_existing_reminder(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_snapshot = MagicMock()
        fake_snapshot.exists = True
        fake_snapshot.to_dict.return_value = {"id": "rem_x", "name": "test"}
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = fake_snapshot

        resp = client.get("/reminders/rem_x")
        assert resp.status_code == 200
        assert resp.get_json()["reminder"]["name"] == "test"

    @patch("app.blueprints.reminders_routes.db")
    def test_get_404_when_missing(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_snapshot = MagicMock()
        fake_snapshot.exists = False
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = fake_snapshot

        resp = client.get("/reminders/rem_missing")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestGetReminder -v`
Expected: FAIL — 404 returned by Flask itself

- [ ] **Step 3: Add handler**

Append to `app/blueprints/reminders_routes.py`:

```python
@reminders_bp.route("/reminders/<reminder_id>", methods=["GET"])
@error_handler
@login_required
def get_reminder(reminder_id: str):
    user_id = session["user"]["uid"]
    doc = (
        db.collection("users").document(user_id)
        .collection("reminders").document(reminder_id).get()
    )
    if not doc.exists:
        return jsonify({"error": "Reminder not found."}), 404
    return jsonify({"reminder": doc.to_dict()})
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestGetReminder -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders): GET /reminders/:id"
```

---

### Task 1.4: GET /reminders (list with status filter)

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_reminders_routes.py`:

```python
class TestListReminders:
    @patch("app.blueprints.reminders_routes.db")
    def test_list_returns_user_reminders(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_docs = []
        for i in range(3):
            d = MagicMock()
            d.to_dict.return_value = {"id": f"rem_{i}", "name": f"r{i}", "status": "scheduled"}
            d.id = f"rem_{i}"
            fake_docs.append(d)

        query = mock_db.collection.return_value.document.return_value.collection.return_value
        query.order_by.return_value.limit.return_value.stream.return_value = fake_docs

        resp = client.get("/reminders")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["reminders"]) == 3
        assert body["reminders"][0]["id"] == "rem_0"
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestListReminders -v`
Expected: FAIL — stub returns `[]`

- [ ] **Step 3: Replace the stub with real implementation**

Replace the existing `list_reminders` in `app/blueprints/reminders_routes.py`:

```python
@reminders_bp.route("/reminders", methods=["GET"])
@error_handler
@login_required
def list_reminders():
    from google.cloud import firestore as gc_firestore
    user_id = session["user"]["uid"]
    status_filter = request.args.get("status")  # optional: scheduled|paused|delivered|failed|cancelled

    coll = db.collection("users").document(user_id).collection("reminders")
    query = coll
    if status_filter:
        query = query.where("status", "==", status_filter)
    docs = query.order_by("fireAt", direction=gc_firestore.Query.DESCENDING).limit(200).stream()

    items = []
    for d in docs:
        item = d.to_dict() or {}
        item["id"] = d.id
        items.append(item)
    return jsonify({"reminders": items})
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestListReminders -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders): GET /reminders list with optional status filter"
```

---

### Task 1.5: PATCH /reminders/:id (edit / pause / resume)

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reminders_routes.py`:

```python
class TestPatchReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_patch_updates_name(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {"id": "rem_x", "status": "scheduled", "name": "old", "timezone": "Europe/London"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = fake_snap

        resp = client.patch("/reminders/rem_x", json={"name": "new"})
        assert resp.status_code == 200
        # Verify update() was called with name
        args, _ = doc_ref.update.call_args
        assert args[0]["name"] == "new"

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_can_pause(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {"id": "rem_x", "status": "scheduled"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = fake_snap

        resp = client.patch("/reminders/rem_x", json={"status": "paused"})
        assert resp.status_code == 200
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "paused"

    @patch("app.blueprints.reminders_routes.db")
    def test_patch_rejects_invalid_status(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {"id": "rem_x", "status": "scheduled"}
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = fake_snap

        resp = client.patch("/reminders/rem_x", json={"status": "frobnicated"})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestPatchReminder -v`
Expected: FAIL — no PATCH handler

- [ ] **Step 3: Implement PATCH**

Append to `app/blueprints/reminders_routes.py`:

```python
from datetime import datetime, timezone
from app.services.reminder_model import ALL_STATUSES, STATUS_SCHEDULED, STATUS_FAILED


_EDITABLE_FIELDS = {"name", "instructions", "fireAtLocal", "timezone", "status"}
_PATCH_ALLOWED_STATUSES = {"scheduled", "paused", "cancelled"}
# 'delivered', 'sending', 'failed' are server-set only


@reminders_bp.route("/reminders/<reminder_id>", methods=["PATCH"])
@error_handler
@login_required
@csrf_required
def patch_reminder(reminder_id: str):
    user_id = session["user"]["uid"]
    payload = request.get_json(silent=True) or {}

    unknown = set(payload.keys()) - _EDITABLE_FIELDS - {"_retry"}
    if unknown:
        return jsonify({"error": f"unknown fields: {sorted(unknown)}"}), 400

    doc_ref = db.collection("users").document(user_id).collection("reminders").document(reminder_id)
    snap = doc_ref.get()
    if not snap.exists:
        return jsonify({"error": "Reminder not found."}), 404
    current = snap.to_dict() or {}

    update = {}
    if "name" in payload:
        name = (payload["name"] or "").strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
        update["name"] = name
    if "instructions" in payload:
        update["instructions"] = (payload["instructions"] or "").strip()
    if "fireAtLocal" in payload or "timezone" in payload:
        new_local = payload.get("fireAtLocal") or current.get("fireAt")
        new_tz = payload.get("timezone") or current.get("timezone")
        try:
            fire_at_utc = compute_fire_at_utc(new_local, new_tz)
        except Exception as exc:
            return jsonify({"error": f"invalid fireAtLocal/timezone: {exc}"}), 400
        if fire_at_utc <= datetime.now(timezone.utc):
            return jsonify({"error": "fireAtLocal must be in the future"}), 400
        update["fireAt"] = fire_at_utc.isoformat()
        update["timezone"] = new_tz
    if "status" in payload:
        if payload["status"] not in _PATCH_ALLOWED_STATUSES:
            return jsonify({"error": f"status must be one of {sorted(_PATCH_ALLOWED_STATUSES)}"}), 400
        update["status"] = payload["status"]

    # _retry: special path to re-arm a failed reminder
    if payload.get("_retry") and current.get("status") == STATUS_FAILED:
        update["status"] = STATUS_SCHEDULED
        update["attemptCount"] = 0
        update["lastError"] = None
        update["lockedAt"] = None

    if not update:
        return jsonify({"error": "no fields to update"}), 400

    update["updatedAt"] = datetime.now(timezone.utc).isoformat()
    doc_ref.update(update)
    logger.info(f"[reminders] patched {reminder_id} for {user_id}: {sorted(update.keys())}")
    return jsonify({"reminder": {**current, **update, "id": reminder_id}})
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestPatchReminder -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders): PATCH /reminders/:id (edit/pause/cancel/retry)"
```

---

### Task 1.6: DELETE /reminders/:id

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `tests/test_reminders_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_reminders_routes.py`:

```python
class TestDeleteReminder:
    @patch("app.blueprints.reminders_routes.db")
    def test_delete_removes_doc(self, mock_db, client):
        with client.session_transaction() as sess:
            sess["user"] = {"uid": "u1", "email": "a@b.com"}

        fake_snap = MagicMock()
        fake_snap.exists = True
        fake_snap.to_dict.return_value = {"id": "rem_x"}
        doc_ref = mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value
        doc_ref.get.return_value = fake_snap

        resp = client.delete("/reminders/rem_x")
        assert resp.status_code == 200
        assert doc_ref.delete.called
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestDeleteReminder -v`
Expected: FAIL — 405 method not allowed

- [ ] **Step 3: Implement DELETE**

Append to `app/blueprints/reminders_routes.py`:

```python
@reminders_bp.route("/reminders/<reminder_id>", methods=["DELETE"])
@error_handler
@login_required
@csrf_required
def delete_reminder(reminder_id: str):
    user_id = session["user"]["uid"]
    doc_ref = db.collection("users").document(user_id).collection("reminders").document(reminder_id)
    snap = doc_ref.get()
    if not snap.exists:
        return jsonify({"error": "Reminder not found."}), 404
    doc_ref.delete()
    logger.info(f"[reminders] deleted {reminder_id} for {user_id}")
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_routes.py::TestDeleteReminder -v`
Expected: 1 passed

- [ ] **Step 5: Run the full reminders_routes suite**

Run: `./venv/bin/pytest tests/test_reminders_routes.py -v`
Expected: all tests pass (10+ tests)

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/reminders_routes.py tests/test_reminders_routes.py
git commit -m "feat(reminders): DELETE /reminders/:id"
```

---

### Task 1.7: Firestore security rules + composite index

**Files:**
- Modify: `firestore.rules`

- [ ] **Step 1: Add ownership rule for reminders**

In `firestore.rules`, inside `match /databases/{database}/documents { ... }`, after the existing `match /users/{userId}/chat_sessions/...` block, add:

```
    // Reminders M0 — owner-only read/write. Worker uses admin SDK (bypasses rules).
    match /users/{userId}/reminders/{reminderId} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }
```

- [ ] **Step 2: Deploy rules to Firestore**

Run: `firebase deploy --only firestore:rules`
Expected: "Deploy complete" with rules listed.

(If `firebase` CLI is not configured locally, document the rule change in the commit and have the operator run it during rollout stage 1.)

- [ ] **Step 3: Composite index — declare needed**

Note in the commit message that the worker query requires a Firestore composite index on collection-group `reminders`: fields `(status ASC, fireAt ASC)`. Firestore will refuse the first query with a console link to create it; the operator clicks it during rollout stage 1. If a `firestore.indexes.json` file is later added to the repo, it should include:

```json
{
  "indexes": [
    {
      "collectionGroup": "reminders",
      "queryScope": "COLLECTION_GROUP",
      "fields": [
        { "fieldPath": "status", "order": "ASCENDING" },
        { "fieldPath": "fireAt", "order": "ASCENDING" }
      ]
    }
  ]
}
```

- [ ] **Step 4: Commit**

```bash
git add firestore.rules
git commit -m "firestore: owner-only rules for users/{uid}/reminders/{id}

Worker uses admin SDK; bypasses these rules. First worker query
will require a collection-group composite index on
(status ASC, fireAt ASC) — Firestore console will surface a
direct-create link on the first query failure."
```

---

### Task 1.8: SendGrid email sender (mockable wrapper)

**Files:**
- Create: `app/services/email_sender.py`
- Create: `tests/test_email_sender.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_sender.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from app.services.email_sender import (
    send_transactional_email, EmailSendError, EmailSendResult,
)


class TestSendTransactional:
    @patch("app.services.email_sender.SendGridAPIClient")
    def test_success_returns_result(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock(status_code=202, body=b"", headers={})
        mock_client.send.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            result = send_transactional_email(
                to_email="user@example.com",
                subject="hi",
                body_text="body",
            )
        assert isinstance(result, EmailSendResult)
        assert result.status_code == 202
        assert result.retryable is False

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_5xx_raises_retryable_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock(status_code=503, body=b"upstream down", headers={})
        mock_client.send.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is True
        assert exc.value.status_code == 503

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_4xx_raises_non_retryable_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock(status_code=403, body=b"bad request", headers={})
        mock_client.send.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is False

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_network_error_is_retryable(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.send.side_effect = ConnectionError("dns failed")
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is True

    def test_missing_api_key_raises(self):
        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = ""
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert "api key" in str(exc.value).lower()
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_email_sender.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement email_sender.py**

Create `app/services/email_sender.py`:

```python
"""SendGrid wrapper for transactional email.

Reusable for reminders, verification, password reset, etc. M0 only
hooks the reminder use case.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

from app.config import Config

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when SendGrid returns non-2xx or the request itself fails.

    `retryable` distinguishes transient failures (network, 5xx) from
    permanent failures (4xx — bad email, blocked sender).
    """
    def __init__(self, message: str, *, retryable: bool, status_code: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class EmailSendResult:
    status_code: int
    retryable: bool = False  # always False for success


def send_transactional_email(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> EmailSendResult:
    """Send a transactional email via SendGrid.

    Raises EmailSendError on failure (with retryable flag set).
    """
    if not Config.SENDGRID_API_KEY:
        raise EmailSendError("SendGrid API key not configured.", retryable=False)

    message = Mail(
        from_email=Email(Config.SENDGRID_FROM_EMAIL, Config.SENDGRID_FROM_NAME),
        to_emails=[To(to_email)],
        subject=subject,
    )
    message.add_content(Content("text/plain", body_text))
    if body_html:
        message.add_content(Content("text/html", body_html))

    try:
        client = SendGridAPIClient(Config.SENDGRID_API_KEY)
        response = client.send(message)
    except Exception as exc:
        # Network errors, DNS failures, timeouts — always retryable
        raise EmailSendError(f"SendGrid request failed: {exc}", retryable=True) from exc

    status = getattr(response, "status_code", 0)
    if 200 <= status < 300:
        return EmailSendResult(status_code=status)
    if 500 <= status < 600:
        raise EmailSendError(
            f"SendGrid 5xx: {status} body={getattr(response, 'body', b'')!r}",
            retryable=True,
            status_code=status,
        )
    # 4xx — bad input, won't change on retry
    raise EmailSendError(
        f"SendGrid {status}: {getattr(response, 'body', b'')!r}",
        retryable=False,
        status_code=status,
    )
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_email_sender.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/email_sender.py tests/test_email_sender.py
git commit -m "feat(email): SendGrid transactional wrapper + retryable error type"
```

---

### Task 1.9: Reminders worker — due query

**Files:**
- Create: `tools/reminders_worker.py`
- Create: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_reminders_worker.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


def _make_doc(doc_id, data):
    snap = MagicMock()
    snap.id = doc_id
    snap.to_dict.return_value = data
    snap.reference = MagicMock()
    return snap


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
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.reminders_worker'`

- [ ] **Step 3: Create the worker module**

Ensure `tools/__init__.py` exists (create empty if missing):
```bash
mkdir -p tools && touch tools/__init__.py
```

Create `tools/reminders_worker.py`:

```python
"""Reminders worker — CLI poller for due reminders. Run by systemd timer.

Usage: python -m tools.reminders_worker [--limit N] [--dry-run]
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import List

logger = logging.getLogger("reminders_worker")


_db_cache = None


def get_db():
    """Lazy Firestore client. Lets tests patch this single seam."""
    global _db_cache
    if _db_cache is None:
        from app.config import db as app_db
        _db_cache = app_db
    return _db_cache


def fetch_due_reminders(limit: int = 100):
    """Return up to `limit` reminders that are status=scheduled and fireAt<=now."""
    from google.cloud import firestore as gc_firestore
    db = get_db()
    now = datetime.now(timezone.utc)
    docs = (
        db.collection_group("reminders")
        .where("status", "==", "scheduled")
        .where("fireAt", "<=", now.isoformat())
        .order_by("fireAt", direction=gc_firestore.Query.ASCENDING)
        .limit(limit)
        .stream()
    )
    return list(docs)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from app.config import Config
    if not Config.REMINDERS_ENABLED:
        logger.info("REMINDERS_ENABLED=false; exiting without work.")
        return 0

    docs = fetch_due_reminders(limit=args.limit)
    logger.info(f"fetched {len(docs)} due reminder(s)")
    # send loop is added in Task 1.11
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestFetchDue -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tools/__init__.py tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/worker): CLI scaffold + fetch_due_reminders"
```

---

### Task 1.10: Worker — transaction lock (claim a reminder)

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reminders_worker.py`:

```python
class TestClaimReminder:
    @patch("tools.reminders_worker.get_db")
    def test_claim_wins_when_status_scheduled(self, mock_get_db):
        from tools.reminders_worker import try_claim_reminder
        db = MagicMock()
        mock_get_db.return_value = db

        # Simulate transaction context manager
        txn = MagicMock()
        db.transaction.return_value = txn

        doc_ref = MagicMock()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"status": "scheduled"}

        # The transactional decorator runs the inner fn with the txn
        # We test the inner function directly via try_claim_reminder
        result = try_claim_reminder(doc_ref, _now_iso="2026-05-15T08:00:00+00:00",
                                    _fake_snap=snap)
        assert result is True
        # Verify update was called with status='sending'
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "sending"
        assert args[0]["lockedAt"] == "2026-05-15T08:00:00+00:00"

    def test_claim_loses_when_status_not_scheduled(self):
        from tools.reminders_worker import try_claim_reminder
        doc_ref = MagicMock()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"status": "sending"}  # someone else won

        result = try_claim_reminder(doc_ref, _now_iso="2026-05-15T08:00:00+00:00",
                                    _fake_snap=snap)
        assert result is False
        doc_ref.update.assert_not_called()
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestClaimReminder -v`
Expected: FAIL — `try_claim_reminder` doesn't exist

- [ ] **Step 3: Add claim function**

Append to `tools/reminders_worker.py`:

```python
def try_claim_reminder(doc_ref, *, _now_iso=None, _fake_snap=None) -> bool:
    """Atomically flip status scheduled→sending, set lockedAt.

    Returns True if this worker won the claim, False otherwise.
    `_now_iso` and `_fake_snap` are testing seams.
    """
    if _now_iso is None:
        _now_iso = datetime.now(timezone.utc).isoformat()

    if _fake_snap is not None:
        # Testing path: skip the transaction, just check the snapshot
        if _fake_snap.exists and _fake_snap.to_dict().get("status") == "scheduled":
            doc_ref.update({"status": "sending", "lockedAt": _now_iso})
            return True
        return False

    # Real Firestore transaction
    from google.cloud import firestore as gc_firestore
    db = get_db()
    transaction = db.transaction()

    @gc_firestore.transactional
    def _txn(txn, ref):
        snap = ref.get(transaction=txn)
        if not snap.exists:
            return False
        if snap.to_dict().get("status") != "scheduled":
            return False
        txn.update(ref, {"status": "sending", "lockedAt": _now_iso})
        return True

    return _txn(transaction, doc_ref)
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestClaimReminder -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/worker): transactional claim (scheduled→sending)"
```

---

### Task 1.11: Worker — send + transition (delivered / retry / failed)

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reminders_worker.py`:

```python
class TestProcessReminder:
    @patch("tools.reminders_worker.send_transactional_email")
    def test_success_marks_delivered(self, mock_send):
        from tools.reminders_worker import process_reminder, EmailSendResult
        mock_send.return_value = EmailSendResult(status_code=202)

        doc_ref = MagicMock()
        data = {
            "name": "test", "instructions": "body", "userId": "u1",
            "status": "sending", "attemptCount": 0, "channels": ["email"],
            "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "Europe/London",
        }
        process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "delivered"
        assert "deliveredAt" in args[0]

    @patch("tools.reminders_worker.send_transactional_email")
    def test_retryable_under_cap_reverts_to_scheduled(self, mock_send):
        from tools.reminders_worker import process_reminder
        from app.services.email_sender import EmailSendError
        mock_send.side_effect = EmailSendError("503", retryable=True, status_code=503)

        doc_ref = MagicMock()
        data = {"name": "x", "userId": "u1", "status": "sending", "attemptCount": 0,
                "channels": ["email"], "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "UTC"}
        process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "scheduled"
        assert args[0]["attemptCount"] == 1
        assert args[0]["lockedAt"] is None

    @patch("tools.reminders_worker.send_transactional_email")
    def test_retryable_at_cap_marks_failed(self, mock_send):
        from tools.reminders_worker import process_reminder
        from app.services.email_sender import EmailSendError
        mock_send.side_effect = EmailSendError("503", retryable=True, status_code=503)

        doc_ref = MagicMock()
        data = {"name": "x", "userId": "u1", "status": "sending", "attemptCount": 2,
                "channels": ["email"], "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "UTC"}
        process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "failed"
        assert args[0]["attemptCount"] == 3

    @patch("tools.reminders_worker.send_transactional_email")
    def test_non_retryable_marks_failed_immediately(self, mock_send):
        from tools.reminders_worker import process_reminder
        from app.services.email_sender import EmailSendError
        mock_send.side_effect = EmailSendError("403", retryable=False, status_code=403)

        doc_ref = MagicMock()
        data = {"name": "x", "userId": "u1", "status": "sending", "attemptCount": 0,
                "channels": ["email"], "fireAt": "2026-05-15T08:00:00+00:00", "timezone": "UTC"}
        process_reminder(doc_ref, data, user_email="a@b.com")
        args, _ = doc_ref.update.call_args
        assert args[0]["status"] == "failed"
        assert "403" in args[0]["lastError"]
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestProcessReminder -v`
Expected: FAIL — `process_reminder` doesn't exist

- [ ] **Step 3: Add process_reminder + main-loop wiring**

In `tools/reminders_worker.py`, add at top of imports:

```python
from app.services.email_sender import (
    send_transactional_email, EmailSendError, EmailSendResult,
)
```

Then append:

```python
RETRY_CAP = 3


def _build_email_body(reminder: dict) -> str:
    name = reminder.get("name", "")
    instructions = (reminder.get("instructions") or "").strip()
    body = instructions if instructions else name
    fire_at = reminder.get("fireAt", "")
    return f"{body}\n\n— XelaSphere Reminders\nScheduled for: {fire_at}\n"


def process_reminder(doc_ref, reminder: dict, *, user_email: str) -> None:
    """Send the email and update status. Idempotent against status=sending."""
    name = reminder.get("name", "Reminder")
    body_text = _build_email_body(reminder)
    attempt = reminder.get("attemptCount", 0)

    try:
        result = send_transactional_email(
            to_email=user_email,
            subject=f"Reminder: {name}",
            body_text=body_text,
        )
        doc_ref.update({
            "status": "delivered",
            "deliveredAt": datetime.now(timezone.utc).isoformat(),
            "attemptCount": attempt + 1,
            "lastError": None,
            "lockedAt": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"sent reminder name={name!r} attempts={attempt + 1} sg_status={result.status_code}")
        return
    except EmailSendError as exc:
        new_attempt = attempt + 1
        if exc.retryable and new_attempt < RETRY_CAP:
            doc_ref.update({
                "status": "scheduled",  # next tick retries
                "attemptCount": new_attempt,
                "lastError": str(exc),
                "lockedAt": None,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            logger.warning(f"retryable failure name={name!r} attempt={new_attempt}/{RETRY_CAP}: {exc}")
        else:
            doc_ref.update({
                "status": "failed",
                "attemptCount": new_attempt,
                "lastError": str(exc),
                "lockedAt": None,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            logger.error(f"giving up name={name!r} attempts={new_attempt}: {exc}")
```

Then update `main()` to actually process — replace the existing `main` body's logging line with:

```python
    docs = fetch_due_reminders(limit=args.limit)
    logger.info(f"fetched {len(docs)} due reminder(s)")
    if args.dry_run:
        for d in docs:
            logger.info(f"DRY: would send {d.id} {d.to_dict().get('name', '')!r}")
        return 0

    for snap in docs:
        data = snap.to_dict() or {}
        doc_ref = snap.reference
        if not try_claim_reminder(doc_ref):
            logger.info(f"skip {snap.id} (lost claim race)")
            continue
        # Look up the owning user's email
        user_id = data.get("userId")
        user_email = _get_user_email(user_id)
        if not user_email:
            logger.error(f"no email for user {user_id}; marking failed")
            doc_ref.update({
                "status": "failed",
                "lastError": "user has no email on file",
                "lockedAt": None,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            continue
        # Re-read after claim so attemptCount reflects the latest
        process_reminder(doc_ref, {**data, "status": "sending"}, user_email=user_email)
    return 0
```

And add the helper:

```python
def _get_user_email(user_id: str) -> str:
    """Read users/{uid}.email — cached in-process for the worker run."""
    if not user_id:
        return ""
    db = get_db()
    snap = db.collection("users").document(user_id).get()
    if not snap.exists:
        return ""
    return (snap.to_dict() or {}).get("email", "")
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_worker.py -v`
Expected: all worker tests pass (6+)

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/worker): send + transition (delivered/retry/failed)"
```

---

### Task 1.12: Worker — stuck-lock sweep

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_reminders_worker.py`:

```python
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
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestStuckLockSweep -v`
Expected: FAIL

- [ ] **Step 3: Implement sweep + call from main**

Append to `tools/reminders_worker.py`:

```python
def sweep_stuck_locks(stale_after_seconds: int = 300) -> int:
    """Find reminders stuck in status=sending with lockedAt older than threshold,
    revert to scheduled. Returns count of swept docs."""
    from datetime import timedelta
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat()
    docs = (
        db.collection_group("reminders")
        .where("status", "==", "sending")
        .where("lockedAt", "<=", cutoff)
        .limit(100)
        .stream()
    )
    count = 0
    for snap in docs:
        snap.reference.update({
            "status": "scheduled",
            "lockedAt": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        logger.warning(f"swept stuck lock {snap.id}")
        count += 1
    return count
```

In `main()`, call sweep at the start of the run:

```python
    # ...after the REMINDERS_ENABLED check:
    swept = sweep_stuck_locks()
    if swept:
        logger.info(f"swept {swept} stuck lock(s)")
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestStuckLockSweep -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/worker): stuck-lock sweep at start of each run"
```

---

### Task 1.13: Worker — late-fire banner in email body

**Files:**
- Modify: `tools/reminders_worker.py`
- Modify: `tests/test_reminders_worker.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_reminders_worker.py`:

```python
class TestLateFireBanner:
    def test_body_includes_late_banner_when_delay_over_10min(self):
        from tools.reminders_worker import _build_email_body
        late_fire_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        body = _build_email_body({"name": "x", "instructions": "body", "fireAt": late_fire_at})
        assert "delayed" in body.lower()

    def test_body_omits_late_banner_when_on_time(self):
        from tools.reminders_worker import _build_email_body
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        body = _build_email_body({"name": "x", "instructions": "body", "fireAt": fresh})
        assert "delayed" not in body.lower()
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminders_worker.py::TestLateFireBanner -v`
Expected: FAIL (current `_build_email_body` doesn't add the banner)

- [ ] **Step 3: Update _build_email_body**

Replace `_build_email_body` in `tools/reminders_worker.py`:

```python
LATE_DELAY_THRESHOLD_SECONDS = 600  # 10 min


def _build_email_body(reminder: dict) -> str:
    name = reminder.get("name", "")
    instructions = (reminder.get("instructions") or "").strip()
    body = instructions if instructions else name
    fire_at_iso = reminder.get("fireAt", "")
    banner = ""
    try:
        fire_at = datetime.fromisoformat(fire_at_iso)
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        delay = (datetime.now(timezone.utc) - fire_at).total_seconds()
        if delay > LATE_DELAY_THRESHOLD_SECONDS:
            banner = f"\n(This reminder was scheduled for {fire_at_iso} but delivery was delayed.)\n"
    except (ValueError, TypeError):
        pass
    return f"{body}{banner}\n\n— XelaSphere Reminders\nScheduled for: {fire_at_iso}\n"
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminders_worker.py -v`
Expected: all worker tests still pass, late-fire banner tests pass

- [ ] **Step 5: Commit**

```bash
git add tools/reminders_worker.py tests/test_reminders_worker.py
git commit -m "feat(reminders/worker): late-fire banner when delay > 10min"
```

---

### Task 1.14: systemd timer + service units

**Files:**
- Create: `deploy/xelasphere-reminders.service`
- Create: `deploy/xelasphere-reminders.timer`
- Create: `deploy/install-reminders-units.sh`

- [ ] **Step 1: Create the service unit**

Create `deploy/xelasphere-reminders.service`:

```ini
# /etc/systemd/system/xelasphere-reminders.service
#
# Reminders worker — one-shot Python CLI invoked by
# xelasphere-reminders.timer every minute. Queries Firestore for
# due reminders, sends via SendGrid, updates status. Exits.
#
# See deploy/xelasphere.service for the matching long-running
# Gunicorn unit pattern.

[Unit]
Description=XelaSphere Reminders Worker (one-shot, runs via timer)
After=network.target

[Service]
Type=oneshot
User=xelasphere
Group=xelasphere
WorkingDirectory=/opt/xelasphere
Environment=PATH=/opt/xelasphere/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=/etc/xelasphere/xelasphere.env
EnvironmentFile=/etc/xelasphere/mempalace.env
ExecStart=/opt/xelasphere/venv/bin/python -m tools.reminders_worker
StandardOutput=journal
StandardError=journal

# Hard cap: a single run should complete in seconds. If it stalls,
# kill it so the next timer tick can proceed cleanly.
TimeoutStartSec=120

# No restart — the timer is the retry mechanism.

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the timer unit**

Create `deploy/xelasphere-reminders.timer`:

```ini
# /etc/systemd/system/xelasphere-reminders.timer
#
# Fires xelasphere-reminders.service every minute.

[Unit]
Description=XelaSphere Reminders Worker timer (every minute)
Requires=xelasphere-reminders.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Unit=xelasphere-reminders.service
# Persistent=true catches up missed runs after a server restart —
# fine for our use case (late delivery is better than dropped).
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create install script**

Create `deploy/install-reminders-units.sh`:

```bash
#!/usr/bin/env bash
# Install/refresh the reminders worker systemd units.
# Run on the server as root.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="/etc/systemd/system"

install -m 0644 "$SRC_DIR/xelasphere-reminders.service" "$DEST/"
install -m 0644 "$SRC_DIR/xelasphere-reminders.timer"   "$DEST/"

systemctl daemon-reload
systemctl enable --now xelasphere-reminders.timer

echo "Installed. Verify:"
echo "  systemctl status xelasphere-reminders.timer"
echo "  systemctl list-timers xelasphere-reminders.timer"
echo "  journalctl -u xelasphere-reminders.service -f"
```

- [ ] **Step 4: Make install script executable**

Run: `chmod +x deploy/install-reminders-units.sh`

- [ ] **Step 5: Commit**

```bash
git add deploy/xelasphere-reminders.service deploy/xelasphere-reminders.timer deploy/install-reminders-units.sh
git commit -m "deploy(reminders): systemd timer + service units + install script"
```

---

### Task 1.15: Stage 1 deploy + manual smoke test

This task is manual — no TDD. It proves the end-to-end loop works in production before any UI exists.

**Files:** none changed (operational task).

- [ ] **Step 1: Get SendGrid API key + verify sender domain**

- Create a SendGrid account if needed
- Generate an API key with "Mail Send" permission
- In SendGrid → Sender Authentication, set up domain authentication for `xelasphere.io` (DNS records: CNAMEs for SPF + DKIM). Wait for verification (can take minutes to hours).
- Note the API key — you'll add it to env in step 3.

- [ ] **Step 2: Sync code to /opt/xelasphere**

On the server (as root):
```bash
cd /opt/xelasphere
git fetch origin
git checkout <this-branch>
git pull
./venv/bin/pip install -r requirements-cpu.txt
```

- [ ] **Step 3: Add env vars**

Edit `/etc/xelasphere/xelasphere.env` (as root):
```
REMINDERS_ENABLED=true
SENDGRID_API_KEY=SG.your-real-key
SENDGRID_FROM_EMAIL=noreply@xelasphere.io
SENDGRID_FROM_NAME=XelaSphere Reminders
```

Restart Flask app: `systemctl restart xelasphere`

- [ ] **Step 4: Install the reminders worker timer**

```bash
sudo bash /opt/xelasphere/deploy/install-reminders-units.sh
```

Verify:
```bash
systemctl status xelasphere-reminders.timer
systemctl list-timers xelasphere-reminders.timer
journalctl -u xelasphere-reminders.service -n 50
```

You should see the worker run within ~2 minutes and log `fetched 0 due reminder(s)`.

- [ ] **Step 5: Create a test reminder via curl as admin**

Get a session cookie (log in as admin in browser, copy `session` cookie value), then:
```bash
curl -X POST https://xelasphere.io/reminders \
  -H "Content-Type: application/json" \
  -H "Cookie: session=<your-cookie>" \
  -H "X-CSRF-Token: <your-csrf-token>" \
  -d '{
    "name": "M0 smoke test",
    "instructions": "If this arrives in my inbox, the full server loop works.",
    "fireAtLocal": "<2 minutes from now in ISO local time>",
    "timezone": "Europe/London"
  }'
```

Should return `{"id": "rem_...", "reminder": {...}}` with HTTP 201.

- [ ] **Step 6: Verify Firestore + index**

Open Firebase console → Firestore → `users/<your-uid>/reminders/rem_...` — confirm the doc exists with `status=scheduled`.

The first worker tick may log a Firestore error linking to a "create composite index" page — **click it and create the index**. Wait 1–5 minutes for the index to build.

- [ ] **Step 7: Wait + check inbox**

Within 60–120 seconds of the scheduled `fireAt`, an email titled "Reminder: M0 smoke test" should land in the admin inbox. Verify:

- Worker logs show `sent reminder ... sg_status=202`
- Firestore doc now `status=delivered`, `deliveredAt` set
- Email is in inbox, not spam

If anything fails: do NOT proceed to Milestone 2. Fix and re-test.

- [ ] **Step 8: Document the smoke test result**

Append to PR description: "Stage 1 smoke test: ✅ email received at <time>, doc status=delivered, no stuck locks."

---

## Milestone 2 — AI intent detection wired, no UI (Stage 2)

Reminders auto-create from chat utterances. No card renders yet; admin verifies Firestore writes for false positives.

### Task 2.1: is_reminder_request regex

**Files:**
- Create: `app/services/reminder_intent.py`
- Create: `tests/test_reminder_intent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_reminder_intent.py`:

```python
import pytest
from app.services.reminder_intent import is_reminder_request


class TestIsReminderRequest:
    @pytest.mark.parametrize("prompt", [
        "remind me to call mom tomorrow at 9am",
        "leave a reminder for me to pay rent",
        "set a reminder for the meeting at 3pm",
        "remind me in 2 hours to take the cake out",
        "Remind my customer to pay £184 in the morning",
    ])
    def test_positive(self, prompt):
        assert is_reminder_request(prompt) is True

    @pytest.mark.parametrize("prompt", [
        "what's a reminder app?",
        "the user is reminded that they should pay",  # not a request
        "I'm trying to remember the email",
        "remember when we used to play games?",
        "",
        "x",
    ])
    def test_negative(self, prompt):
        assert is_reminder_request(prompt) is False
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminder_intent.py::TestIsReminderRequest -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement is_reminder_request**

Create `app/services/reminder_intent.py`:

```python
"""Intent detection + LLM extraction for reminder requests.

Mirrors the regex+downstream-call pattern used by is_email_request /
parse_email_from_response in app/blueprints/email_routes.py.
M0 ships English-only. Multilingual support is a follow-up.
"""
import json
import logging
import re
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


_REMINDER_PATTERNS = [
    re.compile(r"\b(?:please\s+)?remind\s+(?:me|us|him|her|them|my\s+\w+)\b", re.IGNORECASE),
    re.compile(r"\b(?:set|leave|create|schedule|add)\s+(?:a\s+|an\s+)?reminder\b", re.IGNORECASE),
    re.compile(r"\bnudge\s+me\b", re.IGNORECASE),
]


def is_reminder_request(user_prompt: str) -> bool:
    """Cheap regex pre-filter: does the user *plausibly* want a reminder?

    False positives are fine — extract_reminder() is the real gatekeeper.
    False negatives skip the whole feature for that turn.
    """
    if not user_prompt or len(user_prompt) < 5:
        return False
    return any(p.search(user_prompt) for p in _REMINDER_PATTERNS)
```

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminder_intent.py::TestIsReminderRequest -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/reminder_intent.py tests/test_reminder_intent.py
git commit -m "feat(reminders/intent): is_reminder_request regex pre-filter"
```

---

### Task 2.2: extract_reminder LLM call

**Files:**
- Modify: `app/services/reminder_intent.py`
- Modify: `tests/test_reminder_intent.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reminder_intent.py`:

```python
from unittest.mock import patch, MagicMock


class TestExtractReminder:
    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_returns_structured_fields(self, mock_llm):
        from app.services.reminder_intent import extract_reminder
        mock_llm.return_value = json.dumps({
            "name": "Pay customer £184",
            "instructions": "collect payment",
            "fireAtLocal": "2026-05-15T09:00:00",
            "timezone": "Europe/London",
        })
        result = extract_reminder(
            prompt="remind me to pay tomorrow morning",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="Europe/London",
        )
        assert result["name"] == "Pay customer £184"
        assert result["fireAtLocal"] == "2026-05-15T09:00:00"

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_handles_garbage_llm_output(self, mock_llm):
        from app.services.reminder_intent import extract_reminder
        mock_llm.return_value = "not json at all"
        result = extract_reminder(
            prompt="remind me",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="UTC",
        )
        # Garbage → all-None dict, never raises
        assert result == {"name": None, "instructions": None, "fireAtLocal": None, "timezone": None}

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_returns_partial_on_missing_time(self, mock_llm):
        from app.services.reminder_intent import extract_reminder
        mock_llm.return_value = json.dumps({
            "name": "Call mom",
            "instructions": None,
            "fireAtLocal": None,
            "timezone": "Europe/London",
        })
        result = extract_reminder(
            prompt="remind me to call mom",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="Europe/London",
        )
        assert result["name"] == "Call mom"
        assert result["fireAtLocal"] is None
```

- [ ] **Step 2: Run, expect failure**

Run: `./venv/bin/pytest tests/test_reminder_intent.py::TestExtractReminder -v`
Expected: FAIL — `extract_reminder` / `_llm_extract_json` don't exist

- [ ] **Step 3: Implement extract_reminder**

Append to `app/services/reminder_intent.py`:

```python
_EXTRACT_SYSTEM_PROMPT = """\
You convert a user's reminder request into structured JSON. Output ONLY JSON, no prose.

Schema:
{
  "name": "short title for the reminder (max 80 chars)",
  "instructions": "fuller body, sent as the email content (max 500 chars, nullable)",
  "fireAtLocal": "ISO datetime in the user's local timezone, YYYY-MM-DDTHH:MM:SS (nullable if you can't determine when)",
  "timezone": "IANA timezone string (use the provided default if unsure)"
}

Rules:
- If the user says 'morning' assume 09:00 local.
- If the user says 'afternoon' assume 14:00 local.
- If the user says 'evening' assume 18:00 local.
- If the user says 'tomorrow' compute relative to the provided current time.
- If no time at all is mentioned, set fireAtLocal to null. Do NOT guess a time.
- Never set fireAtLocal in the past.
"""


def _llm_extract_json(prompt: str, now_utc: datetime, user_timezone: str) -> str:
    """Call the cheap Groq model in JSON mode. Returns raw string (may be junk)."""
    from app.grog_api import GroqClient  # match the project's existing import

    user_msg = (
        f"Current UTC time: {now_utc.isoformat()}\n"
        f"User's timezone: {user_timezone}\n"
        f"User's message: {prompt}\n"
    )
    try:
        client = GroqClient()
        # Use whatever cheap-and-fast model the project already calls — match smart_router.py picks.
        # The exact model id below should match an existing entry in app/model_catalog.py.
        return client.chat_completion(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=300,
        )
    except Exception as exc:
        logger.warning(f"[reminders/extract] LLM call failed: {exc}")
        return ""


def extract_reminder(
    prompt: str, *, now_utc: datetime, user_timezone: str
) -> dict:
    """Extract reminder fields from prompt. Always returns a dict with 4 keys
    (name/instructions/fireAtLocal/timezone), any of which may be None.

    Never raises — garbage LLM output yields all-None.
    """
    raw = _llm_extract_json(prompt, now_utc, user_timezone)
    null_result = {"name": None, "instructions": None, "fireAtLocal": None, "timezone": None}
    if not raw:
        return null_result
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning(f"[reminders/extract] non-JSON output: {raw[:200]!r}")
        return null_result
    if not isinstance(parsed, dict):
        return null_result
    return {
        "name": parsed.get("name") or None,
        "instructions": parsed.get("instructions") or None,
        "fireAtLocal": parsed.get("fireAtLocal") or None,
        "timezone": parsed.get("timezone") or user_timezone,
    }
```

> Important: the `GroqClient` import + method names must match what already exists in `app/grog_api.py`. Run `grep -n "class.*Client\|def chat_completion\|def complete" app/grog_api.py` first; if the surface is different, adapt this call site to match. The system prompt and contract for `_llm_extract_json` (input args, output is a string) stay the same.

- [ ] **Step 4: Run, expect pass**

Run: `./venv/bin/pytest tests/test_reminder_intent.py -v`
Expected: all reminder_intent tests pass (14)

- [ ] **Step 5: Commit**

```bash
git add app/services/reminder_intent.py tests/test_reminder_intent.py
git commit -m "feat(reminders/intent): extract_reminder LLM call (JSON mode, never raises)"
```

---

### Task 2.3: Wire intent into chat_routes (both stream paths)

**Files:**
- Modify: `app/blueprints/chat_routes.py`

This task is non-TDD because chat_routes is a 4000-line file with intricate streaming state. We add a small parallel call that does NOT block the main stream.

- [ ] **Step 1: Find both email-intent hook points**

Run: `grep -n "is_email_request\|looks_like_email" app/blueprints/chat_routes.py`

You should see calls around lines 2114 and 4019. These are the two streaming code paths — the reminder hook goes adjacent to each.

- [ ] **Step 2: Add the import and helper**

Near the existing `from app.blueprints.email_routes import is_email_request, ...` import (around line 94), add:

```python
from app.services.reminder_intent import is_reminder_request, extract_reminder
from app.services.reminder_model import (
    new_reminder_dict, compute_fire_at_utc,
)
```

After the imports block, define a small helper (place near other top-level helpers in this file):

```python
def _maybe_create_reminder_from_prompt(
    user_prompt: str,
    user_id: str,
    user_email: str,
    user_timezone: str,
    source_chat_id: str | None,
) -> str | None:
    """If the prompt is a reminder request and extraction yields all required
    fields, persist it and return the new reminder id. Otherwise return None.

    This runs *in parallel* with the chat completion (caller decides), and is
    intentionally fire-and-forget: any error logs and returns None.
    """
    try:
        from app.config import Config, db
        if not Config.REMINDERS_ENABLED:
            return None
        if not user_id or not user_email:
            return None
        if not is_reminder_request(user_prompt):
            return None
        from datetime import datetime, timezone
        extracted = extract_reminder(
            prompt=user_prompt,
            now_utc=datetime.now(timezone.utc),
            user_timezone=user_timezone or "UTC",
        )
        if not (extracted.get("name") and extracted.get("fireAtLocal") and extracted.get("timezone")):
            return None  # missing field — assistant will ask in plain language (Task 2.4)
        try:
            fire_at_utc = compute_fire_at_utc(extracted["fireAtLocal"], extracted["timezone"])
        except Exception:
            return None
        if fire_at_utc <= datetime.now(timezone.utc):
            return None
        reminder = new_reminder_dict(
            user_id=user_id,
            name=extracted["name"].strip()[:200],
            instructions=(extracted.get("instructions") or "").strip()[:1000],
            fire_at_utc=fire_at_utc,
            timezone_str=extracted["timezone"],
            source_chat_id=source_chat_id,
            created_by="ai",
        )
        doc_ref = db.collection("users").document(user_id).collection("reminders").document()
        reminder["id"] = doc_ref.id
        doc_ref.set(reminder)
        logger.info(f"[reminders] AI-created {doc_ref.id} from chat {source_chat_id}")
        return doc_ref.id
    except Exception as exc:
        logger.warning(f"[reminders] _maybe_create_reminder_from_prompt error (non-fatal): {exc}")
        return None
```

- [ ] **Step 3: Wire into the first stream path (around line 2114)**

Find the block where `is_email_request(user_prompt_raw)` is called (~line 2114). After that block, before the response is finalized, add:

```python
# Reminders M0 — parallel creation if intent detected
reminder_id = _maybe_create_reminder_from_prompt(
    user_prompt=user_prompt_raw,
    user_id=session["user"]["uid"] if "user" in session else None,
    user_email=session["user"]["email"] if "user" in session else None,
    user_timezone=request.headers.get("X-Timezone") or "UTC",
    source_chat_id=chat_id if 'chat_id' in locals() else None,
)
if reminder_id:
    logger.info(f"[reminders] persisted {reminder_id}; sentinel tag emitted in Task 3.1")
```

(`chat_id` is whatever variable the surrounding scope uses — adjust to match local names.)

Repeat for the second hook point (~line 4019) with the analogous local variable names.

- [ ] **Step 4: Smoke-run the existing chat test suite**

Run: `./venv/bin/pytest tests/test_chat_routes_stream_meta.py -v`
Expected: still passes — our addition is a no-op when REMINDERS_ENABLED=false (which is the test default unless conftest overrides).

If conftest forces REMINDERS_ENABLED=true (we did this in Task 1.2), the chat tests should still pass because the parallel call swallows all errors and returns None — but verify.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(reminders): wire intent detection into both chat stream paths

Parallel reminder creation when REMINDERS_ENABLED=true and intent
detected. Sentinel tag emission deferred to Task 3.1. All errors
swallowed to never break the chat stream."
```

---

### Task 2.4: Missing-fields system prompt augmentation

**Files:**
- Modify: `app/blueprints/chat_routes.py`

When `is_reminder_request()` matches but extraction returns null `fireAtLocal`, augment the system prompt for this turn so the assistant asks for the missing detail naturally.

- [ ] **Step 1: Locate where the chat system prompt is assembled**

Run: `grep -n "system_prompt\|system_message\|messages.append" app/blueprints/chat_routes.py | head -20`

You're looking for the spot where the model's `messages` list is built right before the API call.

- [ ] **Step 2: Add a helper to detect "wants reminder but missing fields"**

In `app/services/reminder_intent.py`, append:

```python
def needs_clarification(prompt: str, extracted: dict) -> bool:
    """Returns True when intent matched but extraction is missing fireAtLocal."""
    if not extracted:
        return False
    return (
        is_reminder_request(prompt)
        and extracted.get("name") is not None
        and extracted.get("fireAtLocal") is None
    )
```

Add a parallel test in `tests/test_reminder_intent.py`:

```python
class TestNeedsClarification:
    def test_yes_when_intent_and_no_time(self):
        from app.services.reminder_intent import needs_clarification
        assert needs_clarification(
            "remind me to call mom",
            {"name": "Call mom", "instructions": None, "fireAtLocal": None, "timezone": "UTC"},
        ) is True

    def test_no_when_time_present(self):
        from app.services.reminder_intent import needs_clarification
        assert needs_clarification(
            "remind me to call mom at 9am",
            {"name": "Call mom", "instructions": None, "fireAtLocal": "2026-05-15T09:00", "timezone": "UTC"},
        ) is False

    def test_no_when_intent_doesnt_match(self):
        from app.services.reminder_intent import needs_clarification
        assert needs_clarification(
            "what time is it",
            {"name": None, "instructions": None, "fireAtLocal": None, "timezone": "UTC"},
        ) is False
```

Run: `./venv/bin/pytest tests/test_reminder_intent.py::TestNeedsClarification -v`
Expected: 3 passed.

- [ ] **Step 3: In chat_routes, prepend a system-prompt hint when needs_clarification is true**

Where you do `_maybe_create_reminder_from_prompt` (Task 2.3), restructure it slightly so the caller can get back `(reminder_id, needs_clarification_flag)` instead of just `reminder_id`. Replace the helper:

```python
def _maybe_create_reminder_from_prompt(
    user_prompt: str,
    user_id: str,
    user_email: str,
    user_timezone: str,
    source_chat_id: str | None,
) -> tuple[str | None, bool]:
    """Returns (reminder_id_or_None, needs_clarification_flag)."""
    try:
        from app.config import Config, db
        from app.services.reminder_intent import (
            is_reminder_request, extract_reminder, needs_clarification,
        )
        from app.services.reminder_model import new_reminder_dict, compute_fire_at_utc
        from datetime import datetime, timezone

        if not Config.REMINDERS_ENABLED:
            return None, False
        if not user_id or not user_email:
            return None, False
        if not is_reminder_request(user_prompt):
            return None, False
        extracted = extract_reminder(
            prompt=user_prompt,
            now_utc=datetime.now(timezone.utc),
            user_timezone=user_timezone or "UTC",
        )
        if needs_clarification(user_prompt, extracted):
            return None, True
        if not (extracted.get("name") and extracted.get("fireAtLocal") and extracted.get("timezone")):
            return None, False
        try:
            fire_at_utc = compute_fire_at_utc(extracted["fireAtLocal"], extracted["timezone"])
        except Exception:
            return None, False
        if fire_at_utc <= datetime.now(timezone.utc):
            return None, False
        reminder = new_reminder_dict(
            user_id=user_id,
            name=extracted["name"].strip()[:200],
            instructions=(extracted.get("instructions") or "").strip()[:1000],
            fire_at_utc=fire_at_utc,
            timezone_str=extracted["timezone"],
            source_chat_id=source_chat_id,
            created_by="ai",
        )
        doc_ref = db.collection("users").document(user_id).collection("reminders").document()
        reminder["id"] = doc_ref.id
        doc_ref.set(reminder)
        logger.info(f"[reminders] AI-created {doc_ref.id} from chat {source_chat_id}")
        return doc_ref.id, False
    except Exception as exc:
        logger.warning(f"[reminders] _maybe_create_reminder_from_prompt error: {exc}")
        return None, False
```

Then at each call site (lines 2114 and 4019 areas), replace the old assignment with:

```python
reminder_id, reminder_needs_clarification = _maybe_create_reminder_from_prompt(
    user_prompt=user_prompt_raw,
    user_id=session["user"]["uid"] if "user" in session else None,
    user_email=session["user"]["email"] if "user" in session else None,
    user_timezone=request.headers.get("X-Timezone") or "UTC",
    source_chat_id=chat_id if 'chat_id' in locals() else None,
)
if reminder_needs_clarification:
    # Append a one-turn system-prompt nudge so the assistant asks naturally
    messages.insert(0, {
        "role": "system",
        "content": (
            "The user wants to set a reminder but did not say when. "
            "Ask them naturally for the missing time or date. "
            "Do not create the reminder yourself; the system will retry on their next turn."
        ),
    })
```

Adjust the `messages` variable name to match the local scope (the existing code calls it something specific in each stream path — `chat_messages`, `request_messages`, etc.). Grep for `messages.append` or similar near each hook point.

- [ ] **Step 4: Run all chat + reminder tests**

Run: `./venv/bin/pytest tests/test_reminder_intent.py tests/test_chat_routes_stream_meta.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/reminder_intent.py tests/test_reminder_intent.py app/blueprints/chat_routes.py
git commit -m "feat(reminders): needs_clarification path — assistant asks naturally"
```

---

### Task 2.5: Stage 2 deploy + manual verification

This task verifies the AI auto-creates reminders correctly with no false positives before adding any UI.

- [ ] **Step 1: Deploy backend to prod**

```bash
cd /opt/xelasphere
git pull
./venv/bin/pip install -r requirements-cpu.txt  # likely no-op
systemctl restart xelasphere
```

- [ ] **Step 2: Open the chat as admin, exercise both paths**

In the live chat UI, send these messages and observe Firestore:

| Message | Expected |
|---------|----------|
| "remind me in 2 minutes to test stage 2" | Reminder doc appears with `createdBy=ai`; email arrives ~2min later |
| "remind me to call mom" | NO reminder doc created. Assistant asks "When should I remind you?" |
| "I'm trying to remember the email address" | NO reminder doc, no clarification (intent didn't match) |
| "set a reminder for friday at 10am to pick up dry cleaning" | Reminder created |

- [ ] **Step 3: Check logs for false positives**

```bash
journalctl -u xelasphere -n 200 | grep "\[reminders\]"
```

Look for `AI-created` events you didn't expect. If false positives are >10%, do NOT proceed — tighten the regex in Task 2.1 first.

- [ ] **Step 4: Document the stage 2 result**

Append to PR: "Stage 2 verification: 4/4 expected, 0 false positives in N test turns."

---

## Milestone 3 — Inline card + edit modal, admin only (Stage 3)

The visible UI lands. Cards render in chat, modal lets the user edit.

### Task 3.1: Server emits sentinel tag in stream

**Files:**
- Modify: `app/blueprints/chat_routes.py`

- [ ] **Step 1: Locate the assistant streaming chunk emission**

Where you call `_maybe_create_reminder_from_prompt`, the server is mid-stream. After persisting and getting a reminder_id, we need to emit a sentinel tag into the assistant's text stream so the frontend can render the card inline.

- [ ] **Step 2: Decide WHERE in the stream to emit the tag**

Looking at the existing artifact-tag pattern: the project emits sentinel tags during streaming. Grep:

```bash
grep -n "yield.*<artifact\|yield.*data:.*<" app/blueprints/chat_routes.py | head -10
grep -rn "sentinel\|placeholder" app/blueprints/chat_routes.py | head -10
```

If artifact tags are emitted as plain text inside the streamed assistant content (i.e., the assistant's reply text contains the literal `<artifact id="..." />`), do the same for reminders. If artifact tags are emitted as a separate SSE event type, use that channel.

- [ ] **Step 3: Emit the reminder tag**

At each call site (both stream paths), once you have `reminder_id`, prepend the tag to the assistant's reply. The simplest invariant: prepend `<reminder id="rem_xxx" />\n` to the first streamed text chunk for this turn.

Pseudocode (adapt to the local stream-emit function):

```python
if reminder_id and not _reminder_tag_emitted:
    yield f'<reminder id="{reminder_id}" />\n'
    _reminder_tag_emitted = True
```

Where `_reminder_tag_emitted` is a per-turn flag local to the stream function.

- [ ] **Step 4: Smoke-test that the tag reaches the browser**

In dev: send "remind me in 2 min to test" → in browser devtools network tab → watch the streaming chat response → confirm the response text contains `<reminder id="rem_..." />`.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(reminders): server emits <reminder id=.../> sentinel in stream"
```

---

### Task 3.2: Frontend stream-renderer recognizes the tag

**Files:**
- Modify: `app/static/js/streamRenderer.js`
- Create: `app/static/js/remindersModule.js`

- [ ] **Step 1: Find where artifact tags are recognized**

Run: `grep -n "artifact\|<artifact" app/static/js/streamRenderer.js | head -20`

If artifact handling lives in another JS file, follow imports. The pattern to find: regex-replace incoming stream text to swap `<artifact id="..." />` for a DOM placeholder.

- [ ] **Step 2: Create the remindersModule.js skeleton**

Create `app/static/js/remindersModule.js`:

```javascript
// Reminders M0 — inline card render, edit modal, Schedules tab.
//
// Stream-renderer hook: window.RemindersModule.renderTagPlaceholder(reminderId)
// returns the HTML string to swap in place of <reminder id=.../>.
//
// On DOM ready, scans for any .reminder-card placeholders and binds click handlers.

(function () {
  'use strict';

  const STATE_LABEL = {
    scheduled: 'scheduled',
    paused: 'paused',
    delivered: 'delivered',
    failed: 'failed',
    cancelled: 'cancelled',
    sending: 'sending',
  };

  function renderTagPlaceholder(reminderId) {
    // Initial placeholder. Real card hydrates on DOM-ready scan or via fetchAndRender.
    return `<button type="button" class="reminder-card reminder-card--loading"
                    data-reminder-id="${reminderId}"
                    aria-label="Reminder loading">
              <span class="reminder-card__icon">🔔</span>
              <span class="reminder-card__body">
                <span class="reminder-card__name">Loading reminder…</span>
              </span>
            </button>`;
  }

  async function fetchReminder(id) {
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, {
      credentials: 'same-origin',
    });
    if (!resp.ok) throw new Error(`fetch failed: ${resp.status}`);
    const data = await resp.json();
    return data.reminder;
  }

  function formatWhen(reminder) {
    const dt = new Date(reminder.fireAt);
    const tz = reminder.timezone || 'UTC';
    return dt.toLocaleString(undefined, {
      timeZone: tz,
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }) + ' · ' + tz.split('/').pop().replace('_', ' ');
  }

  function renderCardInner(reminder) {
    const state = reminder.status;
    const label = STATE_LABEL[state] || state;
    return `<span class="reminder-card__icon">🔔</span>
            <span class="reminder-card__body">
              <span class="reminder-card__name">${escapeHtml(reminder.name)}</span>
              <span class="reminder-card__when">${escapeHtml(formatWhen(reminder))} · ${label}</span>
            </span>`;
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async function hydrateCard(button) {
    const id = button.dataset.reminderId;
    if (!id) return;
    try {
      const reminder = await fetchReminder(id);
      button.classList.remove('reminder-card--loading');
      button.classList.add(`reminder-card--${reminder.status}`);
      button.innerHTML = renderCardInner(reminder);
      button.dataset.reminderJson = JSON.stringify(reminder);
    } catch (err) {
      button.classList.add('reminder-card--error');
      button.innerHTML = '<span class="reminder-card__name">Failed to load reminder</span>';
    }
  }

  function scanAndHydrate(root = document) {
    root.querySelectorAll('.reminder-card[data-reminder-id]').forEach(btn => {
      if (btn.dataset.hydrated) return;
      btn.dataset.hydrated = '1';
      hydrateCard(btn);
      btn.addEventListener('click', () => {
        if (window.RemindersModule && window.RemindersModule.openModal) {
          const reminder = JSON.parse(btn.dataset.reminderJson || '{}');
          window.RemindersModule.openModal(reminder);
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => scanAndHydrate());
  // Re-scan after chat stream updates the DOM
  document.addEventListener('chat-message-rendered', () => scanAndHydrate());

  window.RemindersModule = window.RemindersModule || {};
  window.RemindersModule.renderTagPlaceholder = renderTagPlaceholder;
  window.RemindersModule.hydrateCard = hydrateCard;
  window.RemindersModule.scanAndHydrate = scanAndHydrate;
  window.RemindersModule.fetchReminder = fetchReminder;
  // openModal added in Task 3.4
})();
```

- [ ] **Step 3: Hook into stream renderer**

In the stream renderer (whichever file processes the streamed text into DOM — `streamRenderer.js` or similar), find the artifact-tag replacement code and add an analogous block:

```javascript
// after existing artifact handling
text = text.replace(/<reminder\s+id="([^"]+)"\s*\/>/g, function (match, id) {
  if (window.RemindersModule && window.RemindersModule.renderTagPlaceholder) {
    return window.RemindersModule.renderTagPlaceholder(id);
  }
  return ''; // remove tag if module missing
});
```

If the project's stream-render path emits a `chat-message-rendered` event already, the scan in `remindersModule.js` will pick up the new cards automatically. If not, dispatch one after every render: `document.dispatchEvent(new CustomEvent('chat-message-rendered'))`.

- [ ] **Step 4: Include the new JS + (placeholder) CSS in `index.html`**

In `app/templates/index.html`, find where other module scripts and stylesheets are included. Add:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/reminders.css') }}">
<script defer src="{{ url_for('static', filename='js/remindersModule.js') }}"></script>
```

(`reminders.css` is created in Task 3.5; create an empty file now so the link doesn't 404.)

```bash
touch app/static/css/reminders.css
```

- [ ] **Step 5: Smoke-test in dev**

Restart Flask dev. In browser, send "remind me in 5 minutes to verify the card". The card placeholder should appear and hydrate to the real name + timestamp.

- [ ] **Step 6: Commit**

```bash
git add app/static/js/remindersModule.js app/static/js/streamRenderer.js app/static/css/reminders.css app/templates/index.html
git commit -m "feat(reminders/ui): inline card render via <reminder> sentinel tag"
```

---

### Task 3.3: Edit modal — HTML partial

**Files:**
- Create: `app/templates/_reminders_modal.html`
- Modify: `app/templates/index.html`

- [ ] **Step 1: Create the modal markup**

Create `app/templates/_reminders_modal.html`:

```html
<div id="reminders-modal" class="reminders-modal" hidden role="dialog" aria-modal="true" aria-labelledby="reminders-modal-title">
  <div class="reminders-modal__backdrop" data-close></div>
  <div class="reminders-modal__panel">
    <header class="reminders-modal__header">
      <h2 id="reminders-modal-title">Edit reminder</h2>
      <button type="button" class="reminders-modal__close" data-close aria-label="Close">✕</button>
    </header>

    <form class="reminders-modal__form" data-reminders-form>
      <input type="hidden" name="id" value="">

      <label class="reminders-modal__field">
        <span class="reminders-modal__label">Name</span>
        <input type="text" name="name" maxlength="200" required>
      </label>

      <label class="reminders-modal__field">
        <span class="reminders-modal__label">Instructions <small>(sent as email body)</small></span>
        <textarea name="instructions" rows="3" maxlength="1000"></textarea>
      </label>

      <fieldset class="reminders-modal__field reminders-modal__when">
        <legend class="reminders-modal__label">When</legend>
        <input type="date" name="date" required>
        <input type="time" name="time" required>
        <span class="reminders-modal__tz" data-tz>—</span>
      </fieldset>

      <label class="reminders-modal__field reminders-modal__field--disabled" title="Recurrence coming soon">
        <span class="reminders-modal__label">Doesn't repeat</span>
        <select disabled><option>Doesn't repeat</option></select>
      </label>

      <footer class="reminders-modal__footer">
        <div class="reminders-modal__left-actions">
          <button type="button" class="btn btn--ghost" data-action="pause">Pause</button>
          <button type="button" class="btn btn--danger" data-action="delete">Delete</button>
        </div>
        <div class="reminders-modal__right-actions">
          <button type="button" class="btn btn--ghost" data-close>Cancel</button>
          <button type="submit" class="btn btn--primary">Save</button>
        </div>
      </footer>
    </form>
  </div>
</div>
```

- [ ] **Step 2: Include the modal in index.html**

In `app/templates/index.html`, near other `{% include %}` modals (search for `_settings_modal.html` for placement), add:

```html
{% include "_reminders_modal.html" %}
```

- [ ] **Step 3: Visual smoke (forced visible)**

Open the page in a browser, then in devtools console: `document.getElementById('reminders-modal').hidden = false;`
Confirm the modal markup renders (it will be unstyled until Task 3.5).

- [ ] **Step 4: Commit**

```bash
git add app/templates/_reminders_modal.html app/templates/index.html
git commit -m "feat(reminders/ui): edit modal HTML partial"
```

---

### Task 3.4: Edit modal — JS (open / save / pause / delete)

**Files:**
- Modify: `app/static/js/remindersModule.js`

- [ ] **Step 1: Append modal-handling code to remindersModule.js**

In `app/static/js/remindersModule.js`, inside the IIFE before the `window.RemindersModule.openModal` placeholder line, add:

```javascript
  function getCsrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function getBrowserTz() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch {
      return 'UTC';
    }
  }

  function splitFireAtLocal(reminder) {
    // Convert UTC fireAt + reminder.timezone → date input + time input (HH:MM)
    const dt = new Date(reminder.fireAt);
    const tz = reminder.timezone || getBrowserTz();
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(dt);
    const get = type => parts.find(p => p.type === type)?.value || '';
    return {
      date: `${get('year')}-${get('month')}-${get('day')}`,
      time: `${get('hour')}:${get('minute')}`,
    };
  }

  function openModal(reminder) {
    reminder = reminder || {};
    const modal = document.getElementById('reminders-modal');
    if (!modal) return;
    const form = modal.querySelector('[data-reminders-form]');
    form.elements['id'].value = reminder.id || '';
    form.elements['name'].value = reminder.name || '';
    form.elements['instructions'].value = reminder.instructions || '';

    let dateStr = '', timeStr = '';
    if (reminder.fireAt) {
      const split = splitFireAtLocal(reminder);
      dateStr = split.date;
      timeStr = split.time;
    } else {
      const now = new Date(Date.now() + 60 * 60 * 1000);  // 1h ahead default
      dateStr = now.toISOString().slice(0, 10);
      timeStr = now.toTimeString().slice(0, 5);
    }
    form.elements['date'].value = dateStr;
    form.elements['time'].value = timeStr;

    const tz = reminder.timezone || getBrowserTz();
    modal.querySelector('[data-tz]').textContent = tz;
    form.dataset.timezone = tz;

    // Pause button label
    const pauseBtn = modal.querySelector('[data-action="pause"]');
    pauseBtn.textContent = reminder.status === 'paused' ? 'Resume' : 'Pause';
    pauseBtn.dataset.targetStatus = reminder.status === 'paused' ? 'scheduled' : 'paused';

    // Hide delete for delivered/sending
    modal.querySelector('[data-action="delete"]').hidden =
      ['delivered', 'sending'].includes(reminder.status);

    modal.hidden = false;
  }

  function closeModal() {
    const modal = document.getElementById('reminders-modal');
    if (modal) modal.hidden = true;
  }

  async function saveFromForm(ev) {
    ev.preventDefault();
    const form = ev.currentTarget;
    const id = form.elements['id'].value;
    const tz = form.dataset.timezone || getBrowserTz();
    const fireAtLocal = `${form.elements['date'].value}T${form.elements['time'].value}:00`;

    const body = {
      name: form.elements['name'].value.trim(),
      instructions: form.elements['instructions'].value.trim(),
      fireAtLocal,
      timezone: tz,
    };
    try {
      const url = id ? `/reminders/${encodeURIComponent(id)}` : `/reminders`;
      const method = id ? 'PATCH' : 'POST';
      const resp = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrf() },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        alert(`Could not save reminder: ${err.error || resp.statusText}`);
        return;
      }
      closeModal();
      // Re-hydrate any card with this id
      const data = await resp.json();
      const newId = data.id || (data.reminder && data.reminder.id) || id;
      document.querySelectorAll(`.reminder-card[data-reminder-id="${newId}"]`).forEach(btn => {
        btn.dataset.hydrated = '';
        hydrateCard(btn);
      });
      // Refresh Schedules tab if open
      if (window.RemindersModule.refreshSchedulesTab) {
        window.RemindersModule.refreshSchedulesTab();
      }
    } catch (e) {
      alert(`Network error: ${e.message}`);
    }
  }

  async function pauseFromForm(form) {
    const id = form.elements['id'].value;
    if (!id) return;
    const targetStatus = form.querySelector('[data-action="pause"]').dataset.targetStatus;
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrf() },
      body: JSON.stringify({ status: targetStatus }),
    });
    if (resp.ok) {
      closeModal();
      document.querySelectorAll(`.reminder-card[data-reminder-id="${id}"]`).forEach(btn => {
        btn.dataset.hydrated = '';
        hydrateCard(btn);
      });
    }
  }

  async function deleteFromForm(form) {
    const id = form.elements['id'].value;
    if (!id) return;
    if (!confirm('Delete this reminder?')) return;
    const resp = await fetch(`/reminders/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': getCsrf() },
    });
    if (resp.ok) {
      closeModal();
      document.querySelectorAll(`.reminder-card[data-reminder-id="${id}"]`).forEach(btn => {
        btn.remove();
      });
      if (window.RemindersModule.refreshSchedulesTab) {
        window.RemindersModule.refreshSchedulesTab();
      }
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('reminders-modal');
    if (!modal) return;
    modal.querySelectorAll('[data-close]').forEach(el =>
      el.addEventListener('click', closeModal));
    const form = modal.querySelector('[data-reminders-form]');
    form.addEventListener('submit', saveFromForm);
    form.querySelector('[data-action="pause"]').addEventListener('click', () => pauseFromForm(form));
    form.querySelector('[data-action="delete"]').addEventListener('click', () => deleteFromForm(form));
  });

  window.RemindersModule.openModal = openModal;
  window.RemindersModule.closeModal = closeModal;
```

- [ ] **Step 2: Manual test the modal**

In dev, click an existing reminder card → modal opens prefilled. Change the name, click Save → name updates on card without page reload. Click Pause → card greys out.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/remindersModule.js
git commit -m "feat(reminders/ui): modal open/save/pause/delete + card re-hydration"
```

---

### Task 3.5: CSS — card + modal + state variants

**Files:**
- Modify: `app/static/css/reminders.css`

- [ ] **Step 1: Write the stylesheet**

Replace `app/static/css/reminders.css` with:

```css
/* Reminders M0 — inline card + modal. Match the project's existing
   visual idiom; tweak vars to align with the design tokens used elsewhere. */

.reminder-card {
  display: inline-flex;
  align-items: center;
  gap: 0.5em;
  padding: 0.55em 0.75em;
  margin: 0.5em 0;
  border: 1px solid var(--color-border, #d0d7de);
  border-radius: 10px;
  background: var(--color-card-bg, #fff);
  color: var(--color-text, #1f2328);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;
  max-width: 100%;
}
.reminder-card:hover {
  background: var(--color-card-bg-hover, #f6f8fa);
  border-color: var(--color-border-strong, #afb8c1);
}
.reminder-card__icon { font-size: 1.1em; flex: 0 0 auto; }
.reminder-card__body { display: flex; flex-direction: column; min-width: 0; }
.reminder-card__name { font-weight: 500; }
.reminder-card__when { color: var(--color-text-muted, #656d76); font-size: 0.85em; }

.reminder-card--loading { opacity: 0.65; }
.reminder-card--paused { opacity: 0.65; }
.reminder-card--delivered { opacity: 0.7; cursor: default; }
.reminder-card--delivered:hover { background: var(--color-card-bg, #fff); }
.reminder-card--failed { border-color: var(--color-danger, #cf222e); background: rgba(207,34,46,0.05); }
.reminder-card--cancelled { text-decoration: line-through; opacity: 0.55; }

/* Modal */
.reminders-modal {
  position: fixed; inset: 0; z-index: 9000;
  display: flex; align-items: center; justify-content: center;
}
.reminders-modal[hidden] { display: none; }
.reminders-modal__backdrop {
  position: absolute; inset: 0;
  background: rgba(0, 0, 0, 0.45);
}
.reminders-modal__panel {
  position: relative;
  background: var(--color-card-bg, #fff);
  color: var(--color-text, #1f2328);
  border-radius: 14px;
  padding: 1.25em 1.5em;
  width: min(560px, 95vw);
  max-height: 90vh; overflow-y: auto;
  box-shadow: 0 12px 40px rgba(0,0,0,0.25);
}
.reminders-modal__header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 0.75em;
}
.reminders-modal__header h2 { margin: 0; font-size: 1.15rem; }
.reminders-modal__close {
  background: transparent; border: 0; font-size: 1.1rem; cursor: pointer;
  color: var(--color-text-muted, #656d76);
}
.reminders-modal__field {
  display: block; margin: 0.85em 0;
}
.reminders-modal__label {
  display: block; font-weight: 500; margin-bottom: 0.3em;
}
.reminders-modal__field input[type="text"],
.reminders-modal__field textarea,
.reminders-modal__field input[type="date"],
.reminders-modal__field input[type="time"] {
  width: 100%; padding: 0.5em 0.6em;
  border: 1px solid var(--color-border, #d0d7de); border-radius: 7px;
  font: inherit; box-sizing: border-box;
}
.reminders-modal__when { border: 0; padding: 0; margin: 0.85em 0; }
.reminders-modal__when input { width: auto; margin-right: 0.5em; }
.reminders-modal__tz {
  color: var(--color-text-muted, #656d76); font-size: 0.9em;
}
.reminders-modal__field--disabled select {
  width: 100%; padding: 0.5em 0.6em;
  border: 1px solid var(--color-border, #d0d7de); border-radius: 7px;
  background: var(--color-disabled-bg, #f6f8fa);
  color: var(--color-text-muted, #656d76);
}
.reminders-modal__footer {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 1em;
}
.reminders-modal__left-actions, .reminders-modal__right-actions {
  display: flex; gap: 0.5em;
}
.reminders-modal .btn {
  padding: 0.5em 0.95em; border-radius: 7px; border: 1px solid transparent;
  cursor: pointer; font: inherit;
}
.reminders-modal .btn--primary {
  background: var(--color-primary, #0969da); color: #fff;
}
.reminders-modal .btn--ghost {
  background: transparent; color: var(--color-text, #1f2328);
  border-color: var(--color-border, #d0d7de);
}
.reminders-modal .btn--danger {
  background: transparent; color: var(--color-danger, #cf222e);
  border-color: var(--color-danger, #cf222e);
}
```

- [ ] **Step 2: Visual smoke**

Open chat, trigger a reminder, click card → modal should look like the ChatGPT-style mock from the spec (rounded panel, dark backdrop, properly aligned fields).

- [ ] **Step 3: Commit**

```bash
git add app/static/css/reminders.css
git commit -m "feat(reminders/ui): card + modal CSS with state variants"
```

---

### Task 3.6: Stage 3 deploy + manual UX QA as admin

- [ ] **Step 1: Deploy**

```bash
cd /opt/xelasphere
git pull
systemctl restart xelasphere
```

- [ ] **Step 2: UX QA matrix**

In the live chat, exercise every interactive path:

- [ ] Send "remind me in 2 min to test card" → card renders inline → email arrives on time
- [ ] Click the card → modal opens prefilled
- [ ] Change the name → Save → card name updates
- [ ] Reschedule for 5 min later → Save → email arrives at new time
- [ ] Pause a future reminder → card greys out → email does NOT arrive at original time
- [ ] Resume → email arrives at correct time
- [ ] Delete a future reminder → card disappears → no email
- [ ] Try to set fireAt in the past → error toast / Save blocked
- [ ] Reload page mid-reminder → card re-hydrates correctly from sentinel tag
- [ ] Fail SendGrid intentionally (wrong API key temporarily) → card shows red `failed` state after 3 attempts

- [ ] **Step 3: Document**

Append to PR: "Stage 3 UX QA: 10/10 paths verified by admin."

---

## Milestone 4 — Schedules tab + manual create (Stage 4)

Replace settings → Schedules "Coming Soon" placeholder with the full management UI.

### Task 4.1: Replace placeholder with pane markup

**Files:**
- Modify: `app/templates/_settings_modal.html`

- [ ] **Step 1: Replace the Schedules pane**

In `app/templates/_settings_modal.html`, find the line:

```html
<section class="settings-pane" data-pane="schedules">
    <h2 class="settings-pane-title">Schedules</h2>
    <p class="settings-pane-empty">Coming Soon.</p>
</section>
```

Replace with:

```html
<section class="settings-pane" data-pane="schedules">
    <h2 class="settings-pane-title">Schedules</h2>

    <div class="schedules-pane__actions">
        <button type="button" class="btn btn--primary" data-schedules-new>+ New reminder</button>
    </div>

    <h3 class="schedules-pane__section-title">Upcoming <span data-schedules-upcoming-count>(0)</span></h3>
    <ul class="schedules-pane__list" data-schedules-upcoming>
        <li class="schedules-pane__empty">No upcoming reminders.</li>
    </ul>

    <h3 class="schedules-pane__section-title schedules-pane__section-title--collapsible">
        Past <span data-schedules-past-count>(0)</span>
        <button type="button" class="schedules-pane__toggle" data-schedules-toggle-past>show</button>
    </h3>
    <ul class="schedules-pane__list" data-schedules-past hidden>
        <li class="schedules-pane__empty">No past reminders.</li>
    </ul>
</section>
```

- [ ] **Step 2: Visual smoke**

Open settings → Schedules tab. Should render the section headings + a "+ New reminder" button (unstyled is OK until Task 4.3 CSS).

- [ ] **Step 3: Commit**

```bash
git add app/templates/_settings_modal.html
git commit -m "feat(reminders/ui): replace Schedules placeholder with pane markup"
```

---

### Task 4.2: Schedules tab JS — list rendering + new-reminder hook

**Files:**
- Modify: `app/static/js/remindersModule.js`

- [ ] **Step 1: Append list-rendering code**

In `remindersModule.js` (inside the IIFE), add:

```javascript
  function renderRow(reminder) {
    const li = document.createElement('li');
    li.className = `schedules-row schedules-row--${reminder.status}`;
    li.dataset.reminderId = reminder.id;
    li.innerHTML = `
      <span class="schedules-row__icon">${reminder.status === 'delivered' ? '✓' : '🔔'}</span>
      <span class="schedules-row__body">
        <span class="schedules-row__name">${escapeHtml(reminder.name)}</span>
        <span class="schedules-row__when">${escapeHtml(formatWhen(reminder))} · ${STATE_LABEL[reminder.status] || reminder.status}</span>
      </span>
    `;
    li.addEventListener('click', () => openModal(reminder));
    return li;
  }

  async function refreshSchedulesTab() {
    const pane = document.querySelector('[data-pane="schedules"]');
    if (!pane) return;
    try {
      const resp = await fetch('/reminders', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const data = await resp.json();
      const reminders = data.reminders || [];
      const upcoming = reminders.filter(r => ['scheduled', 'paused', 'failed', 'sending'].includes(r.status));
      const past = reminders.filter(r => ['delivered', 'cancelled'].includes(r.status));

      const upUl = pane.querySelector('[data-schedules-upcoming]');
      const pastUl = pane.querySelector('[data-schedules-past]');
      const upCount = pane.querySelector('[data-schedules-upcoming-count]');
      const pastCount = pane.querySelector('[data-schedules-past-count]');

      upUl.innerHTML = '';
      if (upcoming.length === 0) {
        upUl.innerHTML = '<li class="schedules-pane__empty">No upcoming reminders.</li>';
      } else {
        upcoming.forEach(r => upUl.appendChild(renderRow(r)));
      }
      pastUl.innerHTML = '';
      if (past.length === 0) {
        pastUl.innerHTML = '<li class="schedules-pane__empty">No past reminders.</li>';
      } else {
        past.forEach(r => pastUl.appendChild(renderRow(r)));
      }
      upCount.textContent = `(${upcoming.length})`;
      pastCount.textContent = `(${past.length})`;
    } catch (e) {
      console.warn('refreshSchedulesTab failed', e);
    }
  }

  function bindSchedulesTab() {
    const pane = document.querySelector('[data-pane="schedules"]');
    if (!pane) return;
    pane.querySelector('[data-schedules-new]')?.addEventListener('click', () => {
      openModal({});  // empty reminder → create flow
    });
    const pastToggle = pane.querySelector('[data-schedules-toggle-past]');
    const pastList = pane.querySelector('[data-schedules-past]');
    pastToggle?.addEventListener('click', () => {
      const isHidden = pastList.hasAttribute('hidden');
      if (isHidden) {
        pastList.removeAttribute('hidden');
        pastToggle.textContent = 'hide';
      } else {
        pastList.setAttribute('hidden', '');
        pastToggle.textContent = 'show';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', bindSchedulesTab);
  // Refresh whenever the settings modal opens to this tab
  document.addEventListener('settings-tab-shown', (ev) => {
    if (ev.detail && ev.detail.tab === 'schedules') refreshSchedulesTab();
  });

  window.RemindersModule.refreshSchedulesTab = refreshSchedulesTab;
```

- [ ] **Step 2: Wire the settings modal to dispatch `settings-tab-shown`**

In `app/static/js/settingsModal.js`, find where a tab click is handled (likely a click listener on `.settings-rail-item`). After the existing show-pane code, add:

```javascript
document.dispatchEvent(new CustomEvent('settings-tab-shown', { detail: { tab: paneName } }));
```

(`paneName` is whatever local variable holds the data-tab value — match existing naming.)

- [ ] **Step 3: Smoke-test**

Open settings → Schedules → should see Upcoming/Past lists populated from existing reminders. Click `+ New reminder` → modal opens empty.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/remindersModule.js app/static/js/settingsModal.js
git commit -m "feat(reminders/ui): Schedules tab list + manual create button"
```

---

### Task 4.3: Schedules tab CSS

**Files:**
- Modify: `app/static/css/reminders.css`

- [ ] **Step 1: Append to reminders.css**

```css
/* Schedules tab */
.schedules-pane__actions { margin-bottom: 1em; }
.schedules-pane__section-title {
  margin-top: 1.25em; margin-bottom: 0.5em;
  font-size: 1rem; font-weight: 600;
  color: var(--color-text-muted, #656d76);
  display: flex; align-items: center; gap: 0.5em;
}
.schedules-pane__toggle {
  margin-left: auto; background: transparent; border: 0;
  color: var(--color-link, #0969da); cursor: pointer; font: inherit;
}
.schedules-pane__list {
  list-style: none; padding: 0; margin: 0;
  border-top: 1px solid var(--color-border, #d0d7de);
}
.schedules-pane__list[hidden] { display: none; }
.schedules-pane__empty {
  padding: 0.85em; color: var(--color-text-muted, #656d76);
  font-style: italic;
}
.schedules-row {
  display: flex; gap: 0.65em; align-items: flex-start;
  padding: 0.75em;
  border-bottom: 1px solid var(--color-border, #d0d7de);
  cursor: pointer;
}
.schedules-row:hover { background: var(--color-card-bg-hover, #f6f8fa); }
.schedules-row__body { display: flex; flex-direction: column; min-width: 0; }
.schedules-row__name { font-weight: 500; }
.schedules-row__when { color: var(--color-text-muted, #656d76); font-size: 0.85em; }
.schedules-row--paused { opacity: 0.65; }
.schedules-row--delivered { opacity: 0.7; }
.schedules-row--failed { border-left: 3px solid var(--color-danger, #cf222e); }
.schedules-row--cancelled { text-decoration: line-through; opacity: 0.55; }
```

- [ ] **Step 2: Visual smoke**

Open settings → Schedules → confirm rows look polished. Hover should highlight. Past section toggles open/closed.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/reminders.css
git commit -m "feat(reminders/ui): Schedules tab styles"
```

---

### Task 4.4: Stage 4 deploy + manual QA

- [ ] **Step 1: Deploy**

```bash
cd /opt/xelasphere
git pull
systemctl restart xelasphere
```

- [ ] **Step 2: QA matrix**

- [ ] Open settings → Schedules → see Upcoming + Past sections
- [ ] Click + New reminder → modal opens empty
- [ ] Fill name + future date/time → Save → reminder appears in Upcoming
- [ ] Click an existing reminder row → modal opens prefilled
- [ ] Edit + Save → row updates
- [ ] Pause → row greys
- [ ] Delete → row disappears
- [ ] Wait for a reminder to fire → it moves from Upcoming to Past on next tab open

- [ ] **Step 3: Document**

Append to PR: "Stage 4 QA: 8/8 paths verified."

---

## Milestone 5 — Open to all + observability + ship (Stage 5)

Final stage. Verify the kill switch, add operational sugar, monitor.

### Task 5.1: Verify kill switch in every path

**Files:**
- Already touched files; this task is verification + small fixes if anything's missing.

- [ ] **Step 1: Grep every code path that's gated on REMINDERS_ENABLED**

```bash
grep -rn "REMINDERS_ENABLED" app/ tools/
```

Expected hits:
- `app/config.py` (definition)
- `app/blueprints/reminders_routes.py:create_reminder` (returns 503 if false)
- `app/blueprints/chat_routes.py` `_maybe_create_reminder_from_prompt` (returns None,False if false)
- `tools/reminders_worker.py:main` (exits early if false)

If any path is missing the check, add it.

- [ ] **Step 2: Manual kill-switch test**

In dev: `export REMINDERS_ENABLED=false` and restart. Send "remind me in 1 min". Expected:
- No reminder created in Firestore
- POST /reminders returns 503 with body `{"error": "Reminders are not enabled."}`
- Worker logs "REMINDERS_ENABLED=false; exiting"

Re-enable: `export REMINDERS_ENABLED=true`, restart, redo the test → reminder fires normally.

- [ ] **Step 3: Commit any fixes**

```bash
git add -p
git commit -m "fix(reminders): close any kill-switch gaps"
```

(If no gaps, skip this step.)

---

### Task 5.2: Worker — structured JSON logging

**Files:**
- Modify: `tools/reminders_worker.py`

- [ ] **Step 1: Replace string logger format with JSON**

In `tools/reminders_worker.py`, replace the `logging.basicConfig(...)` call in `main()` with:

```python
import json as _json
class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)
        return _json.dumps(payload)


def _setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
```

Then in `main()`, replace `logging.basicConfig(...)` with `_setup_logging()`.

Where individual log calls would benefit from structured fields (e.g., the send-success log), use:

```python
logger.info("reminder_sent", extra={"extra_fields": {
    "reminderId": snap.id, "userId": data.get("userId"),
    "attempts": new_attempt, "sg_status": result.status_code,
}})
```

- [ ] **Step 2: Smoke-run**

```bash
./venv/bin/python -m tools.reminders_worker --dry-run
```

Output should be JSON one-line-per-log entry: `{"ts": "...", "level": "INFO", ...}`.

- [ ] **Step 3: Commit**

```bash
git add tools/reminders_worker.py
git commit -m "feat(reminders/worker): structured JSON logging for ops"
```

---

### Task 5.3: In-app toast for failed reminders since last visit

**Files:**
- Modify: `app/blueprints/reminders_routes.py`
- Modify: `app/static/js/remindersModule.js`

- [ ] **Step 1: Add a server endpoint to count failed reminders unseen by user**

Append to `app/blueprints/reminders_routes.py`:

```python
@reminders_bp.route("/reminders/unseen-failures", methods=["GET"])
@error_handler
@login_required
def unseen_failures():
    """Count reminders in status=failed since user's lastSeenAt."""
    user_id = session["user"]["uid"]
    user_doc = db.collection("users").document(user_id).get()
    last_seen = (user_doc.to_dict() or {}).get("reminders_last_seen_at") if user_doc.exists else None

    q = db.collection("users").document(user_id).collection("reminders").where("status", "==", "failed")
    if last_seen:
        from google.cloud import firestore as gc_firestore
        q = q.where("updatedAt", ">", last_seen)
    count = sum(1 for _ in q.limit(50).stream())
    return jsonify({"count": count})


@reminders_bp.route("/reminders/mark-seen", methods=["POST"])
@error_handler
@login_required
@csrf_required
def mark_seen():
    user_id = session["user"]["uid"]
    from datetime import datetime, timezone
    db.collection("users").document(user_id).set(
        {"reminders_last_seen_at": datetime.now(timezone.utc).isoformat()},
        merge=True,
    )
    return jsonify({"ok": True})
```

- [ ] **Step 2: Frontend — poll on page load, show toast if count > 0**

Append to `app/static/js/remindersModule.js`:

```javascript
  async function checkUnseenFailures() {
    try {
      const resp = await fetch('/reminders/unseen-failures', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.count > 0) {
        showToast(`${data.count} reminder${data.count === 1 ? '' : 's'} failed to deliver. Open Schedules to see why.`);
        // Mark as seen after showing
        fetch('/reminders/mark-seen', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'X-CSRF-Token': getCsrf() },
        });
      }
    } catch (e) { /* silent */ }
  }

  function showToast(message) {
    // Reuse the project's toast system if available, else minimal fallback.
    if (window.Toast && window.Toast.show) {
      window.Toast.show(message, { type: 'warning', duration: 6000 });
      return;
    }
    const el = document.createElement('div');
    el.className = 'reminders-toast';
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 6000);
  }

  document.addEventListener('DOMContentLoaded', () => setTimeout(checkUnseenFailures, 1500));
```

If the project already has a toast module, the `window.Toast.show` path uses it; otherwise the minimal fallback inserts a styled div. Add to `reminders.css`:

```css
.reminders-toast {
  position: fixed; bottom: 24px; right: 24px; z-index: 9001;
  padding: 0.8em 1.1em;
  background: var(--color-warning-bg, #fff8c5);
  color: var(--color-warning-text, #57534e);
  border: 1px solid var(--color-warning-border, #d4a72c);
  border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.15);
  max-width: 360px;
}
```

- [ ] **Step 3: Smoke-test**

Force a failure (point SENDGRID_API_KEY to garbage temporarily, fire a reminder, restore the key). Reload page → toast appears once, doesn't reappear on next reload.

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/reminders_routes.py app/static/js/remindersModule.js app/static/css/reminders.css
git commit -m "feat(reminders/ui): unseen-failures toast on page load"
```

---

### Task 5.4: Stage 5 — open to all + 48h monitoring

This task is operational.

- [ ] **Step 1: Final deploy with feature flag on**

Ensure `/etc/xelasphere/xelasphere.env` has `REMINDERS_ENABLED=true`. Restart:

```bash
cd /opt/xelasphere
git pull
systemctl restart xelasphere
systemctl restart xelasphere-reminders.timer  # just to be sure
```

- [ ] **Step 2: Define the 48h monitoring checklist**

Run these every ~6h for two days. Record values in PR description.

```bash
# Worker health
journalctl -u xelasphere-reminders.service --since "6 hours ago" | grep -c "fetched"
# Should be ~360 (one per minute over 6h, give or take)

# Delivery success rate
journalctl -u xelasphere-reminders.service --since "6 hours ago" \
  | grep -E "reminder_sent|giving up" | wc -l
journalctl -u xelasphere-reminders.service --since "6 hours ago" \
  | grep "reminder_sent" | wc -l
# Compute: sent / (sent + failed). Goal: ≥ 99%.

# Stuck-lock incidents
journalctl -u xelasphere-reminders.service --since "6 hours ago" | grep -c "swept"
# Goal: 0 in steady state. Non-zero → worker is crashing somewhere; investigate.

# P99 worker run time
journalctl -u xelasphere-reminders.service --since "6 hours ago" \
  | grep "Started\|Finished" | head -20
# Eyeball latency. Goal: < 5s typical.
```

- [ ] **Step 3: Definition of done**

- All 5 rollout stages green (1–4 done in prior milestones; 5 just demonstrated)
- 48h post-launch: zero `lock_expired` log entries, p99 worker run time < 5s, SendGrid delivery rate ≥ 99%
- At least 3 real reminders scheduled and received without intervention
- Customer-payment use case from spec works end-to-end: "remind my customer to pay £184 in the morning" → email arrives at 9am local

- [ ] **Step 4: Close out the PR**

Update PR description with the 48h numbers, hit Merge.

```bash
# After merge to main:
git checkout main
git pull
```

---

## Appendix — Operational quick reference

```bash
# Force a manual worker run (e.g., to test new code without waiting for timer)
sudo -u xelasphere /opt/xelasphere/venv/bin/python -m tools.reminders_worker

# Dry-run (no actual sends)
sudo -u xelasphere /opt/xelasphere/venv/bin/python -m tools.reminders_worker --dry-run

# Disable the feature fast
sudo sed -i 's/^REMINDERS_ENABLED=true/REMINDERS_ENABLED=false/' /etc/xelasphere/xelasphere.env
sudo systemctl restart xelasphere

# Stop the worker timer (existing reminders untouched, just no new fires)
sudo systemctl stop xelasphere-reminders.timer

# Re-enable
sudo systemctl start xelasphere-reminders.timer

# Tail worker logs
sudo journalctl -u xelasphere-reminders.service -f
```

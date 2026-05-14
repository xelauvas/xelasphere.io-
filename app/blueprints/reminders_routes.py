"""Reminders CRUD blueprint. M0: email-only, login-required.

Routes:
- GET    /reminders               — list current user's reminders (optional ?status= filter)
- POST   /reminders               — create
- GET    /reminders/<id>          — fetch one
- PATCH  /reminders/<id>          — update (name/instructions/fireAtLocal/timezone/status/_retry)
- DELETE /reminders/<id>          — delete
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, session
from google.cloud import firestore as gc_firestore

from app.config import db, Config
from app.blueprints.shared import login_required, error_handler
from app.limiter import limiter, csrf_required
from app.services.reminder_model import (
    new_reminder_dict, validate_reminder_payload, compute_fire_at_utc,
    STATUS_SCHEDULED, STATUS_FAILED,
)

logger = logging.getLogger(__name__)

reminders_bp = Blueprint("reminders", __name__)


# ==================== Helpers ====================
_EDITABLE_FIELDS = {"name", "instructions", "fireAtLocal", "timezone", "status"}
# User-settable statuses via PATCH. delivered/sending/failed are server-set.
_PATCH_ALLOWED_STATUSES = {"scheduled", "paused", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ==================== Routes ====================
@reminders_bp.route("/reminders", methods=["GET"])
@error_handler
@login_required
def list_reminders():
    """List current user's reminders, ordered by fireAt DESC. Optional ?status= filter."""
    user_id = session["user"]["uid"]
    status_filter = request.args.get("status")

    coll = db.collection("users").document(user_id).collection("reminders")
    query = coll
    if status_filter:
        query = query.where("status", "==", status_filter)
    docs = query.order_by("fireAt", direction=gc_firestore.Query.DESCENDING).limit(200).stream()

    items = []
    for d in docs:
        item = d.to_dict() or {}
        item["id"] = d.id  # ensure id is on the wire even if doc body is missing it
        items.append(item)
    return jsonify({"reminders": items})


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

    doc_ref = db.collection("users").document(user_id).collection("reminders").document()
    reminder = new_reminder_dict(
        reminder_id=doc_ref.id,
        user_id=user_id,
        name=payload["name"].strip(),
        instructions=(payload.get("instructions") or "").strip(),
        fire_at_utc=fire_at_utc,
        timezone_str=payload["timezone"],
        source_chat_id=payload.get("sourceChatId"),
        source_message_id=payload.get("sourceMessageId"),
        created_by=payload.get("createdBy", "user"),
    )
    doc_ref.set(reminder)

    # Cache the user's default timezone so future reminders pre-fill correctly.
    db.collection("users").document(user_id).set(
        {"defaultTimezone": payload["timezone"]}, merge=True,
    )

    logger.info(f"[reminders] created {doc_ref.id} for {user_id} fire_at={reminder['fireAt']}")
    return jsonify({"id": doc_ref.id, "reminder": reminder}), 201


@reminders_bp.route("/reminders/<reminder_id>", methods=["GET"])
@error_handler
@login_required
def get_reminder(reminder_id: str):
    user_id = session["user"]["uid"]
    snap = (
        db.collection("users").document(user_id)
        .collection("reminders").document(reminder_id).get()
    )
    if not snap.exists:
        return jsonify({"error": "Reminder not found."}), 404
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return jsonify({"reminder": data})


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

    doc_ref = (
        db.collection("users").document(user_id)
        .collection("reminders").document(reminder_id)
    )
    snap = doc_ref.get()
    if not snap.exists:
        return jsonify({"error": "Reminder not found."}), 404
    current = snap.to_dict() or {}

    update = {}
    if "name" in payload:
        raw_name = payload["name"]
        if not isinstance(raw_name, str) or not raw_name.strip():
            return jsonify({"error": "name must be a non-empty string"}), 400
        update["name"] = raw_name.strip()

    if "instructions" in payload:
        instr = payload["instructions"]
        if instr is not None and not isinstance(instr, str):
            return jsonify({"error": "instructions must be a string or null"}), 400
        update["instructions"] = (instr or "").strip()

    if "fireAtLocal" in payload or "timezone" in payload:
        new_tz = payload.get("timezone") or current.get("timezone")
        if not new_tz:
            return jsonify({"error": "timezone needed to reschedule"}), 400

        if "fireAtLocal" in payload:
            # Explicit reschedule: take fireAtLocal at face value
            new_local = payload["fireAtLocal"]
        else:
            # Timezone-only PATCH: keep the local wall-clock the same, but
            # interpret it in the new timezone. Recover the wall-clock
            # string by converting stored UTC → old local tz → strip tzinfo.
            old_tz_str = current.get("timezone")
            if not old_tz_str:
                return jsonify({"error": "cannot reinterpret tz without prior timezone"}), 400
            try:
                old_tz = ZoneInfo(old_tz_str)
                stored_utc = datetime.fromisoformat(current["fireAt"])
                if stored_utc.tzinfo is None:
                    stored_utc = stored_utc.replace(tzinfo=timezone.utc)
                wall = stored_utc.astimezone(old_tz)
                new_local = wall.strftime("%Y-%m-%dT%H:%M:%S")
            except (ValueError, KeyError, TypeError) as exc:
                return jsonify({"error": f"could not recover wall-clock time: {exc}"}), 400

        try:
            fire_at_utc = compute_fire_at_utc(new_local, new_tz)
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": f"invalid fireAtLocal/timezone: {exc}"}), 400
        if fire_at_utc <= datetime.now(timezone.utc):
            return jsonify({"error": "fireAtLocal must be in the future"}), 400
        update["fireAt"] = fire_at_utc.isoformat()
        update["timezone"] = new_tz

    if "status" in payload:
        if payload["status"] not in _PATCH_ALLOWED_STATUSES:
            return jsonify({"error": f"status must be one of {sorted(_PATCH_ALLOWED_STATUSES)}"}), 400
        update["status"] = payload["status"]

    if payload.get("_retry") and current.get("status") == STATUS_FAILED:
        update["status"] = STATUS_SCHEDULED
        update["attemptCount"] = 0
        update["lastError"] = None
        update["lockedAt"] = None

    if not update:
        return jsonify({"error": "no fields to update"}), 400

    update["updatedAt"] = _now_iso()
    doc_ref.update(update)
    logger.info(f"[reminders] patched {reminder_id} for {user_id}: {sorted(update.keys())}")
    return jsonify({"reminder": {**current, **update, "id": reminder_id}})


@reminders_bp.route("/reminders/<reminder_id>", methods=["DELETE"])
@error_handler
@login_required
@csrf_required
def delete_reminder(reminder_id: str):
    user_id = session["user"]["uid"]
    doc_ref = (
        db.collection("users").document(user_id)
        .collection("reminders").document(reminder_id)
    )
    snap = doc_ref.get()
    if not snap.exists:
        return jsonify({"error": "Reminder not found."}), 404
    doc_ref.delete()
    logger.info(f"[reminders] deleted {reminder_id} for {user_id}")
    return jsonify({"ok": True})


# ==================== Unseen-failures helpers (M5) ====================
@reminders_bp.route("/reminders/unseen-failures", methods=["GET"])
@error_handler
@login_required
def unseen_failures():
    """Count reminders in status=failed updated since the user's last visit.

    Frontend polls this on page load and shows a one-shot toast.
    """
    user_id = session["user"]["uid"]
    user_doc = db.collection("users").document(user_id).get()
    last_seen = None
    if user_doc.exists:
        last_seen = (user_doc.to_dict() or {}).get("reminders_last_seen_at")

    q = (
        db.collection("users").document(user_id)
        .collection("reminders").where("status", "==", "failed")
    )
    if last_seen:
        q = q.where("updatedAt", ">", last_seen)
    count = sum(1 for _ in q.limit(50).stream())
    return jsonify({"count": count})


@reminders_bp.route("/reminders/mark-seen", methods=["POST"])
@error_handler
@login_required
@csrf_required
def mark_seen():
    """Stamp users/{uid}.reminders_last_seen_at = now. Frontend calls this
    right after showing the unseen-failures toast."""
    user_id = session["user"]["uid"]
    db.collection("users").document(user_id).set(
        {"reminders_last_seen_at": _now_iso()}, merge=True,
    )
    return jsonify({"ok": True})

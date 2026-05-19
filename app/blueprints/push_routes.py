"""Web Push routes for subscribe / unsubscribe / VAPID key + prompt dismiss."""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from flask import Blueprint, jsonify, request, session

from app.config import Config, db
from app.blueprints.shared import login_required, error_handler
from app.limiter import csrf_required

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
            "keys": keys,
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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        recently = (datetime.now(timezone.utc) - dt) < timedelta(days=30)
    except Exception:
        recently = False
    return jsonify({"dismissedRecently": recently}), 200

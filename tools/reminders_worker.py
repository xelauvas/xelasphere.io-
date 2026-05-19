"""Reminders worker — CLI poller for due reminders. Run by systemd timer.

Usage:
    python -m tools.reminders_worker [--limit N] [--dry-run]

Lifecycle (per invocation):
  1. Stuck-lock sweep: revert reminders stuck in status='sending' with
     stale lockedAt back to 'scheduled'.
  2. Query Firestore collection_group('reminders') for due+scheduled.
  3. For each: transactional claim (scheduled→sending), send via
     SendGrid, transition (delivered / scheduled+retry / failed).
  4. Exit.

Idempotent: if the worker crashes mid-send, the stuck-lock sweep on the
next tick recovers the doc. If two workers race the same doc, the
transactional claim ensures only one sends.
"""
from __future__ import annotations

import argparse
import html as _html
import json as _json
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("reminders_worker")

RETRY_CAP = 3
LATE_DELAY_THRESHOLD_SECONDS = 600  # 10 min — banner threshold for late-fire emails
STUCK_LOCK_STALE_AFTER_SECONDS = 300  # 5 min — sweep threshold

_db_cache = None


def get_db():
    """Lazy Firestore client. Single seam tests can patch."""
    global _db_cache
    if _db_cache is None:
        from app.config import db as app_db
        _db_cache = app_db
    return _db_cache


# ==================== Email-body builder ====================
def _build_chat_url(reminder: dict) -> Optional[str]:
    """Return the deeplink to the chat that created this reminder, or None
    when the reminder has no sourceChatId."""
    chat_id = reminder.get("sourceChatId")
    if not chat_id:
        return None
    from app.config import Config
    return f"{Config.APP_BASE_URL}/c/{chat_id}"


def _late_delivery_seconds(reminder: dict) -> Optional[float]:
    """Returns seconds-late if past threshold, else None."""
    try:
        fire_at = datetime.fromisoformat(reminder.get("fireAt", ""))
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        delay = (datetime.now(timezone.utc) - fire_at).total_seconds()
        if delay > LATE_DELAY_THRESHOLD_SECONDS:
            return delay
    except (ValueError, TypeError):
        pass
    return None


def _build_email_body(reminder: dict) -> str:
    """Construct the plain-text email body. Adds a late-delivery banner
    when fireAt is more than LATE_DELAY_THRESHOLD_SECONDS in the past."""
    name = reminder.get("name", "")
    instructions = (reminder.get("instructions") or "").strip()
    body = instructions if instructions else name
    fire_at_iso = reminder.get("fireAt", "")
    banner = ""
    if _late_delivery_seconds(reminder) is not None:
        banner = f"\n(This reminder was scheduled for {fire_at_iso} but delivery was delayed.)\n"
    chat_url = _build_chat_url(reminder)
    link_line = f"\nOpen chat: {chat_url}" if chat_url else ""
    return f"{body}{banner}\n\n— XelaSphere Reminders\nScheduled for: {fire_at_iso}{link_line}\n"


def _build_email_html(reminder: dict) -> str:
    """HTML version of the reminder email. Inline styles only — many email
    clients strip <style> tags. Escapes user content to prevent injection."""
    name_safe = _html.escape(reminder.get("name") or "")
    instructions = (reminder.get("instructions") or "").strip()
    body_safe = _html.escape(instructions if instructions else (reminder.get("name") or ""))
    fire_at_iso_safe = _html.escape(reminder.get("fireAt", ""))
    banner_html = ""
    if _late_delivery_seconds(reminder) is not None:
        banner_html = (
            '<p style="margin:0 0 16px 0;padding:10px 14px;background:#FFF3CD;'
            'border-left:4px solid #856404;color:#856404;font-size:13px;'
            'border-radius:4px;">'
            f'This reminder was scheduled for {fire_at_iso_safe} but delivery was delayed.'
            '</p>'
        )
    chat_url = _build_chat_url(reminder)
    button_html = ""
    if chat_url:
        button_html = (
            f'<p style="margin:24px 0 0 0;"><a href="{_html.escape(chat_url)}" '
            'style="display:inline-block;padding:10px 20px;background:#10A37F;'
            'color:#fff;text-decoration:none;border-radius:8px;font-weight:600;'
            'font-size:14px;">Open chat</a></p>'
        )
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
        '<hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0 16px 0;">'
        f'<p style="margin:0;font-size:12px;color:#888;">— XelaSphere Reminders &middot; Scheduled for {fire_at_iso_safe}</p>'
        '</div></body></html>'
    )


# ==================== Firestore queries ====================
def fetch_due_reminders(limit: int = 100):
    """Return up to `limit` Firestore document snapshots that are
    status=scheduled AND fireAt<=now."""
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


def _try_revert_stuck_lock(doc_ref, cutoff_iso: str, *, _fake_snap=None) -> bool:
    """Transactionally re-check + revert a single stuck-lock doc.

    Returns True iff this call won the revert race (status was still
    'sending' AND lockedAt was still <= cutoff_iso at the moment of
    the transactional read).

    `_fake_snap` is a testing seam — when supplied, the function does
    the same gate checks against the fake snap but writes via the
    plain doc_ref.update (no transaction). Production callers do not
    pass _fake_snap.
    """
    if _fake_snap is not None:
        if not _fake_snap.exists:
            return False
        data = _fake_snap.to_dict() or {}
        if data.get("status") != "sending":
            return False
        locked_at = data.get("lockedAt")
        if not locked_at or locked_at > cutoff_iso:
            return False
        doc_ref.update({
            "status": "scheduled",
            "lockedAt": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        return True

    from google.cloud import firestore as gc_firestore
    db = get_db()
    transaction = db.transaction()

    @gc_firestore.transactional
    def _txn(txn, ref, cutoff):
        snap = ref.get(transaction=txn)
        if not snap.exists:
            return False
        data = snap.to_dict() or {}
        if data.get("status") != "sending":
            return False
        locked_at = data.get("lockedAt")
        if not locked_at or locked_at > cutoff:
            return False
        txn.update(ref, {
            "status": "scheduled",
            "lockedAt": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        return True

    return _txn(transaction, doc_ref, cutoff_iso)


def sweep_stuck_locks(stale_after_seconds: int = STUCK_LOCK_STALE_AFTER_SECONDS) -> int:
    """Revert reminders stuck in status='sending' with lockedAt older than
    threshold back to 'scheduled'. Returns count of swept docs.

    Each revert is transactional — it re-reads the doc inside a Firestore
    transaction and only writes if status is still 'sending' and lockedAt
    is still stale. This prevents two concurrent sweep runs (possible after
    Persistent=true timer catch-up) from both re-arming the same doc, which
    would cause a double-send on the next normal tick.
    """
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
        if _try_revert_stuck_lock(snap.reference, cutoff):
            logger.warning(
                "lock_expired",
                extra={"extra_fields": {"reminderId": snap.id}},
            )
            count += 1
    return count


# ==================== Lock + process ====================
def try_claim_reminder(doc_ref, *, _now_iso: Optional[str] = None, _fake_snap=None) -> bool:
    """Atomically flip status scheduled→sending, set lockedAt. Returns True
    if this worker won the claim, False otherwise.

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


def process_reminder(doc_ref, reminder: dict, *, user_email: str) -> None:
    """Send the email and transition status."""
    from app.services.email_sender import (
        send_transactional_email, EmailSendError,
    )

    name = reminder.get("name", "Reminder")
    body_text = _build_email_body(reminder)
    body_html = _build_email_html(reminder)
    attempt = reminder.get("attemptCount", 0)
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        result = send_transactional_email(
            to_email=user_email,
            subject=f"[Task Update] Reminder: {name}",
            body_text=body_text,
            body_html=body_html,
        )
    except EmailSendError as exc:
        new_attempt = attempt + 1
        if exc.retryable and new_attempt < RETRY_CAP:
            doc_ref.update({
                "status": "scheduled",  # next tick retries
                "attemptCount": new_attempt,
                "lastError": str(exc),
                "lockedAt": None,
                "updatedAt": now_iso,
            })
            logger.warning(
                "reminder_retry",
                extra={"extra_fields": {
                    "name": name, "attempt": new_attempt, "cap": RETRY_CAP,
                    "error": str(exc),
                }},
            )
        else:
            doc_ref.update({
                "status": "failed",
                "attemptCount": new_attempt,
                "lastError": str(exc),
                "lockedAt": None,
                "updatedAt": now_iso,
            })
            logger.error(
                "reminder_failed",
                extra={"extra_fields": {
                    "name": name, "attempts": new_attempt, "error": str(exc),
                }},
            )
        return

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


def _get_user_email(user_id: str) -> str:
    """Read users/{uid}.email — caller is responsible for absent-doc handling."""
    if not user_id:
        return ""
    db = get_db()
    snap = db.collection("users").document(user_id).get()
    if not snap.exists:
        return ""
    return (snap.to_dict() or {}).get("email", "")


# ==================== Logging setup ====================
class _JsonFormatter(logging.Formatter):
    """One-line JSON log records. Extra fields go via `extra={'extra_fields': {...}}`."""
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return _json.dumps(payload)


def _setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


# ==================== Main loop ====================
def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging()

    from app.config import Config
    if not Config.REMINDERS_ENABLED:
        logger.info("worker_disabled_skip")
        return 0

    swept = sweep_stuck_locks()
    if swept:
        logger.info("swept_locks", extra={"extra_fields": {"count": swept}})

    docs = fetch_due_reminders(limit=args.limit)
    logger.info("fetched", extra={"extra_fields": {"count": len(docs)}})

    if args.dry_run:
        for d in docs:
            logger.info(
                "dry_run_would_send",
                extra={"extra_fields": {
                    "reminderId": d.id, "name": (d.to_dict() or {}).get("name"),
                }},
            )
        return 0

    for snap in docs:
        data = snap.to_dict() or {}
        doc_ref = snap.reference
        if not try_claim_reminder(doc_ref):
            logger.info("claim_lost", extra={"extra_fields": {"reminderId": snap.id}})
            continue

        user_id = data.get("userId")
        user_email = _get_user_email(user_id)
        if not user_email:
            doc_ref.update({
                "status": "failed",
                "attemptCount": data.get("attemptCount", 0) + 1,
                "lastError": "user has no email on file",
                "lockedAt": None,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            logger.error(
                "missing_user_email",
                extra={"extra_fields": {"reminderId": snap.id, "userId": user_id}},
            )
            continue

        # Re-read attemptCount fresh from snapshot (data is from pre-claim read)
        process_reminder(
            doc_ref,
            {**data, "status": "sending"},
            user_email=user_email,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

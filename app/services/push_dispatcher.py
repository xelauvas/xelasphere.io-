"""Web Push fan-out for reminder deliveries.

After the worker successfully sends the email, it calls
send_push_for_reminder(reminder, user_id) (added in Task 4) which
iterates the user's pushSubscriptions and sends an encrypted notification
to each.

This file contains pure helpers (no I/O) used by that dispatcher and
covered by unit tests.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import pywebpush
from app.config import Config, db
from app.services.signed_links import make_snooze_token

logger = logging.getLogger(__name__)

# Web Push spec caps encrypted payloads at ~4KB. We aim well under to
# leave room for the JSON envelope + encryption overhead.
PAYLOAD_BODY_LIMIT = 2048
PAYLOAD_BODY_RETRY_LIMIT = 256


def _truncate(text: Optional[str], limit: int) -> str:
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

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


# send_push_for_reminder is implemented in Task 4 (uses Firestore + pywebpush).

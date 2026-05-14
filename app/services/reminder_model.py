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

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
    """Convert a naive local ISO datetime + IANA tz into a UTC datetime.

    Raises ValueError on a DST-gap time (a clock value that doesn't exist
    in the given timezone, e.g. 01:30 London on the last Sunday in March).
    For DST-fold ambiguity (e.g. 01:30 London in October, which occurs
    twice) defaults to fold=0 (pre-transition / BST), matching CPython
    and most date libraries.
    """
    if not isinstance(fire_at_local_iso, str):
        raise ValueError(f"fireAtLocal must be a string, got {type(fire_at_local_iso).__name__}")
    if not isinstance(timezone_str, str):
        raise ValueError(f"timezone must be a string, got {type(timezone_str).__name__}")

    naive = datetime.fromisoformat(fire_at_local_iso)
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)

    tz = ZoneInfo(timezone_str)
    local_fold0 = naive.replace(tzinfo=tz, fold=0)
    local_fold1 = naive.replace(tzinfo=tz, fold=1)
    if local_fold0.utcoffset() != local_fold1.utcoffset():
        # Either DST gap (doesn't exist) or fold (ambiguous). Detect gap
        # by round-tripping through UTC: a gap time's UTC roundtrip
        # won't match the original local wall clock.
        roundtrip = local_fold0.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
        if roundtrip != naive:
            raise ValueError(
                f"{fire_at_local_iso} does not exist in {timezone_str} "
                f"(falls in a DST gap)"
            )
        # Ambiguous (fold) — fold=0 is the conventional default.
    return local_fold0.astimezone(timezone.utc)


def validate_reminder_payload(payload: dict) -> Tuple[bool, Optional[str]]:
    """Validate a create-reminder request payload. Returns (ok, error_or_None)."""
    raw_name = payload.get("name")
    if not isinstance(raw_name, str):
        if raw_name is None or raw_name == "":
            return False, "name is required"
        return False, "name must be a string"
    name = raw_name.strip()
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
    except (ValueError, TypeError, KeyError) as exc:
        return False, f"invalid fireAtLocal or timezone: {exc}"
    if fire_at_utc <= datetime.now(timezone.utc):
        return False, "fireAtLocal must be in the future"
    return True, None


def new_reminder_dict(
    *,
    reminder_id: str,
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

    `reminder_id` is required so the doc body contains the same id as the
    Firestore document path — callers like the worker and the chat hook
    write this dict directly without a second post-write step.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": reminder_id,
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

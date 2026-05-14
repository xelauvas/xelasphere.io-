"""Chat hook for the reminders M0 feature.

Called from app/blueprints/chat_routes.py at both stream and non-stream
hook points (where is_email_request lives). Encapsulates the full
"detect intent → extract → persist → return id" flow so chat_routes
itself only needs an import + a few-line call.

Always fail-open: any error returns (None, False) and is logged at
warning level. This module must never break the chat stream.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def maybe_create_reminder_from_prompt(
    *,
    user_prompt: Optional[str],
    user_id: Optional[str],
    user_email: Optional[str],
    user_timezone: Optional[str],
    source_chat_id: Optional[str] = None,
    source_message_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[dict], bool]:
    """Detect reminder intent, extract structured fields, persist if complete.

    Returns: (reminder_id_or_None, reminder_dict_or_None, needs_clarification)
      - (rem_id, reminder_dict, False): reminder created — caller has
                         everything needed to serialize into a done payload.
      - (None,  None, True):  intent detected but missing time.
      - (None,  None, False): not a reminder request OR feature disabled
                              OR extraction failed.

    Never raises.
    """
    try:
        from app.config import Config
        if not Config.REMINDERS_ENABLED:
            return None, None, False
        if not user_id or not user_email or not user_prompt:
            return None, None, False

        from app.services.reminder_intent import (
            is_reminder_request, extract_reminder, needs_clarification,
        )
        from app.services.reminder_model import (
            new_reminder_dict, compute_fire_at_utc,
        )
        from app.config import db

        if not is_reminder_request(user_prompt):
            return None, None, False

        extracted = extract_reminder(
            prompt=user_prompt,
            now_utc=datetime.now(timezone.utc),
            user_timezone=user_timezone or "UTC",
        )
        if needs_clarification(user_prompt, extracted):
            return None, None, True
        if not (extracted.get("name") and extracted.get("fireAtLocal") and extracted.get("timezone")):
            return None, None, False

        try:
            fire_at_utc = compute_fire_at_utc(extracted["fireAtLocal"], extracted["timezone"])
        except (ValueError, KeyError, TypeError):
            return None, None, False
        if fire_at_utc <= datetime.now(timezone.utc):
            return None, None, False

        doc_ref = (
            db.collection("users").document(user_id)
            .collection("reminders").document()
        )
        reminder = new_reminder_dict(
            reminder_id=doc_ref.id,
            user_id=user_id,
            name=extracted["name"].strip()[:200],
            instructions=(extracted.get("instructions") or "").strip()[:1000],
            fire_at_utc=fire_at_utc,
            timezone_str=extracted["timezone"],
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            created_by="ai",
        )
        doc_ref.set(reminder)

        # Cache the user's default timezone so future reminders pre-fill correctly.
        try:
            db.collection("users").document(user_id).set(
                {"defaultTimezone": extracted["timezone"]}, merge=True,
            )
        except Exception as exc:
            logger.warning(f"[reminders/hook] failed to cache defaultTimezone: {exc}")

        logger.info(
            f"[reminders/hook] AI-created {doc_ref.id} for user {user_id} "
            f"from chat {source_chat_id}, fire_at={reminder['fireAt']}"
        )
        return doc_ref.id, reminder, False

    except Exception as exc:
        logger.warning(f"[reminders/hook] non-fatal error: {exc}")
        return None, None, False


# ==================== System-prompt nudge text ====================
CLARIFICATION_SYSTEM_HINT = (
    "The user wants to set a reminder but did not specify a time. "
    "Ask them naturally for the missing detail (when, time, or date). "
    "Do not create the reminder yourself; the system will retry on their next turn."
)

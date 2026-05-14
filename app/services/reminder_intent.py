"""Intent detection + LLM extraction for reminder requests.

Mirrors the regex+downstream-call pattern of is_email_request /
parse_email_from_response in app/blueprints/email_routes.py.

M0 is English-only. Multilingual support is a follow-up: extend
_REMINDER_PATTERNS with translations of "remind", "reminder", etc.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ==================== Regex pre-filter ====================
_REMINDER_PATTERNS = [
    re.compile(r"\b(?:please\s+)?remind\s+(?:me|us|him|her|them|my\s+\w+)\b", re.IGNORECASE),
    re.compile(r"\b(?:set|leave|create|schedule|add)\s+(?:a\s+|an\s+)?reminder\b", re.IGNORECASE),
    re.compile(r"\bnudge\s+me\b", re.IGNORECASE),
]


def is_reminder_request(user_prompt: Optional[str]) -> bool:
    """Cheap regex pre-filter: does the user *plausibly* want a reminder?

    False positives are fine — extract_reminder() is the real gatekeeper.
    False negatives skip the feature for that turn, which is acceptable.
    """
    if not user_prompt or len(user_prompt) < 5:
        return False
    return any(p.search(user_prompt) for p in _REMINDER_PATTERNS)


# ==================== LLM extraction ====================
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
    import requests
    from app.config import Config

    user_msg = (
        f"Current UTC time: {now_utc.isoformat()}\n"
        f"User's timezone: {user_timezone}\n"
        f"User's message: {prompt}\n"
    )
    try:
        resp = requests.post(
            Config.API_URL,  # Groq endpoint
            headers={
                "Authorization": f"Bearer {Config.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 300,
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        return body["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning(f"[reminders/extract] LLM call failed: {exc}")
        return ""


def extract_reminder(
    prompt: str, *, now_utc: datetime, user_timezone: str
) -> dict:
    """Extract reminder fields from prompt. Always returns a dict with
    4 keys (name/instructions/fireAtLocal/timezone), any of which may be None.

    Never raises — garbage LLM output yields all-None.
    """
    # null_result always backfills timezone with the user's tz so callers
    # have a consistent contract: timezone is never None in the returned dict.
    null_result = {
        "name": None, "instructions": None, "fireAtLocal": None,
        "timezone": user_timezone,
    }
    raw = _llm_extract_json(prompt, now_utc, user_timezone)
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


# ==================== Clarification helper ====================
def needs_clarification(prompt: str, extracted: dict) -> bool:
    """Returns True when intent matched and the LLM understood the *what*
    (got a name) but not the *when* (fireAtLocal is null)."""
    if not extracted:
        return False
    return (
        is_reminder_request(prompt)
        and extracted.get("name") is not None
        and extracted.get("fireAtLocal") is None
    )

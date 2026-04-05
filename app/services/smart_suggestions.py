"""
Smart Suggestions Service

Manages a pool of AI-generated personalized suggestions for users.
The pool is stored in Firestore and refreshed as needed.
"""

import json
import logging
import random
import re
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from app.config import Config
from app.grog_api import TaskOption

logger = logging.getLogger(__name__)

# Pool configuration constants
POOL_SIZE = 10
DISPLAY_SIZE = 3
SLOT_TYPES = ("continue", "today", "discover")


@dataclass
class Suggestion:
    """
    A single smart suggestion for a user.

    The `reasoning` field captures why this suggestion was generated.
    It is intentionally excluded from `to_dict()` to avoid exposing
    internal AI reasoning to the client.
    """
    title: str
    subtitle: str
    prompt: str
    slot_type: str
    category: str
    icon: str
    reasoning: str
    shown_count: int = 0
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize the suggestion for Firestore storage or API responses.

        Note: `reasoning` is intentionally excluded here — it must never
        be sent to the client.
        """
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "prompt": self.prompt,
            "slot_type": self.slot_type,
            "category": self.category,
            "icon": self.icon,
            "shown_count": self.shown_count,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
        }

    def to_task_option(self) -> TaskOption:
        """Convert this suggestion to the existing TaskOption format."""
        return TaskOption(
            title=self.title,
            subtitle=self.subtitle,
            action=f"sendMessage('{self.prompt.replace(chr(39), chr(92) + chr(39))}')",
            icon=self.icon,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Suggestion":
        """Deserialize a Suggestion from a Firestore document dict."""
        created_at_raw = data.get("created_at")
        if isinstance(created_at_raw, str):
            try:
                created_at = datetime.fromisoformat(created_at_raw)
            except ValueError:
                created_at = datetime.now(timezone.utc)
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            created_at = datetime.now(timezone.utc)

        return cls(
            title=data.get("title", ""),
            subtitle=data.get("subtitle", ""),
            prompt=data.get("prompt", ""),
            slot_type=data.get("slot_type", ""),
            category=data.get("category", ""),
            icon=data.get("icon", "star"),
            reasoning=data.get("reasoning", ""),
            shown_count=data.get("shown_count", 0),
            created_at=created_at,
        )


def save_pool(db, user_id: str, suggestions: List[Suggestion], reason: str) -> None:
    """
    Persist the suggestion pool to Firestore.

    Writes to: users/{user_id}/smart_suggestions/pool
    """
    pool_data = {
        "suggestions": [s.to_dict() for s in suggestions],
        "last_refresh": datetime.now(timezone.utc).isoformat(),
        "refresh_reason": reason,
        "pool_size": len(suggestions),
    }
    (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("pool")
        .set(pool_data)
    )


def load_pool(db, user_id: str) -> List[Suggestion]:
    """
    Load the suggestion pool from Firestore.

    Returns an empty list if no pool document exists yet.
    """
    snapshot = (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("pool")
        .get()
    )
    if not snapshot.exists:
        return []

    data = snapshot.to_dict() or {}
    raw_suggestions = data.get("suggestions", [])
    return [Suggestion.from_dict(s) for s in raw_suggestions]


def get_pool_metadata(db, user_id: str) -> Dict[str, Any]:
    """
    Return metadata about the current suggestion pool.

    Returns a dict with last_refresh, refresh_reason, and pool_size.
    Returns defaults if the pool document does not exist.
    """
    snapshot = (
        db.collection("users")
        .document(user_id)
        .collection("smart_suggestions")
        .document("pool")
        .get()
    )
    if not snapshot.exists:
        return {
            "last_refresh": None,
            "refresh_reason": None,
            "pool_size": 0,
        }

    data = snapshot.to_dict() or {}
    return {
        "last_refresh": data.get("last_refresh"),
        "refresh_reason": data.get("refresh_reason"),
        "pool_size": data.get("pool_size", 0),
    }


# Time-of-day keyword ranges (inclusive on both ends, except night which wraps).
_TIME_KEYWORDS: Dict[str, tuple] = {
    "morning": (5, 11),
    "afternoon": (12, 16),
    "evening": (17, 21),
    "night": (22, 4),  # wraps midnight
}


def _score_today_slot(suggestion: Suggestion, hour: int) -> int:
    """
    Score a today-slot suggestion based on time-of-day keywords in its
    title and subtitle.

    Returns 1 if a keyword matches the given hour, 0 otherwise.
    Multiple keyword matches still return 1 (presence, not frequency).
    """
    text = (suggestion.title + " " + suggestion.subtitle).lower()
    for keyword, (start, end) in _TIME_KEYWORDS.items():
        if keyword not in text:
            continue
        if start <= end:
            if start <= hour <= end:
                return 1
        else:
            # Wraps midnight (e.g. night: 22-4)
            if hour >= start or hour <= end:
                return 1
    return 0


def select_display_slots(
    pool: List[Suggestion],
    hour: Optional[int] = None,
) -> List[Suggestion]:
    """
    Pick up to 3 suggestions from the pool: 1 continue + 1 today + 1 discover.

    Selection rules:
    - continue: lowest shown_count, random tiebreaker
    - today: highest _score_today_slot(), random tiebreaker among top scorers
    - discover: lowest shown_count, random tiebreaker
    - If a slot type has no candidates in the pool it is skipped.

    Returns fewer than 3 items when slot types are missing from the pool.
    """
    if not pool:
        return []

    if hour is None:
        hour = datetime.now(timezone.utc).hour

    # Group suggestions by slot_type
    by_slot: Dict[str, List[Suggestion]] = {}
    for s in pool:
        by_slot.setdefault(s.slot_type, []).append(s)

    selected: List[Suggestion] = []

    # --- continue slot ---
    if "continue" in by_slot:
        candidates = by_slot["continue"]
        min_count = min(s.shown_count for s in candidates)
        tied = [s for s in candidates if s.shown_count == min_count]
        selected.append(random.choice(tied))

    # --- today slot ---
    if "today" in by_slot:
        candidates = by_slot["today"]
        scores = [(s, _score_today_slot(s, hour)) for s in candidates]
        max_score = max(sc for _, sc in scores)
        top = [s for s, sc in scores if sc == max_score]
        selected.append(random.choice(top))

    # --- discover slot ---
    if "discover" in by_slot:
        candidates = by_slot["discover"]
        min_count = min(s.shown_count for s in candidates)
        tied = [s for s in candidates if s.shown_count == min_count]
        selected.append(random.choice(tied))

    return selected


# ---------------------------------------------------------------------------
# LLM Pool Generation
# ---------------------------------------------------------------------------

_GENERATION_SYSTEM_PROMPT = (
    "You are a personalization engine for an AI assistant app. "
    "Generate exactly {pool_size} smart suggestions tailored to the user based on the context provided.\n\n"
    "Return ONLY a valid JSON array with no additional text. Each item must have these fields:\n"
    "- title: short action phrase (max 6 words)\n"
    "- subtitle: one-line description (max 10 words)\n"
    "- prompt: the full message to send to the AI on behalf of the user\n"
    "- slot_type: one of 'continue', 'today', or 'discover'\n"
    "- category: a short category label (e.g. 'work', 'learning', 'health', 'creative')\n"
    "- icon: a single Material Icons name (e.g. 'star', 'bolt', 'book', 'fitness_center')\n"
    "- reasoning: brief internal note explaining why this suggestion fits the user (not shown to user)\n\n"
    "Rules:\n"
    "- Do NOT suggest titles in the exclusion list.\n"
    "- Distribute slot_types: include both 'continue', 'today', and 'discover' entries.\n"
    "- Make prompts specific and actionable, not generic.\n"
    "- Tailor suggestions to the user's memories, recent topics, and time of day."
)

_GENERATION_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def build_generation_prompt(context: Dict[str, Any]) -> str:
    """
    Build a user prompt for LLM suggestion generation.

    Args:
        context: dict with keys:
            memories (list of dicts with type/key/value),
            recent_topics (list of strings),
            suggestion_profile (dict with top_categories, avoided_categories),
            hour (int),
            day_of_week (str),
            exclusion_titles (list of strings)

    Returns:
        A single prompt string.
    """
    memories = (context.get("memories") or [])[:20]
    recent_topics = (context.get("recent_topics") or [])[:5]
    profile = context.get("suggestion_profile") or {}
    hour = context.get("hour", 12)
    day_of_week = context.get("day_of_week", "")
    exclusion_titles = (context.get("exclusion_titles") or [])[:20]

    lines = []

    # Memories section
    lines.append("## User Memories")
    if memories:
        for m in memories:
            lines.append(f"- [{m.get('type', '')}] {m.get('key', '')}: {m.get('value', '')}")
    else:
        lines.append("(none)")

    # Recent topics section
    lines.append("\n## Recent Topics")
    if recent_topics:
        for t in recent_topics:
            lines.append(f"- {t}")
    else:
        lines.append("(none)")

    # Suggestion profile section
    lines.append("\n## Suggestion Profile")
    top_cats = profile.get("top_categories") or []
    avoided_cats = profile.get("avoided_categories") or []
    if top_cats:
        lines.append(f"Preferred categories: {', '.join(top_cats)}")
    if avoided_cats:
        lines.append(f"Avoid these categories: {', '.join(avoided_cats)}")
    if not top_cats and not avoided_cats:
        lines.append("(no profile data yet)")

    # Current context section
    lines.append("\n## Current Context")
    lines.append(f"Hour of day (24h): {hour}")
    if day_of_week:
        lines.append(f"Day of week: {day_of_week}")

    # Exclusions section
    lines.append("\n## Do NOT Suggest")
    if exclusion_titles:
        for title in exclusion_titles:
            lines.append(f"- {title}")
    else:
        lines.append("(no exclusions)")

    return "\n".join(lines)


def parse_llm_suggestions(raw: Optional[str]) -> List[Suggestion]:
    """
    Parse the LLM response JSON into a list of Suggestion objects.

    Handles:
    - Plain JSON array
    - Markdown code-block wrapped (```json ... ```)
    - Malformed JSON → returns []
    - Empty or None input → returns []
    - Items missing title or prompt are skipped

    Returns:
        List of Suggestion objects (may be empty).
    """
    if not raw:
        return []

    text = raw.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.debug("parse_llm_suggestions: JSON decode failed")
        return []

    if not isinstance(data, list):
        logger.debug("parse_llm_suggestions: expected JSON array, got %s", type(data).__name__)
        return []

    suggestions = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("title") or not item.get("prompt"):
            continue
        try:
            suggestions.append(Suggestion.from_dict(item))
        except Exception as exc:
            logger.debug("parse_llm_suggestions: skipping item due to error: %s", exc)

    return suggestions


def generate_pool_from_llm(context: Dict[str, Any]) -> List[Suggestion]:
    """
    Call the Groq LLM API to generate a suggestion pool.

    Tries models in order: llama-3.3-70b-versatile, llama-3.1-8b-instant.
    Returns a list of parsed Suggestion objects, or [] on any failure.
    """
    api_key = Config.GROQ_API_KEY
    if not api_key:
        logger.warning("generate_pool_from_llm: GROQ_API_KEY not configured")
        return []

    system_prompt = _GENERATION_SYSTEM_PROMPT.replace("{pool_size}", str(POOL_SIZE))
    user_prompt = build_generation_prompt(context)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for model in _GENERATION_MODELS:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 2000,
            "temperature": 0.7,
        }
        try:
            response = requests.post(
                Config.API_URL,
                json=payload,
                headers=headers,
                timeout=20,
            )
            if response.status_code != 200:
                logger.warning(
                    "generate_pool_from_llm: model %s returned status %d",
                    model,
                    response.status_code,
                )
                continue

            raw = response.json()["choices"][0]["message"]["content"]
            suggestions = parse_llm_suggestions(raw)
            if suggestions:
                logger.info(
                    "generate_pool_from_llm: got %d suggestions from model %s",
                    len(suggestions),
                    model,
                )
                return suggestions

            logger.warning("generate_pool_from_llm: model %s returned empty parse, trying next", model)

        except Exception as exc:
            logger.warning("generate_pool_from_llm: model %s failed: %s", model, exc)

    return []

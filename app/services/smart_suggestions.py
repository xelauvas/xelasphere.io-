"""
Smart Suggestions Service

Manages a pool of AI-generated personalized suggestions for users.
The pool is stored in Firestore and refreshed as needed.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from app.grog_api import TaskOption

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

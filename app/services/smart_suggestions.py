"""
Smart Suggestions Service

Manages a pool of AI-generated personalized suggestions for users.
The pool is stored in Firestore and refreshed as needed.
"""

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

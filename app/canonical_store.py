"""
Canonical Artifacts Store - Truth Over Chat
=============================================
Stores authoritative/final objects separately from messy conversation.

Problem:
    Chat has iterations: "try this code" → "no wait, use this" → "actually..."
    AI sees all versions and gets confused about what's current.

Solution:
    Store canonical (authoritative) artifacts separately:
    - current_code: The final/working code snippet
    - config: Environment/settings decided on
    - endpoints: API endpoints being used
    - solution: The accepted approach
    - todos: Open action items

    Inject these BEFORE chat context so AI sees truth first.

Usage:
    from app.canonical_store import CanonicalStore

    store = CanonicalStore(user_id, session_id)

    # Set canonical items
    store.set("current_code", "def hello(): return 'world'")
    store.set("solution", "Using Redis for caching instead of memcached")
    store.add_todo("Implement rate limiting")

    # Get formatted for prompt injection
    context = store.get_prompt_context()
    # Returns:
    # CANONICAL STATE:
    # [Code] def hello(): return 'world'
    # [Solution] Using Redis for caching instead of memcached
    # [TODOs] - Implement rate limiting
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# =============================================================================
# Canonical Item Types
# =============================================================================

CANONICAL_TYPES = {
    "current_code": {
        "label": "Code",
        "description": "Current working code snippet",
        "max_length": 3000,
    },
    "config": {
        "label": "Config",
        "description": "Environment/configuration settings",
        "max_length": 1000,
    },
    "endpoints": {
        "label": "Endpoints",
        "description": "API endpoints being used",
        "max_length": 500,
    },
    "solution": {
        "label": "Solution",
        "description": "Accepted approach/solution",
        "max_length": 1000,
    },
    "tech_stack": {
        "label": "Stack",
        "description": "Technology stack being used",
        "max_length": 500,
    },
    "constraints": {
        "label": "Constraints",
        "description": "Hard requirements/limitations",
        "max_length": 500,
    },
    "todos": {
        "label": "TODOs",
        "description": "Open action items",
        "max_length": 1000,
        "is_list": True,
    },
}


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class CanonicalItem:
    """Single canonical artifact."""
    key: str
    value: str
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source: str = ""  # "user", "ai", "reflection"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CanonicalItem":
        return cls(
            key=data.get("key", ""),
            value=data.get("value", ""),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
            source=data.get("source", ""),
        )


# =============================================================================
# Canonical Store
# =============================================================================

class CanonicalStore:
    """
    Store for canonical (authoritative) artifacts per session.

    These are the "truth" values that should be injected before
    conversation context to prevent AI confusion.
    """

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id
        self._items: Dict[str, CanonicalItem] = {}
        self._todos: List[str] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy load from Firestore."""
        if self._loaded:
            return

        try:
            from .firebase_config import db
            if not db:
                self._loaded = True
                return

            doc_ref = (
                db.collection("users")
                .document(self.user_id)
                .collection("chat_sessions")
                .document(self.session_id)
            )
            doc = doc_ref.get()

            if doc.exists:
                data = doc.to_dict()
                canonical_data = data.get("canonical", {})

                # Load items
                for key, item_data in canonical_data.get("items", {}).items():
                    if isinstance(item_data, dict):
                        self._items[key] = CanonicalItem.from_dict(item_data)
                    elif isinstance(item_data, str):
                        self._items[key] = CanonicalItem(key=key, value=item_data)

                # Load TODOs
                self._todos = canonical_data.get("todos", [])

            self._loaded = True

        except Exception as e:
            logger.warning(f"Failed to load canonical store: {e}")
            self._loaded = True

    def _save(self) -> None:
        """Persist to Firestore."""
        try:
            from .firebase_config import db
            if not db:
                return

            doc_ref = (
                db.collection("users")
                .document(self.user_id)
                .collection("chat_sessions")
                .document(self.session_id)
            )

            canonical_data = {
                "items": {k: v.to_dict() for k, v in self._items.items()},
                "todos": self._todos,
                "updated_at": datetime.utcnow().isoformat(),
            }

            doc_ref.set({"canonical": canonical_data}, merge=True)

        except Exception as e:
            logger.warning(f"Failed to save canonical store: {e}")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def set(self, key: str, value: str, source: str = "user") -> bool:
        """
        Set a canonical item.

        Args:
            key: Item type (current_code, config, solution, etc.)
            value: The authoritative value
            source: Who set it (user, ai, reflection)

        Returns:
            True if set successfully
        """
        self._ensure_loaded()

        if key not in CANONICAL_TYPES:
            logger.warning(f"Unknown canonical type: {key}")
            return False

        type_info = CANONICAL_TYPES[key]
        max_len = type_info.get("max_length", 1000)

        # Truncate if too long
        if len(value) > max_len:
            value = value[:max_len] + "... [truncated]"

        self._items[key] = CanonicalItem(
            key=key,
            value=value,
            source=source,
        )

        self._save()
        logger.debug(f"Set canonical {key} ({len(value)} chars)")
        return True

    def get(self, key: str) -> Optional[str]:
        """Get a canonical item value."""
        self._ensure_loaded()
        item = self._items.get(key)
        return item.value if item else None

    def remove(self, key: str) -> bool:
        """Remove a canonical item."""
        self._ensure_loaded()
        if key in self._items:
            del self._items[key]
            self._save()
            return True
        return False

    def add_todo(self, todo: str) -> None:
        """Add an open TODO item."""
        self._ensure_loaded()
        todo = todo.strip()
        if todo and todo not in self._todos:
            self._todos.append(todo)
            self._save()

    def remove_todo(self, todo: str) -> bool:
        """Remove a TODO item (mark as done)."""
        self._ensure_loaded()
        todo = todo.strip()
        if todo in self._todos:
            self._todos.remove(todo)
            self._save()
            return True
        return False

    def clear_todos(self) -> None:
        """Clear all TODOs."""
        self._ensure_loaded()
        self._todos = []
        self._save()

    def get_todos(self) -> List[str]:
        """Get all TODO items."""
        self._ensure_loaded()
        return list(self._todos)

    def get_all(self) -> Dict[str, Any]:
        """Get all canonical items."""
        self._ensure_loaded()
        return {
            "items": {k: v.to_dict() for k, v in self._items.items()},
            "todos": self._todos,
        }

    def has_content(self) -> bool:
        """Check if store has any content."""
        self._ensure_loaded()
        return bool(self._items or self._todos)

    # -------------------------------------------------------------------------
    # Prompt Integration
    # -------------------------------------------------------------------------

    def get_prompt_context(self, max_tokens: int = 800) -> str:
        """
        Format canonical items for prompt injection.

        Returns formatted string to inject BEFORE conversation context.
        This ensures AI sees authoritative truth first.
        """
        self._ensure_loaded()

        if not self._items and not self._todos:
            return ""

        parts = ["CANONICAL STATE (authoritative, overrides chat history):"]

        # Add items in priority order
        priority_order = ["solution", "current_code", "tech_stack", "config", "endpoints", "constraints"]

        for key in priority_order:
            if key in self._items:
                item = self._items[key]
                label = CANONICAL_TYPES[key]["label"]
                value = item.value

                # Format code blocks nicely
                if key == "current_code" and "\n" in value:
                    parts.append(f"[{label}]\n```\n{value}\n```")
                else:
                    parts.append(f"[{label}] {value}")

        # Add any items not in priority order
        for key, item in self._items.items():
            if key not in priority_order:
                label = CANONICAL_TYPES.get(key, {}).get("label", key)
                parts.append(f"[{label}] {item.value}")

        # Add TODOs
        if self._todos:
            parts.append("[TODOs]")
            for todo in self._todos[:10]:  # Max 10 TODOs
                parts.append(f"  - {todo}")

        result = "\n".join(parts) + "\n"

        # Truncate if too long (rough estimate: 4 chars per token)
        max_chars = max_tokens * 4
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... [truncated]\n"

        return result


# =============================================================================
# Convenience Functions
# =============================================================================

_store_cache: Dict[str, CanonicalStore] = {}


def get_canonical_store(user_id: str, session_id: str) -> CanonicalStore:
    """Get or create a canonical store for a session."""
    cache_key = f"{user_id}:{session_id}"
    if cache_key not in _store_cache:
        _store_cache[cache_key] = CanonicalStore(user_id, session_id)
    return _store_cache[cache_key]


def clear_store_cache(user_id: str = None, session_id: str = None) -> None:
    """Clear cached stores."""
    global _store_cache
    if user_id and session_id:
        cache_key = f"{user_id}:{session_id}"
        if cache_key in _store_cache:
            del _store_cache[cache_key]
    else:
        _store_cache = {}


def extract_canonical_from_reflection(
    reflection_result: Dict[str, Any],
    store: CanonicalStore
) -> None:
    """
    Extract canonical items from reflection results.

    Called after reflection step to auto-populate canonical store.
    """
    # Extract decisions as solution
    decisions = reflection_result.get("decisions", [])
    if decisions:
        current_solution = store.get("solution") or ""
        new_decisions = [d for d in decisions if d not in current_solution]
        if new_decisions:
            combined = current_solution + ("\n" if current_solution else "") + "\n".join(new_decisions)
            store.set("solution", combined.strip(), source="reflection")

    # Extract next steps as TODOs
    next_steps = reflection_result.get("next_steps", [])
    for step in next_steps:
        store.add_todo(step)

    # Extract constraints
    constraints = reflection_result.get("constraints", [])
    if constraints:
        current = store.get("constraints") or ""
        new_constraints = [c for c in constraints if c not in current]
        if new_constraints:
            combined = current + ("\n" if current else "") + "\n".join(new_constraints)
            store.set("constraints", combined.strip(), source="reflection")

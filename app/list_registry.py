"""
List Registry & Pointer Resolver
=================================
Server-side truth for numbered lists. When AI outputs a list,
store it. When user references an item, resolve deterministically.

Problem:
    User: "tell me about #10"
    AI sees "10" but doesn't know what list it refers to

Solution:
    1. Extract and store lists from AI responses
    2. Detect pointer references in user input
    3. Resolve to actual item text
    4. Rewrite prompt: "tell me about #10" → "Expand item #10: The Bible and Law"

Usage:
    from app.list_registry import ListRegistry, PointerResolver

    # Store list from AI response
    registry = ListRegistry(user_id, session_id)
    registry.extract_and_store(ai_response)

    # Resolve pointer in user input
    resolver = PointerResolver(registry)
    rewritten = resolver.resolve_and_rewrite("tell me about the second one")
    # Returns: "tell me about item #2: [actual item text]"
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Pointer Detection Patterns
# =============================================================================

# Cardinal numbers
CARDINAL_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

# Ordinal numbers
ORDINAL_MAP = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "last": -1,  # Special: last item
}

# Pointer detection patterns
POINTER_PATTERNS = [
    # Direct numbers: "10", "#10", "number 10"
    (r'\b#?(\d{1,2})\b', 'digit'),
    (r'\bnumber\s+(\d{1,2})\b', 'digit'),
    (r'\bitem\s+#?(\d{1,2})\b', 'digit'),
    (r'\boption\s+#?(\d{1,2})\b', 'digit'),

    # Key point references: "key point 4", "point 3", "step 2"
    (r'\b(?:key\s+)?point\s+#?(\d{1,2})\b', 'digit'),
    (r'\bstep\s+#?(\d{1,2})\b', 'digit'),
    (r'\breason\s+#?(\d{1,2})\b', 'digit'),
    (r'\bsection\s+#?(\d{1,2})\b', 'digit'),

    # Ordinals: "the first one", "second option", "tenth item"
    (r'\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|'
     r'eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|'
     r'eighteenth|nineteenth|twentieth|last)\s*(?:one|option|item|choice|thing|point|step)?\b', 'ordinal'),

    # Cardinals with context: "about 4", "in 5", "on number 3"
    (r'\b(?:about|expand|explain|more\s+on|tell\s+me\s+about|in|on)\s+#?(\d{1,2})\b', 'digit'),

    # Word cardinals: "number one", "option two"
    (r'\b(?:number|option|item|point|step)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b', 'cardinal'),
]


# =============================================================================
# List Extraction Patterns
# =============================================================================

# Pattern for numbered list items in AI response
LIST_ITEM_PATTERNS = [
    # Standard numbered: "1. Item text" or "1) Item text"
    r'^(\d{1,2})[.)]\s+(.+)$',
    # Bullet with number: "• 1. Item"
    r'^[•\-\*]\s*(\d{1,2})[.)]\s+(.+)$',
]


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class StoredList:
    """A stored numbered list."""
    id: str
    items: Dict[int, str]  # number -> text
    source_message_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    context: str = ""  # Brief context about what the list is

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "items": {str(k): v for k, v in self.items.items()},
            "source_message_id": self.source_message_id,
            "created_at": self.created_at,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoredList":
        items = {}
        for k, v in data.get("items", {}).items():
            try:
                items[int(k)] = v
            except (ValueError, TypeError):
                pass
        return cls(
            id=data.get("id", ""),
            items=items,
            source_message_id=data.get("source_message_id", ""),
            created_at=data.get("created_at", ""),
            context=data.get("context", ""),
        )

    def get_item(self, number: int) -> Optional[str]:
        """Get item by number. Handles 'last' (-1)."""
        if number == -1 and self.items:
            return self.items.get(max(self.items.keys()))
        return self.items.get(number)

    def get_max_number(self) -> int:
        """Get the highest item number."""
        return max(self.items.keys()) if self.items else 0


@dataclass
class PointerMatch:
    """A detected pointer reference."""
    original_text: str  # The matched text (e.g., "the second one")
    number: int  # Resolved number (e.g., 2)
    start: int  # Start position in input
    end: int  # End position in input


# =============================================================================
# List Registry
# =============================================================================

class ListRegistry:
    """
    Stores and retrieves numbered lists for a session.
    Enables deterministic pointer resolution.
    """

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id
        self._last_list: Optional[StoredList] = None
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
                list_data = data.get("last_list")
                if list_data and isinstance(list_data, dict):
                    self._last_list = StoredList.from_dict(list_data)

            self._loaded = True

        except Exception as e:
            logger.debug(f"Failed to load list registry: {e}")
            self._loaded = True

    def _save(self) -> None:
        """Persist to Firestore."""
        try:
            from .firebase_config import db
            if not db or not self._last_list:
                return

            doc_ref = (
                db.collection("users")
                .document(self.user_id)
                .collection("chat_sessions")
                .document(self.session_id)
            )

            doc_ref.set({
                "last_list": self._last_list.to_dict()
            }, merge=True)

        except Exception as e:
            logger.debug(f"Failed to save list registry: {e}")

    def extract_and_store(
        self,
        ai_response: str,
        message_id: str = "",
        min_items: int = 3
    ) -> Optional[StoredList]:
        """
        Extract numbered list from AI response and store it.

        Args:
            ai_response: The AI's response text
            message_id: Optional message ID for reference
            min_items: Minimum items to consider it a list (default 3)

        Returns:
            StoredList if extracted, None otherwise
        """
        items = self._extract_list_items(ai_response)

        if len(items) < min_items:
            return None

        # Create and store the list
        stored = StoredList(
            id=f"list_{uuid.uuid4().hex[:12]}",
            items=items,
            source_message_id=message_id,
            context=self._extract_context(ai_response),
        )

        self._last_list = stored
        self._loaded = True
        self._save()

        logger.debug(f"Stored list with {len(items)} items: {list(items.keys())}")
        return stored

    def _extract_list_items(self, text: str) -> Dict[int, str]:
        """Extract numbered items from text."""
        items = {}
        lines = text.split('\n')

        # First pass: look for numbered items (1. 2. 3.)
        for line in lines:
            line = line.strip()
            if not line:
                continue

            for pattern in LIST_ITEM_PATTERNS:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    try:
                        number = int(match.group(1))
                        item_text = match.group(2).strip()
                        # Clean up item text (remove trailing punctuation lists)
                        item_text = re.sub(r'\s*[:\-–]\s*$', '', item_text)
                        if item_text and len(item_text) > 2:
                            items[number] = item_text
                    except (ValueError, IndexError):
                        pass
                    break

        # If no numbered items found, try header-style items
        # e.g., "Location:", "Spiritual State:", "Call to Repentance:"
        if not items:
            header_items = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Match "Header:" or "**Header**:" or '"Header":' patterns
                header_match = re.match(
                    r'^(?:\*\*)?(?:")?([A-Z][^:*\n"]{2,40})(?:")?(?:\*\*)?[:\s]',
                    line
                )
                if header_match:
                    header_text = header_match.group(1).strip()
                    # Skip generic headers like "Key Points About..."
                    if not any(skip in header_text.lower() for skip in ['key points', 'summary', 'overview', 'introduction']):
                        header_items.append(header_text)

            # Assign sequential numbers to headers
            for idx, header in enumerate(header_items, 1):
                items[idx] = header

        return items

    def _extract_context(self, text: str) -> str:
        """Extract brief context about what the list is."""
        # Look for introductory text before the list
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if line and not re.match(r'^\d+[.)]\s', line):
                # This might be intro text
                if len(line) > 10 and len(line) < 200:
                    return line[:100]
        return ""

    def get_last_list(self) -> Optional[StoredList]:
        """Get the most recent stored list."""
        self._ensure_loaded()
        return self._last_list

    def get_item(self, number: int) -> Optional[str]:
        """Get item text by number from the last list."""
        self._ensure_loaded()
        if self._last_list:
            return self._last_list.get_item(number)
        return None

    def has_list(self) -> bool:
        """Check if there's a stored list."""
        self._ensure_loaded()
        return self._last_list is not None and bool(self._last_list.items)

    def clear(self) -> None:
        """Clear the stored list."""
        self._last_list = None
        self._save()


# =============================================================================
# Pointer Resolver
# =============================================================================

class PointerResolver:
    """
    Detects and resolves pointer references in user input.

    Steps:
        1. Detect references: "10", "#10", "the second one", "last one"
        2. Resolve against stored list
        3. Rewrite prompt to be explicit
    """

    def __init__(self, registry: ListRegistry):
        self.registry = registry

    def detect_pointers(self, text: str) -> List[PointerMatch]:
        """
        Detect pointer references in text.

        Returns list of PointerMatch objects.
        """
        matches = []
        text_lower = text.lower()

        for pattern, pattern_type in POINTER_PATTERNS:
            for match in re.finditer(pattern, text_lower):
                try:
                    if pattern_type == 'digit':
                        number = int(match.group(1))
                    elif pattern_type == 'ordinal':
                        word = match.group(1).lower()
                        number = ORDINAL_MAP.get(word)
                    elif pattern_type == 'cardinal':
                        word = match.group(1).lower()
                        number = CARDINAL_MAP.get(word)
                    else:
                        continue

                    if number is not None:
                        matches.append(PointerMatch(
                            original_text=match.group(0),
                            number=number,
                            start=match.start(),
                            end=match.end(),
                        ))
                except (ValueError, IndexError):
                    pass

        # Deduplicate overlapping matches (keep longest)
        matches = self._deduplicate_matches(matches)

        return matches

    def _deduplicate_matches(self, matches: List[PointerMatch]) -> List[PointerMatch]:
        """Remove overlapping matches, keeping the longest."""
        if not matches:
            return []

        # Sort by start position, then by length (descending)
        sorted_matches = sorted(matches, key=lambda m: (m.start, -(m.end - m.start)))

        result = []
        last_end = -1

        for match in sorted_matches:
            if match.start >= last_end:
                result.append(match)
                last_end = match.end

        return result

    def resolve(self, text: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Resolve pointer in text against stored list.

        Returns:
            (number, item_text, original_match_text) or (None, None, None)
        """
        matches = self.detect_pointers(text)

        if not matches:
            return None, None, None

        # Use the first match
        match = matches[0]
        item_text = self.registry.get_item(match.number)

        if item_text:
            return match.number, item_text, match.original_text

        return match.number, None, match.original_text

    def resolve_and_rewrite(self, user_input: str) -> Tuple[str, bool, Optional[Dict[str, Any]]]:
        """
        Resolve pointers and rewrite user input to be explicit.

        Args:
            user_input: Original user message

        Returns:
            (rewritten_text, was_rewritten, resolution_info)
        """
        number, item_text, original_match = self.resolve(user_input)

        if number is None:
            # No pointer detected
            return user_input, False, None

        if item_text is None:
            # Pointer detected but no list to resolve against
            # Return info for clarification prompt
            return user_input, False, {
                "pointer_detected": True,
                "number": number,
                "original_match": original_match,
                "needs_clarification": True,
                "clarification_prompt": f"You mentioned '{original_match}', but I don't have a numbered list to reference. Could you clarify what you're referring to?",
            }

        # Rewrite the prompt to be explicit
        # Replace the pointer reference with explicit item reference
        rewritten = self._rewrite_with_item(user_input, original_match, number, item_text)

        return rewritten, True, {
            "pointer_detected": True,
            "number": number,
            "original_match": original_match,
            "item_text": item_text,
            "needs_clarification": False,
        }

    def _rewrite_with_item(
        self,
        text: str,
        original_match: str,
        number: int,
        item_text: str
    ) -> str:
        """
        Rewrite text replacing pointer with explicit item reference.

        Examples:
            "tell me about 10" → "tell me about item #10: The Bible and Law"
            "expand the second one" → "expand item #2: The Bible and Literature"
        """
        # Truncate item text if too long
        display_text = item_text[:80] + "..." if len(item_text) > 80 else item_text

        # Build replacement
        replacement = f"item #{number}: {display_text}"

        # Try to find and replace the original match
        # Case-insensitive replacement
        pattern = re.escape(original_match)
        rewritten = re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)

        # If replacement didn't change anything, append context
        if rewritten == text:
            rewritten = f"{text} [referring to item #{number}: {display_text}]"

        return rewritten


# =============================================================================
# Convenience Functions
# =============================================================================

_registry_cache: Dict[str, ListRegistry] = {}


def get_list_registry(user_id: str, session_id: str) -> ListRegistry:
    """Get or create a list registry for a session."""
    cache_key = f"{user_id}:{session_id}"
    if cache_key not in _registry_cache:
        _registry_cache[cache_key] = ListRegistry(user_id, session_id)
    return _registry_cache[cache_key]


def clear_registry_cache(user_id: str = None, session_id: str = None) -> None:
    """Clear cached registries."""
    global _registry_cache
    if user_id and session_id:
        cache_key = f"{user_id}:{session_id}"
        if cache_key in _registry_cache:
            del _registry_cache[cache_key]
    else:
        _registry_cache = {}


def process_ai_response_for_lists(
    user_id: str,
    session_id: str,
    ai_response: str,
    message_id: str = ""
) -> Optional[StoredList]:
    """
    Extract and store any numbered list from AI response.

    Call this after getting AI response, before returning to user.
    """
    registry = get_list_registry(user_id, session_id)
    return registry.extract_and_store(ai_response, message_id)


def resolve_user_pointer(
    user_id: str,
    session_id: str,
    user_input: str
) -> Tuple[str, bool, Optional[Dict[str, Any]]]:
    """
    Resolve any pointer references in user input.

    Call this before sending to AI.

    Returns:
        (rewritten_input, was_rewritten, info)
    """
    registry = get_list_registry(user_id, session_id)
    resolver = PointerResolver(registry)
    return resolver.resolve_and_rewrite(user_input)

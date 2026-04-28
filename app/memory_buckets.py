"""
Memory Buckets (DEPRECATED)
===========================

!!! DEPRECATED !!!
This module has been superseded by Memory V2.
Use app.memory_v2.MemoryOrchestrator instead.

Migration guide:
    # Old way (DEPRECATED)
    from app.memory_buckets import load_relevant_memory
    memory = load_relevant_memory(user_id, user_prompt)

    # New way (Memory V2)
    from app.memory_v2 import MemoryOrchestrator, SessionContext, ConversationState
    orchestrator = MemoryOrchestrator(user_id, session_ctx)
    context = orchestrator.get_context(user_message, conv_state)
    memory = context.context_block  # Formatted memory string

This module is kept for backward compatibility but will be removed in a future release.
!!! DEPRECATED !!!

Original description:
---------------------
Organizes user memory into logical buckets instead of one blob.

Buckets:
    - preferences: tone, format, language, communication style
    - projects: ongoing work, current stack, constraints
    - profile: stable facts (name, role, timezone)
    - do_not_assume: things the model should NOT guess
"""
import warnings
warnings.warn(
    "memory_buckets is deprecated and will be removed in a future release. "
    "Use app.memory_v2.MemoryOrchestrator instead.",
    DeprecationWarning,
    stacklevel=2
)

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from .config import db

logger = logging.getLogger(__name__)


# =============================================================================
# Bucket Definitions
# =============================================================================

BUCKETS = {
    "preferences": {
        "label": "Preferences",
        "description": "Communication style, output format, tone",
        "icon": "fa-sliders",
        "color": "#ffb74d",
        "keywords": [
            "prefer", "like", "want", "always", "never", "format", "style",
            "tone", "language", "short", "long", "detailed", "brief",
            "code", "explain", "response", "answer"
        ]
    },
    "projects": {
        "label": "Projects",
        "description": "Ongoing work, tech stack, goals",
        "icon": "fa-folder-open",
        "color": "#64b5f6",
        "keywords": [
            "project", "building", "working on", "developing", "app",
            "website", "stack", "framework", "database", "api", "feature",
            "codebase", "repository", "deploy", "production", "startup"
        ]
    },
    "profile": {
        "label": "Profile",
        "description": "Name, role, timezone, expertise",
        "icon": "fa-user",
        "color": "#a88bff",
        "keywords": [
            "my name", "i am", "i'm a", "i work", "my job", "my role",
            "timezone", "location", "experience", "background", "team",
            "developer", "engineer", "designer"
        ]
    },
    "do_not_assume": {
        "label": "Do Not Assume",
        "description": "Things to never guess about the user",
        "icon": "fa-ban",
        "color": "#ef5350",
        "keywords": [
            "don't assume", "never assume", "do not assume", "stop assuming",
            "i'm not", "i don't", "actually", "correction", "wrong"
        ]
    }
}

BUCKET_NAMES = list(BUCKETS.keys())


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class MemoryEntry:
    """Single memory entry with metadata and versioning."""
    text: str
    confidence: float = 0.8
    pinned: bool = False
    version: int = 1
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "pinned": self.pinned,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        created = data.get("created_at", datetime.utcnow().isoformat())
        return cls(
            text=data.get("text", ""),
            confidence=float(data.get("confidence", 0.8)),
            pinned=bool(data.get("pinned", False)),
            version=int(data.get("version", 1)),
            created_at=created,
            updated_at=data.get("updated_at", created)
        )


# =============================================================================
# Memory Bucket Manager
# =============================================================================

class MemoryBucketManager:
    """
    Manages structured memory buckets for a user.

    Example:
        manager = MemoryBucketManager(user_id)

        # Add memory
        manager.add("preferences", "Prefers short answers")

        # Get all memories
        all_data = manager.get_all()

        # Get specific bucket
        prefs = manager.get_bucket("preferences")

        # Clear bucket
        manager.clear_bucket("projects")
    """

    MAX_PER_BUCKET = 15

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._cache: Optional[Dict[str, List[MemoryEntry]]] = None

    def _get_user_ref(self):
        return db.collection('users').document(self.user_id)

    def _load(self) -> Dict[str, List[MemoryEntry]]:
        """Load buckets from Firestore, auto-migrating legacy memory if needed."""
        if self._cache is not None:
            return self._cache

        try:
            doc = self._get_user_ref().get()
            if not doc.exists:
                self._cache = {b: [] for b in BUCKET_NAMES}
                return self._cache

            data = doc.to_dict()
            raw = data.get('memory_buckets', {})

            # Auto-migrate if we have legacy memory but no buckets
            if not raw and data.get('long_term_memory'):
                logger.info(f"Auto-migrating legacy memory for user {self.user_id}")
                migrate_from_legacy(self.user_id)
                # Reload after migration
                doc = self._get_user_ref().get()
                raw = doc.to_dict().get('memory_buckets', {})

            self._cache = {}

            for bucket_name in BUCKET_NAMES:
                entries = []
                for item in raw.get(bucket_name, []):
                    entries.append(MemoryEntry.from_dict(item))
                self._cache[bucket_name] = entries

            return self._cache

        except Exception as e:
            logger.error(f"Failed to load memory buckets: {e}")
            return {b: [] for b in BUCKET_NAMES}

    def _save(self):
        """Save buckets to Firestore."""
        try:
            data = {}
            for bucket_name, entries in self._cache.items():
                data[bucket_name] = [e.to_dict() for e in entries]

            self._get_user_ref().set({
                'memory_buckets': data,
                'memory_updated_at': datetime.utcnow().isoformat()
            }, merge=True)

        except Exception as e:
            logger.error(f"Failed to save memory buckets: {e}")

    def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all buckets as dict."""
        buckets = self._load()
        return {
            name: [e.to_dict() for e in entries]
            for name, entries in buckets.items()
        }

    def get_bucket(self, bucket_name: str) -> List[Dict[str, Any]]:
        """Get entries from a specific bucket."""
        if bucket_name not in BUCKET_NAMES:
            return []
        buckets = self._load()
        return [e.to_dict() for e in buckets.get(bucket_name, [])]

    def get_flat_list(self) -> List[Dict[str, Any]]:
        """Get all memories as flat list (for UI)."""
        buckets = self._load()
        items = []
        for bucket_name, entries in buckets.items():
            for entry in entries:
                items.append({
                    "bucket": bucket_name,
                    "text": entry.text,
                    "confidence": entry.confidence,
                    "pinned": entry.pinned,
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at
                })
        return items

    def _is_similar(self, text1: str, text2: str, threshold: float = 0.7) -> bool:
        """Check if two texts are semantically similar using simple heuristics."""
        t1, t2 = text1.lower().strip(), text2.lower().strip()

        # Exact match
        if t1 == t2:
            return True

        # One contains the other (for updates like "Python developer" -> "Senior Python developer")
        if t1 in t2 or t2 in t1:
            return True

        # Word overlap ratio
        words1 = set(t1.split())
        words2 = set(t2.split())
        if not words1 or not words2:
            return False

        overlap = len(words1 & words2)
        total = len(words1 | words2)
        return (overlap / total) >= threshold

    def add(self, bucket_name: str, text: str, confidence: float = 0.8, use_anchoring: bool = True) -> bool:
        """
        Add a memory to a bucket with dedupe, conflict resolution, and anchoring.
        Newer info always overrides older conflicting memories.

        Args:
            bucket_name: Target bucket
            text: Memory text to add
            confidence: Confidence score (0-1)
            use_anchoring: Whether to use LLM-based conflict detection
        """
        if bucket_name not in BUCKET_NAMES:
            return False

        buckets = self._load()
        bucket = buckets[bucket_name]
        now = datetime.utcnow().isoformat()

        # 1. Check for conflicts using anchoring system
        if use_anchoring and bucket:
            try:
                from .memory_anchor import resolve_memory_conflicts
                existing_dicts = [e.to_dict() for e in bucket]
                resolution = resolve_memory_conflicts(text, existing_dicts, bucket_name)

                if resolution.has_conflict:
                    # Remove conflicting entries (newer overrides older)
                    texts_to_remove = {e.get("text") for e in resolution.to_remove}
                    removed_entries = []

                    for i in range(len(bucket) - 1, -1, -1):
                        if bucket[i].text in texts_to_remove:
                            removed_entry = bucket.pop(i)
                            removed_entries.append(removed_entry)
                            logger.info(f"Anchoring removed conflicting memory (len={len(removed_entry.text)})")

                    # Use the highest version from removed entries + 1
                    max_version = max((e.version for e in removed_entries), default=0)
                    new_version = max_version + 1

                    # Add the new memory with incremented version
                    bucket.append(MemoryEntry(
                        text=resolution.final_text,
                        confidence=confidence,
                        version=new_version,
                        created_at=now,
                        updated_at=now
                    ))
                    self._save()
                    logger.info(f"Anchoring resolved conflict (len={len(resolution.final_text)}, v{new_version})")
                    return True

            except Exception as e:
                logger.debug(f"Anchoring check failed, falling back to heuristic: {e}")

        # 2. Fallback: Simple similarity-based dedupe
        for i, entry in enumerate(bucket):
            if self._is_similar(entry.text, text):
                # Update existing: newer overrides older
                bucket[i] = MemoryEntry(
                    text=text,
                    confidence=max(confidence, entry.confidence),
                    pinned=entry.pinned,
                    version=entry.version + 1,
                    created_at=entry.created_at,
                    updated_at=now
                )
                self._save()
                logger.debug(f"Updated existing memory (old_len={len(entry.text)}, new_len={len(text)})")
                return True

        # 3. No conflict/similar found, add new
        bucket.append(MemoryEntry(text=text, confidence=confidence))

        # 4. Trim to max (but never remove pinned items)
        if len(bucket) > self.MAX_PER_BUCKET:
            # Find first non-pinned item to remove (oldest first)
            for i, entry in enumerate(bucket):
                if not entry.pinned:
                    bucket.pop(i)
                    break

        self._save()
        return True

    def toggle_pin(self, text: str) -> bool:
        """Toggle pin status for a memory item."""
        buckets = self._load()

        for bucket_name in BUCKET_NAMES:
            for entry in buckets[bucket_name]:
                if entry.text == text:
                    entry.pinned = not entry.pinned
                    entry.updated_at = datetime.utcnow().isoformat()
                    self._save()
                    return entry.pinned  # Return new pin status

        return False

    def remove(self, text: str) -> bool:
        """Remove a memory by exact text match (searches all buckets)."""
        buckets = self._load()
        removed = False

        for bucket_name in BUCKET_NAMES:
            original = len(buckets[bucket_name])
            buckets[bucket_name] = [
                e for e in buckets[bucket_name]
                if e.text != text
            ]
            if len(buckets[bucket_name]) < original:
                removed = True

        if removed:
            self._save()
        return removed

    def clear_bucket(self, bucket_name: str) -> bool:
        """Clear a specific bucket."""
        if bucket_name not in BUCKET_NAMES:
            return False

        buckets = self._load()
        buckets[bucket_name] = []
        self._save()
        return True

    def clear_all(self) -> bool:
        """Clear all buckets."""
        self._cache = {b: [] for b in BUCKET_NAMES}
        self._save()
        return True

    def total_count(self) -> int:
        """Total memories across all buckets."""
        buckets = self._load()
        return sum(len(entries) for entries in buckets.values())

    def to_summary(self, buckets_to_include: Optional[List[str]] = None) -> str:
        """
        Convert buckets to text for prompt injection.
        Pinned items are always included first, regardless of bucket filtering.

        Args:
            buckets_to_include: List of bucket names, or None for all
        """
        data = self._load()
        parts = []

        # Collect all pinned items first (always included)
        pinned_texts = []
        for bucket_name in BUCKET_NAMES:
            for entry in data.get(bucket_name, []):
                if entry.pinned:
                    pinned_texts.append(entry.text)

        if pinned_texts:
            parts.append(f"[Important] {'; '.join(pinned_texts)}")

        # Then add bucket-specific items (excluding already-listed pinned ones)
        for bucket_name in (buckets_to_include or BUCKET_NAMES):
            entries = data.get(bucket_name, [])
            if entries:
                label = BUCKETS[bucket_name]["label"]
                texts = [e.text for e in entries if not e.pinned]
                if texts:
                    parts.append(f"[{label}] {'; '.join(texts)}")

        return "\n".join(parts)


# =============================================================================
# Relevance Detection (LLM-based with keyword fallback)
# =============================================================================

BUCKET_ROUTER_PROMPT = """Given this user message, which memory buckets are relevant?

BUCKETS:
- profile: User's identity (name, role, timezone, expertise, background)
- preferences: Communication style, output format, tone, language preferences
- projects: Current work, tech stack, goals, what they're building
- do_not_assume: Things NOT to guess about the user

USER MESSAGE: {prompt}

Return ONLY a JSON array of relevant bucket names. Example: ["profile", "projects"]
If unsure, return ["profile", "do_not_assume"]"""


def _route_with_llm(user_prompt: str) -> Optional[List[str]]:
    """Use LLM to determine relevant buckets."""
    import json
    import requests
    from .config import Config

    try:
        api_key = Config.GROQ_API_KEY
        if not api_key:
            return None

        response = requests.post(
            Config.API_URL,
            json={
                "messages": [{"role": "user", "content": BUCKET_ROUTER_PROMPT.format(prompt=user_prompt[:500])}],
                "model": "llama-3.1-8b-instant",
                "max_tokens": 100,
                "temperature": 0.1
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=3  # Fast timeout for routing
        )

        if response.status_code != 200:
            return None

        content = response.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON array from response
        import re
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            buckets = json.loads(match.group())
            # Validate bucket names
            valid = [b for b in buckets if b in BUCKET_NAMES]
            if valid:
                # Always include do_not_assume
                if "do_not_assume" not in valid:
                    valid.append("do_not_assume")
                return valid

    except Exception as e:
        logger.debug(f"LLM bucket routing failed: {e}")

    return None


def _route_with_keywords(user_prompt: str) -> List[str]:
    """Fallback: keyword-based bucket routing."""
    prompt_lower = user_prompt.lower()
    relevant = set()

    for bucket_name, config in BUCKETS.items():
        for keyword in config["keywords"]:
            if keyword in prompt_lower:
                relevant.add(bucket_name)
                break

    # Always include do_not_assume for safety
    relevant.add("do_not_assume")

    # If only do_not_assume, add profile as baseline
    if len(relevant) == 1:
        relevant.add("profile")

    return list(relevant)


def detect_relevant_buckets(user_prompt: str, use_llm: bool = True) -> List[str]:
    """
    Detect which buckets are relevant to the user's prompt.
    Uses LLM routing with keyword fallback.

    Args:
        user_prompt: The user's message
        use_llm: Whether to try LLM routing first (default True)

    Returns:
        List of bucket names to include in context
    """
    if use_llm:
        result = _route_with_llm(user_prompt)
        if result:
            return result

    return _route_with_keywords(user_prompt)


# =============================================================================
# Main Interface Functions
# =============================================================================

def load_relevant_memory(user_id: str, user_prompt: str, max_chars: int = 2000) -> str:
    """
    Load only relevant memory buckets for the current prompt.

    This is the main function to use in build_prompt().

    Args:
        user_id: User's ID
        user_prompt: Current user message
        max_chars: Max characters to return

    Returns:
        Formatted memory string for prompt injection
    """
    try:
        manager = MemoryBucketManager(user_id)

        if manager.total_count() == 0:
            return ""

        relevant = detect_relevant_buckets(user_prompt)
        result = manager.to_summary(relevant)

        if len(result) > max_chars:
            result = result[:max_chars] + "..."

        return result

    except Exception as e:
        logger.warning(f"Failed to load relevant memory: {e}")
        return ""


def load_all_memory(user_id: str) -> str:
    """Load all memory buckets as text (backward compatible)."""
    try:
        manager = MemoryBucketManager(user_id)
        return manager.to_summary()
    except Exception as e:
        logger.warning(f"Failed to load all memory: {e}")
        return ""


def save_to_bucket(user_id: str, bucket_name: str, text: str, confidence: float = 0.8) -> bool:
    """
    Save a memory item to a specific bucket.

    Called by memory_gate after evaluation.
    """
    manager = MemoryBucketManager(user_id)
    return manager.add(bucket_name, text, confidence)


def get_bucket_metadata() -> Dict[str, Dict]:
    """Get bucket definitions (for UI)."""
    return BUCKETS


# =============================================================================
# Migration Helper
# =============================================================================

def migrate_from_legacy(user_id: str) -> bool:
    """
    Migrate old text-blob memory to structured buckets.

    Call once per user to migrate existing data.
    """
    try:
        user_ref = db.collection('users').document(user_id)
        doc = user_ref.get()

        if not doc.exists:
            return True

        data = doc.to_dict()
        old_memory = data.get('long_term_memory', '')

        if not old_memory:
            return True

        # Skip if already migrated
        if data.get('memory_buckets'):
            return True

        manager = MemoryBucketManager(user_id)

        # Type mapping
        type_to_bucket = {
            "preference": "preferences",
            "project": "projects",
            "fact": "profile",
            "skill": "profile",
            "context": "profile"
        }

        for line in old_memory.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Parse [type] text format
            match = re.match(r'\[(\w+)\]\s*(.+)', line)
            if match:
                old_type = match.group(1).lower()
                text = match.group(2)
                bucket = type_to_bucket.get(old_type, "profile")
                manager.add(bucket, text)
            else:
                manager.add("profile", line)

        logger.info(f"Migrated memory for user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return False

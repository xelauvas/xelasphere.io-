"""
Memory V2 Schema
================
Rich memory item schema with full metadata for ChatGPT/Claude-like memory.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MemoryType(str, Enum):
    """Memory type planes - each has different retrieval/conflict rules."""
    PROFILE = "profile"           # Low volume, high confidence (name, timezone, role)
    PREFERENCE = "preference"     # Soft constraints (style, format, tone)
    PROJECT = "project"           # Multi, evolving (current work, stack, goals)
    CONSTRAINT = "constraint"     # Safety/do-not rules (applied deterministically)
    TEMPORARY = "temporary"       # Short TTL, working memory traces


class MemoryStatus(str, Enum):
    """Memory lifecycle status."""
    ACTIVE = "active"             # Currently valid
    PROVISIONAL = "provisional"   # Low confidence, needs confirmation
    SUPERSEDED = "superseded"     # Replaced by newer memory
    EXPIRED = "expired"           # Past TTL
    DELETED = "deleted"           # User deleted
    QUARANTINED = "quarantined"   # Failed sanitizer check, needs review


@dataclass
class MemoryScope:
    """When/where this memory applies with include/exclude support."""
    # Include filters (memory applies when these match)
    include_topics: List[str] = field(default_factory=list)   # e.g., ["programming", "work"]
    include_intents: List[str] = field(default_factory=list)  # e.g., ["code_review", "architecture"]

    # Exclude filters (memory does NOT apply when these match)
    exclude_topics: List[str] = field(default_factory=list)   # e.g., ["quick_question"]
    exclude_intents: List[str] = field(default_factory=list)  # e.g., ["simple_lookup"]

    applies_when: Optional[str] = None                         # e.g., "user asks for code"
    channel: str = "chat"                                      # e.g., "chat", "voice"
    priority: int = 0                                          # Higher = more important in conflicts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "include_topics": self.include_topics,
            "include_intents": self.include_intents,
            "exclude_topics": self.exclude_topics,
            "exclude_intents": self.exclude_intents,
            "applies_when": self.applies_when,
            "channel": self.channel,
            "priority": self.priority
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryScope":
        # Handle legacy "topics" field
        include_topics = data.get("include_topics", data.get("topics", []))
        return cls(
            include_topics=include_topics,
            include_intents=data.get("include_intents", []),
            exclude_topics=data.get("exclude_topics", []),
            exclude_intents=data.get("exclude_intents", []),
            applies_when=data.get("applies_when"),
            channel=data.get("channel", "chat"),
            priority=int(data.get("priority", 0))
        )

    @staticmethod
    def _fuzzy_match(needle: str, haystack: str) -> bool:
        """Case-insensitive substring match for topic/intent comparison."""
        return needle.lower() in haystack.lower() or haystack.lower() in needle.lower()

    def matches(self, topics: List[str], intents: List[str]) -> float:
        """
        Check if this scope matches given topics/intents.

        Uses fuzzy substring matching so "python programming" matches "programming".

        Returns:
            0.0 if excluded, 1.0+ if included (with boost for explicit matches)
        """
        # Check exclusions first - if any match, return 0
        for topic in topics:
            for exc_topic in self.exclude_topics:
                if self._fuzzy_match(topic, exc_topic):
                    return 0.0
        for intent in intents:
            for exc_intent in self.exclude_intents:
                if self._fuzzy_match(intent, exc_intent):
                    return 0.0

        # Calculate inclusion score
        score = 1.0

        # Boost for explicit includes (with fuzzy matching)
        if self.include_topics:
            matches = sum(
                1 for t in topics
                if any(self._fuzzy_match(t, inc) for inc in self.include_topics)
            )
            if matches > 0:
                score += 0.2 * matches

        if self.include_intents:
            matches = sum(
                1 for i in intents
                if any(self._fuzzy_match(i, inc) for inc in self.include_intents)
            )
            if matches > 0:
                score += 0.3 * matches

        return score


@dataclass
class MemoryEvidence:
    """Source attribution for the memory."""
    source: str = "user"                                  # "user" | "inferred" | "system"
    turn_ids: List[str] = field(default_factory=list)    # Conversation turn IDs
    quote: Optional[str] = None                          # Original user quote
    extraction_method: str = "llm"                        # "llm" | "rule" | "explicit"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "turn_ids": self.turn_ids,
            "quote": self.quote,
            "extraction_method": self.extraction_method
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEvidence":
        return cls(
            source=data.get("source", "user"),
            turn_ids=data.get("turn_ids", []),
            quote=data.get("quote"),
            extraction_method=data.get("extraction_method", "llm")
        )


@dataclass
class MemoryLifecycle:
    """Temporal metadata for decay, cleanup, and confirmation tracking."""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None              # Updated on retrieval
    expires_at: Optional[datetime] = None                # For temporary items
    use_count: int = 0                                   # Times retrieved (for importance, NOT promotion)

    # Confirmation tracking (for promotion from provisional to active)
    repeated_by_user: int = 0                            # Times user restated this fact
    explicit_confirmed: bool = False                     # User explicitly said "yes/correct"
    last_confirmed_at: Optional[datetime] = None         # When last confirmed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "use_count": self.use_count,
            "repeated_by_user": self.repeated_by_user,
            "explicit_confirmed": self.explicit_confirmed,
            "last_confirmed_at": self.last_confirmed_at.isoformat() if self.last_confirmed_at else None
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryLifecycle":
        def parse_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        return cls(
            created_at=parse_dt(data.get("created_at")) or datetime.utcnow(),
            updated_at=parse_dt(data.get("updated_at")) or datetime.utcnow(),
            last_used_at=parse_dt(data.get("last_used_at")),
            expires_at=parse_dt(data.get("expires_at")),
            use_count=int(data.get("use_count", 0)),
            repeated_by_user=int(data.get("repeated_by_user", 0)),
            explicit_confirmed=bool(data.get("explicit_confirmed", False)),
            last_confirmed_at=parse_dt(data.get("last_confirmed_at"))
        )

    def is_expired(self) -> bool:
        """Check if memory has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def should_promote(self) -> bool:
        """Check if provisional memory should be promoted to active."""
        # Promote if: repeated by user >= 2 OR explicitly confirmed
        return self.repeated_by_user >= 2 or self.explicit_confirmed

    def mark_used(self):
        """Update last_used_at and increment use_count (for importance, not promotion)."""
        self.last_used_at = datetime.utcnow()
        self.use_count += 1

    def mark_repeated(self):
        """Mark that user repeated this fact."""
        self.repeated_by_user += 1
        self.last_confirmed_at = datetime.utcnow()

    def mark_confirmed(self):
        """Mark as explicitly confirmed by user."""
        self.explicit_confirmed = True
        self.last_confirmed_at = datetime.utcnow()


@dataclass
class MemoryItem:
    """
    Full memory item with rich metadata.

    Example:
        MemoryItem(
            type=MemoryType.PROFILE,
            key="timezone",
            value="Europe/London",
            confidence=0.92,
            evidence=MemoryEvidence(source="user", quote="I'm in London timezone")
        )
    """
    # Core fields
    id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    user_id: str = ""
    type: MemoryType = MemoryType.PROFILE
    key: str = ""                                        # e.g., "timezone", "tone", "project:foo"
    value: Any = ""                                      # The actual memory content

    # Metadata
    scope: MemoryScope = field(default_factory=MemoryScope)
    confidence: float = 0.8                              # 0.0 to 1.0
    importance: float = 0.5                              # 0.0 to 1.0 (for ranking)
    evidence: MemoryEvidence = field(default_factory=MemoryEvidence)
    lifecycle: MemoryLifecycle = field(default_factory=MemoryLifecycle)

    # Status
    status: MemoryStatus = MemoryStatus.ACTIVE
    pinned: bool = False                                 # User pinned (never auto-delete)

    # Supersede graph for traceability
    superseded_by: Optional[str] = None                  # ID of newer memory that replaced this
    supersedes: Optional[str] = None                     # ID of older memory this replaced

    # Versioning for rollbacks and migrations
    schema_version: int = 2                              # Schema version for migrations
    policy_version: str = "v2.1.0"                       # Policy version that created this

    # Embedding for semantic search (optional, computed on save)
    embedding: Optional[List[float]] = None              # Vector embedding for similarity search

    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dict."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type.value,
            "key": self.key,
            "value": self.value,
            "scope": self.scope.to_dict(),
            "confidence": self.confidence,
            "importance": self.importance,
            "evidence": self.evidence.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "status": self.status.value,
            "pinned": self.pinned,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "embedding": self.embedding
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        """Create from Firestore dict."""
        return cls(
            id=data.get("id", f"mem_{uuid.uuid4().hex[:12]}"),
            user_id=data.get("user_id", ""),
            type=MemoryType(data.get("type", "profile")),
            key=data.get("key", ""),
            value=data.get("value", ""),
            scope=MemoryScope.from_dict(data.get("scope", {})),
            confidence=float(data.get("confidence", 0.8)),
            importance=float(data.get("importance", 0.5)),
            evidence=MemoryEvidence.from_dict(data.get("evidence", {})),
            lifecycle=MemoryLifecycle.from_dict(data.get("lifecycle", {})),
            status=MemoryStatus(data.get("status", "active")),
            pinned=bool(data.get("pinned", False)),
            superseded_by=data.get("superseded_by"),
            supersedes=data.get("supersedes"),
            schema_version=int(data.get("schema_version", 2)),
            policy_version=data.get("policy_version", "v2.1.0"),
            embedding=data.get("embedding")
        )

    def is_valid(self) -> bool:
        """Check if memory is currently valid for use."""
        if self.status not in [MemoryStatus.ACTIVE, MemoryStatus.PROVISIONAL]:
            return False
        if self.lifecycle.is_expired():
            return False
        return True

    def validate_invariants(self) -> Tuple[bool, str]:
        """
        Validate memory invariants.

        Returns:
            (is_valid, error_message)
        """
        # Invariant 1: Provisional must have expires_at
        if self.status == MemoryStatus.PROVISIONAL and self.lifecycle.expires_at is None:
            return False, "Provisional memory must have expires_at set"

        # Invariant 2: Superseded items must have superseded_by
        if self.status == MemoryStatus.SUPERSEDED and self.superseded_by is None:
            return False, "Superseded memory must have superseded_by set"

        # Invariant 3: Pinned items MUST be ACTIVE (simple, avoids extra state)
        # Pinned is a boolean that works with ACTIVE status only
        if self.pinned and self.status != MemoryStatus.ACTIVE:
            return False, "Pinned memory must have status=ACTIVE"

        return True, ""

    def is_retrievable(self) -> bool:
        """
        Check if memory can be retrieved.

        Retrievable = (ACTIVE or PROVISIONAL) and not expired.
        Pinned items are always ACTIVE per invariant.
        """
        if self.status not in [MemoryStatus.ACTIVE, MemoryStatus.PROVISIONAL]:
            return False
        if self.lifecycle.is_expired():
            return False
        return True

    def calculate_score(self, relevance: float = 1.0) -> float:
        """
        Calculate retrieval score.
        Score = relevance × confidence × recency × importance × scope_match
        """
        # Recency decay (halve weight after 30 days of no use)
        recency = 1.0
        if self.lifecycle.last_used_at:
            days_since_use = (datetime.utcnow() - self.lifecycle.last_used_at).days
            recency = max(0.1, 1.0 - (days_since_use / 60))  # Decay over 60 days

        # Frequency boost (used more = more important)
        freq_boost = min(1.5, 1.0 + (self.lifecycle.use_count * 0.05))

        # Pinned items get boost
        pin_boost = 1.5 if self.pinned else 1.0

        return relevance * self.confidence * recency * self.importance * freq_boost * pin_boost

    def get_embedding_text(self) -> str:
        """
        Get text representation for embedding.

        Combines key and value into a searchable string.
        """
        # Format: "type: key = value"
        # This gives the embedding model context about what this memory represents
        value_str = str(self.value) if self.value else ""
        return f"{self.type.value}: {self.key} = {value_str}"


# Default TTLs for different memory types (type-specific decay)
DEFAULT_TTLS = {
    MemoryType.TEMPORARY: timedelta(hours=24),       # Short-lived working memory
    MemoryType.PROJECT: timedelta(days=90),          # Projects expire to inactive after 90 days
    # Profile: no TTL (permanent until superseded)
    # Preference: decay weight after ~180 days (handled in scoring, not hard TTL)
    # Constraint: no TTL (permanent, safety-critical)
}

# Provisional TTL (for all types when stored provisionally)
# 30 days gives users more time to naturally confirm facts
PROVISIONAL_TTL = timedelta(days=30)

# Type-specific decay weights (for scoring decay, not expiration)
DECAY_HALF_LIFE_DAYS = {
    MemoryType.PROFILE: None,       # No decay
    MemoryType.PREFERENCE: 180,     # Decay weight after 180 days of no use
    MemoryType.PROJECT: 60,         # Decay faster
    MemoryType.CONSTRAINT: None,    # No decay (safety-critical)
    MemoryType.TEMPORARY: 7,        # Rapid decay
}

# Allowed constraint keys (controlled keyspace for safety constraints)
# Only these keys are allowed for CONSTRAINT type to prevent misuse
ALLOWED_CONSTRAINT_KEYS = {
    # Privacy & Data
    "no_store_memory",              # Don't store any memories
    "no_location_assumption",       # Don't assume my location
    "no_personal_inference",        # Don't infer personal details

    # Content restrictions
    "no_explicit_content",
    "no_violence",
    "no_political_opinion",

    # Safety
    "allergy",                      # Food/medical allergies
    "medical_restriction",
    "dietary_restriction",

    # Communication
    "no_emoji",
    "no_slang",
    "formal_only",

    # Technical
    "no_deprecated_code",
    "security_first",

    # Generic constraints (from LLM extraction)
    "never_use",                    # User says "never use X"
    "avoid",                        # User says "avoid X"
    "dont_use",                     # User says "don't use X"
}

# Type weights for retrieval (profile facts > preferences for who/where/when)
TYPE_WEIGHTS = {
    MemoryType.PROFILE: 1.3,       # Boost profile facts
    MemoryType.PREFERENCE: 1.0,
    MemoryType.PROJECT: 1.1,
    MemoryType.CONSTRAINT: 1.5,    # Constraints are safety-critical
    MemoryType.TEMPORARY: 0.8,
}


def create_memory(
    user_id: str,
    memory_type: MemoryType,
    key: str,
    value: Any,
    confidence: float = 0.8,
    quote: Optional[str] = None,
    topics: Optional[List[str]] = None,
    provisional: bool = False
) -> MemoryItem:
    """
    Factory function to create a memory item with sensible defaults.

    Args:
        user_id: User's ID
        memory_type: Type of memory
        key: Memory key (e.g., "timezone", "project:myapp")
        value: Memory value
        confidence: Confidence score (0-1)
        quote: Original user quote (for evidence)
        topics: Scope topics
        provisional: If True, mark as provisional with short TTL

    Returns:
        MemoryItem ready for storage
    """
    now = datetime.utcnow()

    # Determine TTL
    expires_at = None
    if memory_type == MemoryType.TEMPORARY:
        expires_at = now + DEFAULT_TTLS[MemoryType.TEMPORARY]
    elif provisional:
        expires_at = now + PROVISIONAL_TTL

    return MemoryItem(
        user_id=user_id,
        type=memory_type,
        key=key,
        value=value,
        confidence=confidence,
        status=MemoryStatus.PROVISIONAL if provisional else MemoryStatus.ACTIVE,
        scope=MemoryScope(include_topics=topics or []),
        evidence=MemoryEvidence(
            source="user",
            quote=quote,
            extraction_method="llm" if quote else "rule"
        ),
        lifecycle=MemoryLifecycle(
            created_at=now,
            updated_at=now,
            expires_at=expires_at
        )
    )

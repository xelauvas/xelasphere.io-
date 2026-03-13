"""
Memory V2 Production Safeguards
===============================
Rate limiting, value constraints, and safety mechanisms.

These are small, high-impact protections that prevent:
- Memory spam from extractor going wild
- Degraded retrieval from oversized values
- Creepy wrong personalization from low-confidence facts
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from .schema import MemoryItem, MemoryType, MemoryStatus

logger = logging.getLogger(__name__)


# ============================================================================
# Rate Limiting
# ============================================================================

@dataclass
class RateLimitConfig:
    """Configuration for memory write rate limiting."""
    max_per_message: int = 5          # Max new memories per user message
    max_per_day: int = 20             # Max new memories per day
    max_updates_per_day: int = 30     # Max updates (supersede/edit) per day
    cooldown_minutes: int = 0         # No cooldown - per-message limit is sufficient


class RateLimiter:
    """
    Per-user rate limiter for memory writes.

    Prevents "memory spam" if extractor goes wild on long messages.

    Usage:
        limiter = RateLimiter(user_id)
        if limiter.can_write():
            # do write
            limiter.record_write()
    """

    def __init__(self, user_id: str, config: Optional[RateLimitConfig] = None):
        self.user_id = user_id
        self.config = config or RateLimitConfig()

        # In-memory tracking (should be Redis in production)
        self._message_writes: int = 0
        self._daily_writes: List[datetime] = []
        self._daily_updates: List[datetime] = []
        self._last_write: Optional[datetime] = None

    def reset_message_counter(self) -> None:
        """Reset per-message counter (call at start of each message)."""
        self._message_writes = 0

    def can_write(self) -> Tuple[bool, str]:
        """
        Check if a write is allowed.

        Returns:
            (allowed, reason) tuple
        """
        now = datetime.utcnow()

        # Per-message limit
        if self._message_writes >= self.config.max_per_message:
            return False, f"Max {self.config.max_per_message} memories per message"

        # Daily limit
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        recent_writes = [w for w in self._daily_writes if w >= day_start]
        if len(recent_writes) >= self.config.max_per_day:
            return False, f"Max {self.config.max_per_day} memories per day"

        # Cooldown
        if self._last_write:
            cooldown_end = self._last_write + timedelta(minutes=self.config.cooldown_minutes)
            if now < cooldown_end:
                return False, f"Cooldown: wait {self.config.cooldown_minutes}min between writes"

        return True, ""

    def can_update(self) -> Tuple[bool, str]:
        """Check if an update (supersede/edit) is allowed."""
        now = datetime.utcnow()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        recent_updates = [u for u in self._daily_updates if u >= day_start]

        if len(recent_updates) >= self.config.max_updates_per_day:
            return False, f"Max {self.config.max_updates_per_day} updates per day"

        return True, ""

    def record_write(self) -> None:
        """Record a successful write."""
        now = datetime.utcnow()
        self._message_writes += 1
        self._daily_writes.append(now)
        self._last_write = now

        # Prune old entries (keep last 24h)
        cutoff = now - timedelta(hours=24)
        self._daily_writes = [w for w in self._daily_writes if w > cutoff]

    def record_update(self) -> None:
        """Record a successful update."""
        now = datetime.utcnow()
        self._daily_updates.append(now)

        # Prune old entries
        cutoff = now - timedelta(hours=24)
        self._daily_updates = [u for u in self._daily_updates if u > cutoff]

    def get_stats(self) -> Dict[str, Any]:
        """Get current rate limit stats."""
        now = datetime.utcnow()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        return {
            "message_writes": self._message_writes,
            "daily_writes": len([w for w in self._daily_writes if w >= day_start]),
            "daily_updates": len([u for u in self._daily_updates if u >= day_start]),
            "max_per_message": self.config.max_per_message,
            "max_per_day": self.config.max_per_day,
        }


# ============================================================================
# Value Constraints
# ============================================================================

# Maximum value lengths by type
MAX_VALUE_LENGTHS = {
    MemoryType.PROFILE: 280,       # Short profile facts
    MemoryType.PREFERENCE: 280,    # Preferences should be concise
    MemoryType.CONSTRAINT: 200,    # Safety constraints must be clear
    MemoryType.TEMPORARY: 500,     # Working memory can be longer
    MemoryType.PROJECT: 1000,      # Projects get summary treatment
}

DEFAULT_MAX_VALUE_LENGTH = 280


def validate_value_length(memory: MemoryItem) -> Tuple[bool, str]:
    """
    Validate memory value length.

    Long values degrade retrieval quality and safety.

    Returns:
        (valid, error_message)
    """
    value_str = str(memory.value)
    max_length = MAX_VALUE_LENGTHS.get(memory.type, DEFAULT_MAX_VALUE_LENGTH)

    if len(value_str) > max_length:
        return False, f"Value too long ({len(value_str)} chars, max {max_length})"

    return True, ""


def summarize_long_value(value: str, max_length: int = 280) -> str:
    """
    Summarize a long value to fit within limits.

    For project descriptions, this creates a condensed version.
    """
    if len(value) <= max_length:
        return value

    # Simple truncation with ellipsis for now
    # In production, could use LLM summarization
    return value[:max_length - 3].rsplit(' ', 1)[0] + "..."


# ============================================================================
# Profile Evidence Requirements
# ============================================================================

# Profile keys that require stronger evidence
HIGH_STAKES_PROFILE_KEYS = {
    "name",
    "timezone",
    "location",
    "role",
    "company",
    "email",
}

# Minimum confidence for high-stakes profile without confirmation
HIGH_STAKES_MIN_CONFIDENCE = 0.85


def requires_stronger_evidence(memory: MemoryItem) -> Tuple[bool, str]:
    """
    Check if memory requires stronger evidence before storing.

    High-stakes profile facts need:
    - Explicit statement ("My timezone is...")
    - OR higher confidence threshold
    - OR confirmation prompt

    Returns:
        (needs_confirmation, reason)
    """
    if memory.type != MemoryType.PROFILE:
        return False, ""

    if memory.key not in HIGH_STAKES_PROFILE_KEYS:
        return False, ""

    # Explicit statement is strong evidence
    if memory.evidence.extraction_method == "explicit":
        return False, ""

    # High confidence is acceptable
    if memory.confidence >= HIGH_STAKES_MIN_CONFIDENCE:
        return False, ""

    # Otherwise needs confirmation
    return True, f"Profile fact '{memory.key}' requires confirmation (confidence {memory.confidence:.2f} < {HIGH_STAKES_MIN_CONFIDENCE})"


# ============================================================================
# Language/Locale Consistency
# ============================================================================

def should_scope_language_preference(
    new_language: str,
    existing_language: Optional[str],
    message_count_in_language: int = 1
) -> bool:
    """
    Determine if language preference should be conversation-scoped.

    Don't immediately overwrite "preferred language" when user switches.
    Instead, scope it to current conversation until pattern emerges.

    Args:
        new_language: Language detected in current message
        existing_language: User's stored language preference
        message_count_in_language: How many messages in this language

    Returns:
        True if should be scoped to conversation only
    """
    if existing_language is None:
        return False  # First language, store globally

    if new_language.lower() == existing_language.lower():
        return False  # Same language, no change needed

    # Require pattern before changing global preference
    if message_count_in_language < 3:
        return True  # Scope to conversation only

    return False  # Pattern established, can update global


# ============================================================================
# Low-Confidence Confirmation
# ============================================================================

CONFIRMATION_THRESHOLD = 0.75  # Ask to confirm if below this


@dataclass
class ConfirmationPrompt:
    """A prompt to confirm memory before using it for actions."""
    memory_id: str
    memory_key: str
    memory_value: Any
    confidence: float
    suggested_question: str


def should_confirm_before_action(
    memory: MemoryItem,
    action_type: str = "generic"
) -> Optional[ConfirmationPrompt]:
    """
    Check if memory should be confirmed before using for an action.

    When memory might be wrong, ask instead of asserting.

    Example:
        Memory: timezone=Europe/London (confidence 0.62)
        User: "schedule meeting tomorrow at 9"
        → Ask: "Is that 9am your local time (London)?"

    Args:
        memory: Memory being used
        action_type: Type of action ("schedule", "send", "order", etc.)

    Returns:
        ConfirmationPrompt if confirmation needed, None otherwise
    """
    if memory.confidence >= CONFIRMATION_THRESHOLD:
        return None

    # Generate appropriate question based on key and action
    question = _generate_confirmation_question(memory, action_type)

    return ConfirmationPrompt(
        memory_id=memory.id,
        memory_key=memory.key,
        memory_value=memory.value,
        confidence=memory.confidence,
        suggested_question=question
    )


def _generate_confirmation_question(memory: MemoryItem, action_type: str) -> str:
    """Generate a natural confirmation question."""
    key = memory.key.lower()
    value = memory.value

    if "timezone" in key:
        return f"Is that in your local time ({value})?"

    if "name" in key:
        return f"Should I address you as {value}?"

    if "location" in key:
        return f"I have your location as {value} - is that correct?"

    if "language" in key:
        return f"Should I respond in {value}?"

    if "style" in key or "preference" in key:
        return f"Would you like me to use {value} style?"

    # Generic fallback
    return f"I have '{memory.key}' set to '{value}' - is that correct?"


# ============================================================================
# Cold Start Behavior
# ============================================================================

def is_cold_start(memory_count: int, canonical_count: int = 0) -> bool:
    """
    Check if user is in cold-start state (no memories).

    If cold start, retrieval should skip LLM routing entirely.

    Args:
        memory_count: Total memory count
        canonical_count: Count of canonical (non-provisional) memories

    Returns:
        True if cold start (should skip LLM routing)
    """
    # Zero memories = definitely cold start
    if memory_count == 0:
        return True

    # Only provisional memories = still cold start
    if canonical_count == 0:
        return True

    return False


# ============================================================================
# Audit Log Enhancement
# ============================================================================

@dataclass
class AuditEntry:
    """Enhanced audit log entry with full context."""
    timestamp: datetime
    action: str                      # save, update, supersede, delete, quarantine
    memory_id: str
    actor: str                       # "user", "system", "cleanup_job"
    reason_code: str                 # Why this action was taken
    before_state: Optional[Dict] = None    # State before change
    after_state: Optional[Dict] = None     # State after change
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "memory_id": self.memory_id,
            "actor": self.actor,
            "reason_code": self.reason_code,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "metadata": self.metadata
        }


def create_audit_entry(
    action: str,
    memory_id: str,
    actor: str,
    reason_code: str,
    before: Optional[MemoryItem] = None,
    after: Optional[MemoryItem] = None,
    **metadata
) -> AuditEntry:
    """
    Create an enhanced audit entry with before/after diffs.

    Args:
        action: Action type
        memory_id: Memory ID
        actor: Who performed action
        reason_code: Why action was taken
        before: Memory state before
        after: Memory state after
        **metadata: Additional context

    Returns:
        AuditEntry ready for logging
    """
    return AuditEntry(
        timestamp=datetime.utcnow(),
        action=action,
        memory_id=memory_id,
        actor=actor,
        reason_code=reason_code,
        before_state=before.to_dict() if before else None,
        after_state=after.to_dict() if after else None,
        metadata=metadata
    )


# Reason codes for audit trail
class ReasonCode:
    """Standard reason codes for audit entries."""
    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    USER_RESTATED = "user_restated"
    USER_DELETED = "user_deleted"
    CONFLICT_SUPERSEDE = "conflict_supersede"
    TTL_EXPIRED = "ttl_expired"
    CLEANUP_JOB = "cleanup_job"
    INJECTION_DETECTED = "injection_detected"
    VALIDATION_FAILED = "validation_failed"
    RATE_LIMITED = "rate_limited"
    PROMOTION = "promotion"

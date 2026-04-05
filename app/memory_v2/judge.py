"""
Memory V2 Judge
================
Hybrid rules + LLM for storage decisions.

Decision criteria:
- Is this worth remembering? (not too trivial, not too specific)
- Is confidence high enough?
- Does it conflict with existing memory?
- Should it be provisional or canonical?
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .schema import MemoryItem, MemoryType, MemoryStatus
from .extractor import MemoryCandidate
from .store import MemoryStoreV2

logger = logging.getLogger(__name__)


class JudgmentDecision(str, Enum):
    """Possible storage decisions."""
    STORE = "store"                    # Store as active
    STORE_PROVISIONAL = "provisional"  # Store with TTL, needs confirmation
    UPDATE = "update"                  # Update existing memory
    SUPERSEDE = "supersede"            # Replace existing memory
    REJECT = "reject"                  # Don't store
    DEFER = "defer"                    # Ask user for confirmation


@dataclass
class JudgmentResult:
    """Result of judging a memory candidate."""
    decision: JudgmentDecision
    reason: str
    existing_memory_id: Optional[str] = None
    adjusted_confidence: float = 0.0
    needs_confirmation: bool = False
    memory_id: Optional[str] = None


class MemoryJudge:
    """
    Decides whether and how to store memory candidates.

    Uses hybrid approach:
    1. Rule-based checks (fast, deterministic)
    2. Conflict detection
    3. Optional LLM review (for edge cases)
    """

    # Minimum confidence thresholds by type
    MIN_CONFIDENCE = {
        MemoryType.PROFILE: 0.7,       # Higher bar for profile facts
        MemoryType.PREFERENCE: 0.6,
        MemoryType.PROJECT: 0.5,
        MemoryType.CONSTRAINT: 0.8,    # Constraints need high confidence
        MemoryType.TEMPORARY: 0.4,     # Lower bar for temp items
        MemoryType.FEEDBACK: 0.65,     # Medium bar - corrections are important but must be real
        MemoryType.REFERENCE: 0.6,     # Medium bar - references are verifiable
    }

    # Patterns that suggest low-value memories
    LOW_VALUE_PATTERNS = [
        "today", "right now", "just",
        "maybe", "probably", "i think",
        "test", "example", "demo"
    ]

    # Patterns that suggest high-value memories
    HIGH_VALUE_PATTERNS = [
        "always", "never", "must",
        "allergic", "important", "critical"
    ]

    def __init__(self, store: MemoryStoreV2, use_llm: bool = False):
        """
        Initialize judge.

        Args:
            store: Memory store for conflict checking
            use_llm: Whether to use LLM for edge cases
        """
        self.store = store
        self.use_llm = use_llm
        self._key_cache: Dict[str, Optional[MemoryItem]] = {}

    def prefetch_existing_keys(self, keys: List[str]) -> None:
        """Batch-fetch existing memories by key to avoid N individual Firestore calls."""
        self._key_cache.clear()
        for key in keys:
            if key not in self._key_cache:
                self._key_cache[key] = self.store.get_by_key(key)

    def judge(self, candidate: MemoryCandidate) -> JudgmentResult:
        """
        Judge a single memory candidate.

        Args:
            candidate: The memory candidate to judge

        Returns:
            JudgmentResult with decision and reasoning
        """
        # Step 1: Basic quality checks
        quality_result = self._check_quality(candidate)
        if quality_result.decision == JudgmentDecision.REJECT:
            return quality_result

        # Step 2: Adjust confidence based on signals
        adjusted_confidence = self._adjust_confidence(candidate)

        # Step 3: Check minimum threshold
        min_conf = self.MIN_CONFIDENCE.get(candidate.type, 0.6)
        if adjusted_confidence < min_conf:
            return JudgmentResult(
                decision=JudgmentDecision.STORE_PROVISIONAL,
                reason=f"Confidence {adjusted_confidence:.2f} below threshold {min_conf}",
                adjusted_confidence=adjusted_confidence
            )

        # Step 4: Check for conflicts with existing memories
        conflict_result = self._check_conflicts(candidate)
        if conflict_result:
            return conflict_result

        # Step 5: Determine final decision
        if adjusted_confidence >= 0.85:
            return JudgmentResult(
                decision=JudgmentDecision.STORE,
                reason="High confidence, no conflicts",
                adjusted_confidence=adjusted_confidence
            )
        else:
            return JudgmentResult(
                decision=JudgmentDecision.STORE_PROVISIONAL,
                reason="Moderate confidence, storing as provisional",
                adjusted_confidence=adjusted_confidence
            )

    def judge_batch(
        self,
        candidates: List[MemoryCandidate]
    ) -> List[Tuple[MemoryCandidate, JudgmentResult]]:
        """
        Judge multiple candidates.

        Returns:
            List of (candidate, result) tuples
        """
        results = []
        for candidate in candidates:
            result = self.judge(candidate)
            results.append((candidate, result))
        return results

    def _check_quality(self, candidate: MemoryCandidate) -> JudgmentResult:
        """Check basic quality of the candidate."""
        value = str(candidate.value).lower()

        # Too short
        if len(value) < 2:
            return JudgmentResult(
                decision=JudgmentDecision.REJECT,
                reason="Value too short"
            )

        # Too long (probably not a discrete fact)
        # Feedback type allows longer values (includes Why/How structure)
        max_length = 800 if candidate.type == MemoryType.FEEDBACK else 500
        if len(value) > max_length:
            return JudgmentResult(
                decision=JudgmentDecision.REJECT,
                reason="Value too long for memory"
            )

        # Empty or whitespace
        if not value.strip():
            return JudgmentResult(
                decision=JudgmentDecision.REJECT,
                reason="Empty value"
            )

        # Contains low-value patterns
        low_value_count = sum(1 for p in self.LOW_VALUE_PATTERNS if p in value)
        if low_value_count >= 2:
            return JudgmentResult(
                decision=JudgmentDecision.REJECT,
                reason="Too many low-value signals"
            )

        return JudgmentResult(
            decision=JudgmentDecision.STORE,
            reason="Passed quality checks"
        )

    def _adjust_confidence(self, candidate: MemoryCandidate) -> float:
        """Adjust confidence based on various signals."""
        confidence = candidate.confidence
        value = str(candidate.value).lower()
        quote = candidate.quote.lower()

        # Boost for explicit statements
        if candidate.extraction_method == "pattern":
            confidence += 0.05

        # Boost for high-value patterns
        for pattern in self.HIGH_VALUE_PATTERNS:
            if pattern in quote:
                confidence += 0.1
                break

        # Penalty for low-value patterns
        for pattern in self.LOW_VALUE_PATTERNS:
            if pattern in value:
                confidence -= 0.1
                break

        # Boost for constraints (safety-critical)
        if candidate.type == MemoryType.CONSTRAINT:
            confidence += 0.1

        # Boost for feedback (user corrections/confirmations are high signal)
        if candidate.type == MemoryType.FEEDBACK:
            # Corrections ("no not that", "stop doing X") are very reliable signals
            if any(w in quote for w in ["stop", "don't", "not that", "wrong", "no,"]):
                confidence += 0.10
            # Confirmations ("perfect", "exactly") are slightly less reliable
            elif any(w in quote for w in ["perfect", "exactly", "great", "keep"]):
                confidence += 0.05
            # "From now on" directives are explicit and high confidence
            if "from now on" in quote or "going forward" in quote:
                confidence += 0.10

        # Clamp to valid range
        return max(0.0, min(1.0, confidence))

    def _check_conflicts(self, candidate: MemoryCandidate) -> Optional[JudgmentResult]:
        """Check for conflicts with existing memories."""
        # Use prefetched cache if available, otherwise fetch
        if candidate.key in self._key_cache:
            existing = self._key_cache[candidate.key]
        else:
            existing = self.store.get_by_key(candidate.key)

        if not existing:
            return None  # No conflict

        # Same key exists - determine action
        existing_value = str(existing.value).lower()
        new_value = str(candidate.value).lower()

        # Exact duplicate
        if existing_value == new_value:
            return JudgmentResult(
                decision=JudgmentDecision.REJECT,
                reason="Exact duplicate already exists",
                existing_memory_id=existing.id
            )

        # Profile type: newer overwrites (singular facts)
        if candidate.type == MemoryType.PROFILE:
            return JudgmentResult(
                decision=JudgmentDecision.SUPERSEDE,
                reason="Profile fact updated - superseding old value",
                existing_memory_id=existing.id,
                adjusted_confidence=candidate.confidence
            )

        # Preference type: may accumulate or conflict
        if candidate.type == MemoryType.PREFERENCE:
            # Check if contradictory
            if self._is_contradictory(existing_value, new_value):
                return JudgmentResult(
                    decision=JudgmentDecision.SUPERSEDE,
                    reason="Contradictory preference - using newer",
                    existing_memory_id=existing.id,
                    adjusted_confidence=candidate.confidence
                )
            # Not contradictory - could accumulate, but same key means update
            return JudgmentResult(
                decision=JudgmentDecision.UPDATE,
                reason="Updating preference value",
                existing_memory_id=existing.id,
                adjusted_confidence=candidate.confidence
            )

        # Constraint type: accumulate (multiple constraints can coexist)
        if candidate.type == MemoryType.CONSTRAINT:
            if existing_value == new_value:
                return JudgmentResult(
                    decision=JudgmentDecision.REJECT,
                    reason="Constraint already exists",
                    existing_memory_id=existing.id
                )
            # Different constraints with same key - supersede
            return JudgmentResult(
                decision=JudgmentDecision.SUPERSEDE,
                reason="Constraint updated",
                existing_memory_id=existing.id,
                adjusted_confidence=candidate.confidence
            )

        # Project type: may have multiple active
        if candidate.type == MemoryType.PROJECT:
            return JudgmentResult(
                decision=JudgmentDecision.UPDATE,
                reason="Project info updated",
                existing_memory_id=existing.id,
                adjusted_confidence=candidate.confidence
            )

        # Feedback type: newer supersedes (user changed their mind or refined)
        if candidate.type == MemoryType.FEEDBACK:
            return JudgmentResult(
                decision=JudgmentDecision.SUPERSEDE,
                reason="Feedback updated — newer guidance replaces old",
                existing_memory_id=existing.id,
                adjusted_confidence=candidate.confidence
            )

        # Reference type: update (URLs/tools may change)
        if candidate.type == MemoryType.REFERENCE:
            return JudgmentResult(
                decision=JudgmentDecision.SUPERSEDE,
                reason="Reference updated — newer pointer replaces old",
                existing_memory_id=existing.id,
                adjusted_confidence=candidate.confidence
            )

        # Default: supersede
        return JudgmentResult(
            decision=JudgmentDecision.SUPERSEDE,
            reason="New value supersedes old",
            existing_memory_id=existing.id,
            adjusted_confidence=candidate.confidence
        )

    def _is_contradictory(self, value1: str, value2: str) -> bool:
        """Check if two preference values are contradictory."""
        # Simple heuristic: opposite words
        opposites = [
            ("like", "dislike"), ("prefer", "avoid"),
            ("always", "never"), ("want", "don't want"),
            ("concise", "detailed"), ("formal", "casual"),
            ("light", "dark"), ("yes", "no"),
        ]

        for word1, word2 in opposites:
            if (word1 in value1 and word2 in value2) or \
               (word2 in value1 and word1 in value2):
                return True

        return False

    def should_confirm_with_user(self, result: JudgmentResult) -> bool:
        """Determine if we should ask user for confirmation."""
        return result.decision == JudgmentDecision.DEFER


@dataclass
class ExecutionResult:
    """Result of executing a storage decision."""
    success: bool
    memory_id: Optional[str] = None
    message: str = ""


class StorageExecutor:
    """
    Executes storage decisions from the judge.
    """

    def __init__(self, store: MemoryStoreV2):
        self.store = store
        self.judge = MemoryJudge(store)

    def process_candidate(
        self,
        candidate: MemoryCandidate,
        user_id: str
    ) -> Tuple[bool, str]:
        """
        Process a single candidate through judgment and storage.

        Returns:
            (success, message)
        """
        from .schema import create_memory

        # Get judgment
        result = self.judge.judge(candidate)

        if result.decision == JudgmentDecision.REJECT:
            return False, f"Rejected: {result.reason}"

        if result.decision == JudgmentDecision.STORE:
            memory = create_memory(
                user_id=user_id,
                memory_type=candidate.type,
                key=candidate.key,
                value=candidate.value,
                confidence=result.adjusted_confidence or candidate.confidence,
                quote=candidate.quote,
                topics=candidate.topics,
                provisional=False
            )
            success = self.store.save(memory)
            return success, "Stored as active memory"

        if result.decision == JudgmentDecision.STORE_PROVISIONAL:
            memory = create_memory(
                user_id=user_id,
                memory_type=candidate.type,
                key=candidate.key,
                value=candidate.value,
                confidence=result.adjusted_confidence or candidate.confidence,
                quote=candidate.quote,
                topics=candidate.topics,
                provisional=True
            )
            success = self.store.save(memory)
            return success, "Stored as provisional (needs confirmation)"

        if result.decision == JudgmentDecision.UPDATE:
            if result.existing_memory_id:
                success = self.store.update(result.existing_memory_id, {
                    "value": candidate.value,
                    "confidence": result.adjusted_confidence or candidate.confidence
                })
                return success, "Updated existing memory"
            return False, "No existing memory to update"

        if result.decision == JudgmentDecision.SUPERSEDE:
            if result.existing_memory_id:
                memory = create_memory(
                    user_id=user_id,
                    memory_type=candidate.type,
                    key=candidate.key,
                    value=candidate.value,
                    confidence=result.adjusted_confidence or candidate.confidence,
                    quote=candidate.quote,
                    topics=candidate.topics,
                    provisional=False
                )
                success = self.store.supersede(result.existing_memory_id, memory)
                return success, "Superseded old memory with new value"
            return False, "No existing memory to supersede"

        return False, f"Unhandled decision: {result.decision}"

    def execute(
        self,
        judgment: JudgmentResult,
        candidate: 'MemoryCandidate',
        user_id: str
    ) -> 'ExecutionResult':
        """
        Execute a storage decision based on judgment result.

        Returns:
            ExecutionResult with success status and memory_id
        """
        from .schema import create_memory

        if judgment.decision == JudgmentDecision.REJECT:
            return ExecutionResult(success=False, message=f"Rejected: {judgment.reason}")

        if judgment.decision == JudgmentDecision.STORE:
            memory = create_memory(
                user_id=user_id,
                memory_type=candidate.type,
                key=candidate.key,
                value=candidate.value,
                confidence=judgment.adjusted_confidence or candidate.confidence,
                quote=candidate.quote,
                topics=candidate.topics,
                provisional=False
            )
            success = self.store.save(memory)
            return ExecutionResult(success=success, memory_id=memory.id if success else None, message="Stored")

        if judgment.decision == JudgmentDecision.STORE_PROVISIONAL:
            memory = create_memory(
                user_id=user_id,
                memory_type=candidate.type,
                key=candidate.key,
                value=candidate.value,
                confidence=judgment.adjusted_confidence or candidate.confidence,
                quote=candidate.quote,
                topics=candidate.topics,
                provisional=True
            )
            success = self.store.save(memory)
            return ExecutionResult(success=success, memory_id=memory.id if success else None, message="Stored provisional")

        if judgment.decision == JudgmentDecision.SUPERSEDE:
            if judgment.existing_memory_id:
                memory = create_memory(
                    user_id=user_id,
                    memory_type=candidate.type,
                    key=candidate.key,
                    value=candidate.value,
                    confidence=judgment.adjusted_confidence or candidate.confidence,
                    quote=candidate.quote,
                    topics=candidate.topics,
                    provisional=False
                )
                success = self.store.supersede(judgment.existing_memory_id, memory)
                return ExecutionResult(success=success, memory_id=memory.id if success else None, message="Superseded")
            return ExecutionResult(success=False, message="No existing memory to supersede")

        if judgment.decision == JudgmentDecision.UPDATE:
            if judgment.existing_memory_id:
                success = self.store.update(judgment.existing_memory_id, {
                    "value": candidate.value,
                    "confidence": judgment.adjusted_confidence or candidate.confidence
                })
                return ExecutionResult(success=success, memory_id=judgment.existing_memory_id, message="Updated")
            return ExecutionResult(success=False, message="No existing memory to update")

        return ExecutionResult(success=False, message=f"Unhandled decision: {judgment.decision}")

    def process_batch(
        self,
        candidates: List[MemoryCandidate],
        user_id: str
    ) -> List[Tuple[MemoryCandidate, bool, str]]:
        """
        Process multiple candidates.

        Returns:
            List of (candidate, success, message)
        """
        results = []
        for candidate in candidates:
            success, message = self.process_candidate(candidate, user_id)
            results.append((candidate, success, message))
            logger.debug(f"Processed {candidate.key}: {message}")
        return results

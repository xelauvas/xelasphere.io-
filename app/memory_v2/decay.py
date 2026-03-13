"""
Memory V2 Decay
================
TTL management, expiration, and cleanup.

Decay rules (type-specific):
- Temporary: 24h TTL
- Provisional: 14d TTL (promoted on confirmation, NOT usage)
- Projects: 90d TTL to inactive after no mention
- Preferences: decay weight after 180d of no use (handled in scoring)
- Profile: no TTL (permanent until superseded)
- Constraints: no TTL (safety-critical)
- Pinned: override decay but not conflict validation

Promotion rules:
- provisional → active if (repeated_by_user >= 2) OR (explicit_confirmed=true)
- "uses" increase importance weight, but do NOT promote
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .schema import MemoryItem, MemoryType, MemoryStatus, DEFAULT_TTLS, DECAY_HALF_LIFE_DAYS
from .store import MemoryStoreV2

logger = logging.getLogger(__name__)


# Decay configuration
DECAY_CONFIG = {
    "unused_threshold_days": 60,       # Mark as stale after N days of no use
    "provisional_ttl_days": 14,        # Provisional items expire after 14 days
    "temporary_ttl_hours": 24,         # Temporary items expire after 24 hours
    "project_inactive_days": 90,       # Projects expire to inactive after 90 days
    "cleanup_batch_size": 100,         # Max items to process per cleanup run
    "min_repeats_for_promotion": 2,    # User must repeat fact 2+ times to promote
}


class DecayManager:
    """
    Manages memory decay, expiration, and cleanup.

    Responsibilities:
    - Calculate and update expiration times
    - Clean up expired memories
    - Promote frequently-used provisional memories
    - Track usage patterns
    """

    def __init__(self, store: MemoryStoreV2, config: Optional[Dict] = None):
        """
        Initialize decay manager.

        Args:
            store: Memory store
            config: Optional config overrides
        """
        self.store = store
        self.config = {**DECAY_CONFIG, **(config or {})}

    def run_cleanup(self) -> Dict[str, int]:
        """
        Run full cleanup cycle.

        Returns:
            Dict with counts of actions taken
        """
        stats = {
            "expired": 0,
            "promoted": 0,
            "marked_stale": 0,
            "cleaned_up": 0,
        }

        # 1. Handle expired memories
        stats["expired"] = self._cleanup_expired()

        # 2. Promote CONFIRMED provisional memories (not usage-based)
        stats["promoted"] = self._promote_confirmed_provisionals()

        # 3. Mark unused memories as stale
        stats["marked_stale"] = self._mark_stale()

        # 4. Clean up old deleted/superseded
        stats["cleaned_up"] = self._cleanup_old_deleted()

        logger.info(f"Cleanup complete: {stats}")
        return stats

    def _cleanup_expired(self) -> int:
        """Mark expired memories as expired."""
        return self.store.cleanup_expired()

    def _promote_confirmed_provisionals(self) -> int:
        """
        Promote provisional memories that have been CONFIRMED (not just used).

        Promotion criteria:
        - repeated_by_user >= 2 (user restated the fact)
        - OR explicit_confirmed = true (user said "yes/correct")

        IMPORTANT: usage (use_count) does NOT trigger promotion.
        """
        promoted = 0
        min_repeats = self.config["min_repeats_for_promotion"]

        try:
            # Get provisional memories from all types
            provisionals = []
            for memory_type in MemoryType:
                provisionals.extend(
                    self.store.get_by_type(memory_type, status=MemoryStatus.PROVISIONAL, limit=50)
                )

            for memory in provisionals:
                # Check confirmation criteria (NOT usage)
                should_promote = memory.lifecycle.should_promote()

                if should_promote:
                    if self.store.promote_provisional(memory.id):
                        promoted += 1
                        logger.info(
                            f"Promoted memory {memory.id} "
                            f"(repeated: {memory.lifecycle.repeated_by_user}, "
                            f"confirmed: {memory.lifecycle.explicit_confirmed})"
                        )

        except Exception as e:
            logger.error(f"Failed to promote provisionals: {e}")

        return promoted

    def _mark_stale(self) -> int:
        """Mark memories that haven't been used in a long time."""
        marked = 0
        threshold_days = self.config["unused_threshold_days"]
        threshold_date = datetime.utcnow() - timedelta(days=threshold_days)

        try:
            all_memories = self.store.get_all(include_expired=False)

            for memory in all_memories:
                # Skip pinned
                if memory.pinned:
                    continue

                # Check last used
                last_used = memory.lifecycle.last_used_at
                if last_used is None:
                    last_used = memory.lifecycle.created_at

                if last_used < threshold_date and memory.lifecycle.use_count < 3:
                    # This memory is stale - reduce confidence over time
                    new_confidence = max(0.3, memory.confidence * 0.9)
                    self.store.update(memory.id, {"confidence": new_confidence})
                    marked += 1

        except Exception as e:
            logger.error(f"Failed to mark stale: {e}")

        return marked

    def _cleanup_old_deleted(self) -> int:
        """Permanently remove old deleted/superseded memories."""
        cleaned = 0
        cutoff = datetime.utcnow() - timedelta(days=30)

        try:
            all_memories = self.store.get_all(include_expired=True)

            for memory in all_memories:
                if memory.status in [MemoryStatus.DELETED, MemoryStatus.SUPERSEDED, MemoryStatus.EXPIRED]:
                    if memory.lifecycle.updated_at < cutoff:
                        if self.store.delete(memory.id, hard_delete=True):
                            cleaned += 1

        except Exception as e:
            logger.error(f"Failed to cleanup deleted: {e}")

        return cleaned

    def calculate_ttl(self, memory: MemoryItem) -> Optional[datetime]:
        """
        Calculate expiration time for a memory.

        Args:
            memory: The memory item

        Returns:
            Expiration datetime, or None if no expiration
        """
        # Pinned never expire
        if memory.pinned:
            return None

        # Check if type has default TTL
        if memory.type in DEFAULT_TTLS:
            return datetime.utcnow() + DEFAULT_TTLS[memory.type]

        # Provisional items get TTL
        if memory.status == MemoryStatus.PROVISIONAL:
            return datetime.utcnow() + timedelta(days=self.config["provisional_ttl_days"])

        # Active items don't expire by default
        return None

    def extend_ttl(self, memory_id: str, days: int = 7) -> bool:
        """
        Extend expiration for a memory.

        Args:
            memory_id: Memory to extend
            days: Days to extend by

        Returns:
            True if extended successfully
        """
        try:
            memory = self.store.get(memory_id)
            if not memory:
                return False

            current_expires = memory.lifecycle.expires_at
            if current_expires is None:
                # No expiration to extend
                return True

            new_expires = max(current_expires, datetime.utcnow()) + timedelta(days=days)
            return self.store.update(memory_id, {
                "lifecycle.expires_at": new_expires.isoformat()
            })

        except Exception as e:
            logger.error(f"Failed to extend TTL: {e}")
            return False

    def touch(self, memory_id: str) -> bool:
        """
        Touch a memory (update last_used, extend TTL if needed).

        Args:
            memory_id: Memory to touch

        Returns:
            True if touched successfully
        """
        success = self.store.mark_used(memory_id)

        # If provisional and used, consider extending TTL
        if success:
            memory = self.store.get(memory_id)
            if memory and memory.status == MemoryStatus.PROVISIONAL:
                # Extend provisional TTL on use
                self.extend_ttl(memory_id, days=3)

        return success


class ConfirmationTracker:
    """
    Tracks confirmation signals for provisional memories.

    Promotion criteria (NOT based on usage):
    - repeated_by_user >= 2: User restated the same fact independently
    - explicit_confirmed = true: User explicitly said "yes/correct/exactly"

    IMPORTANT: Being retrieved/used does NOT confirm a memory.

    SAFETY: Explicit confirmation only works for memories in pending_confirmation_ids.
    This prevents accidental promotions from random "yes" responses.
    """

    # Patterns that indicate explicit confirmation
    EXPLICIT_CONFIRMATION_PATTERNS = [
        r'^(yes|yeah|yep|correct|exactly|right|that\'?s right|indeed)\s*[!.]?$',
        r'^(yes|yeah),?\s+(that\'?s|it\'?s)\s+(correct|right)',
        r'^(you\'?re|that\'?s)\s+(correct|right)',
    ]

    def __init__(self, store: MemoryStoreV2):
        self.store = store
        self.decay_manager = DecayManager(store)
        # Track which memory IDs are pending confirmation (asked about in current turn)
        self._pending_confirmation_ids: List[str] = []

    def set_pending_confirmation(self, memory_ids: List[str]) -> None:
        """
        Set which memory IDs can be explicitly confirmed.

        Call this when assistant asks user to confirm a memory.
        Only these IDs can be promoted via explicit confirmation.
        """
        self._pending_confirmation_ids = memory_ids.copy()
        logger.debug(f"Set pending confirmation for {len(memory_ids)} memories")

    def clear_pending_confirmation(self) -> None:
        """Clear pending confirmation IDs (e.g., after turn ends)."""
        self._pending_confirmation_ids = []

    def get_pending_confirmation(self) -> List[str]:
        """Get list of memory IDs pending confirmation."""
        return self._pending_confirmation_ids.copy()

    def record_explicit_confirmation(self, memory_id: str, force: bool = False) -> bool:
        """
        Record an explicit confirmation (user said "yes/correct").

        SAFETY: Only confirms if memory_id is in _pending_confirmation_ids,
        unless force=True (for admin/testing only).

        This prevents accidental promotions from random "yes" responses.

        Args:
            memory_id: Memory to confirm
            force: Bypass pending check (admin only)

        Returns:
            True if confirmed and promoted
        """
        try:
            # SAFETY CHECK: Only confirm memories we explicitly asked about
            if not force and memory_id not in self._pending_confirmation_ids:
                logger.warning(
                    f"Rejected explicit confirmation for {memory_id}: "
                    f"not in pending_confirmation_ids"
                )
                return False

            memory = self.store.get(memory_id)
            if not memory:
                return False

            # Update the explicit_confirmed flag
            self.store.update(memory_id, {
                "lifecycle.explicit_confirmed": True,
                "lifecycle.last_confirmed_at": datetime.utcnow().isoformat()
            })

            # Check if should promote now
            if memory.status == MemoryStatus.PROVISIONAL:
                promoted = self.store.promote_provisional(memory_id)
                if promoted:
                    logger.info(f"Promoted memory {memory_id} via explicit confirmation")
                return promoted

            return True

        except Exception as e:
            logger.error(f"Failed to record explicit confirmation: {e}")
            return False

    def record_restatement(self, memory_id: str) -> bool:
        """
        Record that user restated the same fact.

        Increments repeated_by_user counter.
        """
        try:
            from google.cloud.firestore_v1 import Increment

            self.store._get_memory_ref().document(memory_id).update({
                "lifecycle.repeated_by_user": Increment(1),
                "lifecycle.last_confirmed_at": datetime.utcnow().isoformat()
            })

            # Check if should promote (2+ repeats)
            memory = self.store.get(memory_id)
            if memory and memory.status == MemoryStatus.PROVISIONAL:
                if memory.lifecycle.repeated_by_user >= 2:
                    return self.store.promote_provisional(memory_id)

            return True

        except Exception as e:
            logger.error(f"Failed to record restatement: {e}")
            return False

    def is_explicit_confirmation(self, text: str) -> bool:
        """Check if text is an explicit confirmation phrase."""
        import re
        text_lower = text.lower().strip()
        for pattern in self.EXPLICIT_CONFIRMATION_PATTERNS:
            if re.match(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    def check_restatement(
        self,
        user_message: str,
        memory: MemoryItem
    ) -> bool:
        """
        Check if user message restates/confirms a memory.

        Args:
            user_message: Current user message
            memory: Memory to check against

        Returns:
            True if message confirms the memory
        """
        message_lower = user_message.lower()
        value_lower = str(memory.value).lower()

        # Check for exact or close match
        if value_lower in message_lower:
            return True

        # Check for key words
        key_words = memory.key.lower().split("_")
        value_words = value_lower.split()

        matches = sum(1 for word in key_words + value_words if word in message_lower and len(word) > 2)
        return matches >= 2

    def process_message_for_confirmations(
        self,
        user_message: str,
        user_id: str
    ) -> List[str]:
        """
        Check if user message confirms any provisional memories.

        Two pathways:
        1. Explicit confirmation: User says "yes/correct" when pending_confirmation_ids is set
        2. Restatement: User repeats a fact (increments repeated_by_user)

        Args:
            user_message: User's message
            user_id: User's ID

        Returns:
            List of promoted memory IDs
        """
        promoted = []

        # Pathway 1: Explicit confirmation (only for pending IDs - prevents accidents)
        if self._pending_confirmation_ids and self.is_explicit_confirmation(user_message):
            for memory_id in self._pending_confirmation_ids:
                # record_explicit_confirmation checks _pending_confirmation_ids internally
                if self.record_explicit_confirmation(memory_id):
                    promoted.append(memory_id)

            # Clear pending after processing explicit confirmation
            self.clear_pending_confirmation()

            if promoted:
                return promoted

        # Pathway 2: Restatement detection (checks all provisionals)
        store = MemoryStoreV2(user_id)
        all_memories = store.get_all()
        provisionals = [m for m in all_memories if m.status == MemoryStatus.PROVISIONAL]

        for memory in provisionals:
            if self.check_restatement(user_message, memory):
                if self.record_restatement(memory.id):
                    # Check if promoted (requires 2+ restatements)
                    updated = store.get(memory.id)
                    if updated and updated.status == MemoryStatus.ACTIVE:
                        promoted.append(memory.id)
                        logger.info(f"Promoted memory {memory.id} via restatement")

        return promoted


def run_scheduled_cleanup(user_id: str) -> Dict[str, int]:
    """
    Run scheduled cleanup for a user.

    Called periodically (e.g., daily) to maintain memory hygiene.
    """
    store = MemoryStoreV2(user_id)
    manager = DecayManager(store)
    return manager.run_cleanup()

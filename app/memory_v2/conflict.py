"""
Memory V2 Conflict Resolution
==============================
Type-aware conflict resolution strategies.

Conflict rules by type:
- Profile: Newer overwrites (singular facts like name, timezone)
- Preference: Accumulate unless contradictory
- Project: Multiple can coexist, merge if same project
- Constraint: Accumulate (all constraints apply)
- Temporary: Newer overwrites
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from .schema import MemoryItem, MemoryType, MemoryStatus
from .store import MemoryStoreV2

logger = logging.getLogger(__name__)


class ConflictStrategy(str, Enum):
    """How to resolve a conflict."""
    OVERWRITE = "overwrite"     # New replaces old
    MERGE = "merge"             # Combine values
    ACCUMULATE = "accumulate"   # Keep both
    IGNORE = "ignore"           # Keep old, ignore new
    DEFER = "defer"             # Ask user


@dataclass
class ConflictInfo:
    """Information about a detected conflict."""
    existing: MemoryItem
    new_value: any
    new_confidence: float
    conflict_type: str  # "exact_duplicate", "same_key", "contradictory", "related"
    recommended_strategy: ConflictStrategy
    reason: str


class ConflictResolver:
    """
    Detects and resolves memory conflicts.

    Different memory types have different conflict resolution strategies:
    - Profile facts are singular (one name, one timezone) → overwrite
    - Preferences can accumulate (like multiple languages) → accumulate/merge
    - Projects can coexist (working on multiple things) → accumulate
    - Constraints always accumulate (all rules apply) → accumulate
    """

    # Type-specific strategies
    TYPE_STRATEGIES = {
        MemoryType.PROFILE: ConflictStrategy.OVERWRITE,
        MemoryType.PREFERENCE: ConflictStrategy.ACCUMULATE,
        MemoryType.PROJECT: ConflictStrategy.ACCUMULATE,
        MemoryType.CONSTRAINT: ConflictStrategy.ACCUMULATE,
        MemoryType.TEMPORARY: ConflictStrategy.OVERWRITE,
    }

    # Keys that are singular (only one value makes sense)
    SINGULAR_KEYS = {
        "name", "first_name", "last_name", "nickname",
        "timezone", "language", "role", "job_title",
        "location", "city", "country",
    }

    # Keys that can accumulate
    ACCUMULATING_KEYS = {
        "interest", "hobby", "skill", "language_spoken",
        "tool", "framework", "library",
    }

    def __init__(self, store: MemoryStoreV2):
        self.store = store

    def detect_conflicts(
        self,
        new_memory: MemoryItem,
        existing_memories: Optional[List[MemoryItem]] = None
    ) -> List[ConflictInfo]:
        """
        Detect conflicts between new memory and existing ones.

        Args:
            new_memory: The memory being added
            existing_memories: Optional list to check against (otherwise fetches from store)

        Returns:
            List of ConflictInfo for each detected conflict
        """
        if existing_memories is None:
            existing_memories = self.store.get_by_type(new_memory.type)

        conflicts = []

        for existing in existing_memories:
            conflict = self._check_pair_conflict(existing, new_memory)
            if conflict:
                conflicts.append(conflict)

        return conflicts

    def _check_pair_conflict(
        self,
        existing: MemoryItem,
        new: MemoryItem
    ) -> Optional[ConflictInfo]:
        """Check if two memories conflict."""
        # Same ID is an update, not a conflict
        if existing.id == new.id:
            return None

        # Check for same key
        if existing.key == new.key:
            # Exact duplicate
            if str(existing.value).lower() == str(new.value).lower():
                return ConflictInfo(
                    existing=existing,
                    new_value=new.value,
                    new_confidence=new.confidence,
                    conflict_type="exact_duplicate",
                    recommended_strategy=ConflictStrategy.IGNORE,
                    reason="Exact duplicate already exists"
                )

            # Same key, different value
            strategy = self._get_strategy_for_key(existing.key, existing.type)
            return ConflictInfo(
                existing=existing,
                new_value=new.value,
                new_confidence=new.confidence,
                conflict_type="same_key",
                recommended_strategy=strategy,
                reason=f"Same key with different value"
            )

        # Check for contradictory values
        if self._are_contradictory(existing, new):
            return ConflictInfo(
                existing=existing,
                new_value=new.value,
                new_confidence=new.confidence,
                conflict_type="contradictory",
                recommended_strategy=ConflictStrategy.OVERWRITE,
                reason="Values are contradictory"
            )

        # Check for related memories (same topic/scope)
        if self._are_related(existing, new):
            return ConflictInfo(
                existing=existing,
                new_value=new.value,
                new_confidence=new.confidence,
                conflict_type="related",
                recommended_strategy=ConflictStrategy.MERGE,
                reason="Related memories that could be merged"
            )

        return None

    def _get_strategy_for_key(self, key: str, memory_type: MemoryType) -> ConflictStrategy:
        """Determine strategy based on key and type."""
        key_lower = key.lower()

        # Singular keys always overwrite
        if key_lower in self.SINGULAR_KEYS:
            return ConflictStrategy.OVERWRITE

        # Accumulating keys accumulate
        if key_lower in self.ACCUMULATING_KEYS:
            return ConflictStrategy.ACCUMULATE

        # Fall back to type default
        return self.TYPE_STRATEGIES.get(memory_type, ConflictStrategy.OVERWRITE)

    def _are_contradictory(self, mem1: MemoryItem, mem2: MemoryItem) -> bool:
        """Check if two memories are contradictory."""
        val1 = str(mem1.value).lower()
        val2 = str(mem2.value).lower()

        # Check for negation
        if (val1.startswith("not ") and not val2.startswith("not ")) or \
           (not val1.startswith("not ") and val2.startswith("not ")):
            # Check if base content matches
            base1 = val1.replace("not ", "").strip()
            base2 = val2.replace("not ", "").strip()
            if base1 == base2:
                return True

        # Check for opposite preferences
        opposites = [
            ("prefer", "avoid"), ("like", "dislike"),
            ("always", "never"), ("want", "don't want"),
            ("light", "dark"), ("concise", "verbose"),
        ]

        for pos, neg in opposites:
            if (pos in val1 and neg in val2) or (neg in val1 and pos in val2):
                return True

        return False

    def _are_related(self, mem1: MemoryItem, mem2: MemoryItem) -> bool:
        """Check if two memories are related (same topic/scope)."""
        # Same type and overlapping topics
        if mem1.type != mem2.type:
            return False

        topics1 = set(mem1.scope.topics)
        topics2 = set(mem2.scope.topics)

        if topics1 and topics2 and topics1.intersection(topics2):
            return True

        # Same key prefix (e.g., "project:myapp" and "project:myapp:stack")
        if mem1.key.startswith(mem2.key) or mem2.key.startswith(mem1.key):
            return True

        return False

    def resolve(
        self,
        conflict: ConflictInfo,
        new_memory: MemoryItem,
        force_strategy: Optional[ConflictStrategy] = None
    ) -> Tuple[bool, str]:
        """
        Resolve a conflict according to strategy.

        Args:
            conflict: The conflict to resolve
            new_memory: The new memory being added
            force_strategy: Override the recommended strategy

        Returns:
            (success, message)
        """
        strategy = force_strategy or conflict.recommended_strategy

        if strategy == ConflictStrategy.IGNORE:
            return False, "Ignoring duplicate"

        if strategy == ConflictStrategy.OVERWRITE:
            return self._resolve_overwrite(conflict, new_memory)

        if strategy == ConflictStrategy.MERGE:
            return self._resolve_merge(conflict, new_memory)

        if strategy == ConflictStrategy.ACCUMULATE:
            return self._resolve_accumulate(conflict, new_memory)

        return False, f"Unknown strategy: {strategy}"

    def _resolve_overwrite(
        self,
        conflict: ConflictInfo,
        new_memory: MemoryItem
    ) -> Tuple[bool, str]:
        """Resolve by superseding old with new."""
        success = self.store.supersede(conflict.existing.id, new_memory)
        if success:
            return True, f"Superseded old value '{conflict.existing.value}' with '{new_memory.value}'"
        return False, "Failed to supersede"

    def _resolve_merge(
        self,
        conflict: ConflictInfo,
        new_memory: MemoryItem
    ) -> Tuple[bool, str]:
        """Resolve by merging values."""
        # Merge depends on value type
        existing_val = conflict.existing.value
        new_val = new_memory.value

        if isinstance(existing_val, list):
            if isinstance(new_val, list):
                merged = list(set(existing_val + new_val))
            else:
                merged = list(set(existing_val + [new_val]))
        elif isinstance(new_val, list):
            merged = list(set([existing_val] + new_val))
        else:
            # Both are scalars - combine as list
            merged = [existing_val, new_val]

        # Update existing with merged value
        success = self.store.update(conflict.existing.id, {
            "value": merged,
            "confidence": max(conflict.existing.confidence, new_memory.confidence)
        })

        if success:
            return True, f"Merged values: {merged}"
        return False, "Failed to merge"

    def _resolve_accumulate(
        self,
        conflict: ConflictInfo,
        new_memory: MemoryItem
    ) -> Tuple[bool, str]:
        """Resolve by keeping both (just save new one)."""
        # For exact duplicates, don't save
        if conflict.conflict_type == "exact_duplicate":
            return False, "Duplicate already exists"

        # Save new memory alongside existing
        success = self.store.save(new_memory)
        if success:
            return True, f"Added new memory alongside existing"
        return False, "Failed to save new memory"

    def resolve_all(
        self,
        new_memory: MemoryItem,
        conflicts: List[ConflictInfo]
    ) -> Tuple[bool, List[str]]:
        """
        Resolve all conflicts for a new memory.

        Returns:
            (success, list of resolution messages)
        """
        messages = []
        final_success = True

        for conflict in conflicts:
            success, message = self.resolve(conflict, new_memory)
            messages.append(message)
            if not success:
                final_success = False

        return final_success, messages


def resolve_memory_conflict(
    store: MemoryStoreV2,
    new_memory: MemoryItem
) -> Tuple[bool, str]:
    """
    Convenience function to detect and resolve conflicts for a new memory.

    Returns:
        (success, message)
    """
    resolver = ConflictResolver(store)
    conflicts = resolver.detect_conflicts(new_memory)

    if not conflicts:
        # No conflicts, save directly
        success = store.save(new_memory)
        return success, "Saved without conflicts"

    # Handle first conflict (most significant)
    conflict = conflicts[0]

    # Exact duplicates - don't save
    if conflict.conflict_type == "exact_duplicate":
        return False, "Memory already exists"

    # Resolve and save
    return resolver.resolve(conflict, new_memory)

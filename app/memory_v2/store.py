"""
Memory V2 Store
===============
Firestore storage with canonical + provisional separation.

Structure:
    /users/{userId}/memory_v2/{memoryId}     # All memories
    /users/{userId}/memory_events/{eventId}  # Audit trail

Security:
    - Only accepts memories with evidence.source == "user"
    - Rejects memories containing injection patterns
    - Validates constraint keys against allowed keyspace
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..config import db
from .schema import MemoryItem, MemoryType, MemoryStatus, ALLOWED_CONSTRAINT_KEYS
from .embeddings import embed_text, embedding_to_list

# Background thread pool for non-blocking embedding generation
_embedding_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem_embed")

logger = logging.getLogger(__name__)


# Patterns that indicate prompt injection attempts
INJECTION_PATTERNS = [
    r"ignore\s+(previous|prior|above|all)\s+(instructions?|prompts?|rules?)",
    r"system\s+(prompt|message|instruction)",
    r"act\s+as\s+(a|an|if)",
    r"you\s+are\s+now",
    r"pretend\s+(to\s+be|you\s+are)",
    r"forget\s+(everything|all|your)",
    r"disregard\s+(the|all|previous)",
    r"override\s+(the|your|all)",
    r"new\s+instructions?",
    r"admin\s+mode",
    r"developer\s+mode",
    r"jailbreak",
    r"dan\s+mode",
    r"\[system\]",
    r"\[admin\]",
    # Additional obfuscation-resistant patterns
    r"(?:i|1)gnore\s+(?:prev|pr1or|ab0ve)",  # Leet-speak variants
    r"(?:s|5)ystem\s*(?:p|P)rompt",
    r"</?\s*(?:system|instruction|prompt)\s*>",  # XML-style tags
    r"```\s*system",  # Code-block wrapped injections
    r"(?:begin|start)\s+(?:new\s+)?(?:role|persona|character)",
    r"(?:as\s+)?(?:a\s+)?(?:helpful|obedient)\s+(?:ai|assistant)\s+(?:you\s+)?(?:must|should|will)",
    r"(?:do\s+not|don'?t)\s+(?:follow|obey|listen\s+to)\s+(?:your|the)\s+(?:previous|original)",
]


def is_injection_attempt(text: str) -> bool:
    """Check if text contains common prompt injection patterns."""
    if not text:
        return False
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def sanitize_memory_value(value: Any) -> Any:
    """Sanitize a memory value, removing potential injection content."""
    if isinstance(value, str):
        # Check for injection
        if is_injection_attempt(value):
            logger.warning(f"Rejected memory value containing injection pattern")
            return None
        # Remove any XML/HTML-like tags that could confuse the prompt
        value = re.sub(r'<[^>]+>', '', value)
        return value.strip()
    elif isinstance(value, list):
        return [sanitize_memory_value(v) for v in value if sanitize_memory_value(v) is not None]
    elif isinstance(value, dict):
        return {k: sanitize_memory_value(v) for k, v in value.items() if sanitize_memory_value(v) is not None}
    return value


class MemoryStoreV2:
    """
    Firestore-based memory store with full CRUD and audit logging.

    All writes go through _commit_write() which enforces:
    - Source validation (must be "user")
    - Invariant checks
    - Sanitizer checks
    - Constraint keyspace validation

    Example:
        store = MemoryStoreV2(user_id)
        store.save(memory_item)
        memories = store.get_by_type(MemoryType.PROFILE)
        store.delete(memory_id)
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._cache: Optional[Dict[str, MemoryItem]] = None

    def _commit_write(
        self,
        memory: MemoryItem,
        reason: str,
        bypass_source_check: bool = False
    ) -> Tuple[bool, str]:
        """
        Centralized write gate. ALL writes must go through this.

        Enforces:
        - Source validation
        - Invariant checks
        - Sanitizer checks
        - Constraint keyspace validation

        Returns:
            (success, error_message)
        """
        # 1. Source validation
        if not bypass_source_check and memory.evidence.source != "user":
            msg = f"Rejected: non-user source '{memory.evidence.source}'"
            logger.warning(msg)
            return False, msg

        # 2. Constraint keyspace validation (BEFORE conflict resolution)
        if memory.type == MemoryType.CONSTRAINT:
            # Allow keys in whitelist OR keys starting with "no_" prefix (for dynamic constraints)
            is_allowed = memory.key in ALLOWED_CONSTRAINT_KEYS or memory.key.startswith("no_")
            if not is_allowed:
                msg = f"Rejected: invalid constraint key '{memory.key}'"
                logger.warning(msg)
                return False, msg

        # 3. Sanitizer check on value
        sanitized_value = sanitize_memory_value(memory.value)
        if sanitized_value is None:
            msg = "Rejected: injection pattern in value"
            logger.warning(msg)
            return False, msg
        memory.value = sanitized_value

        # 4. Sanitizer check on evidence quote
        if memory.evidence.quote and is_injection_attempt(memory.evidence.quote):
            msg = "Rejected: injection pattern in evidence quote"
            logger.warning(msg)
            return False, msg

        # 5. Invariant validation
        is_valid, error_msg = memory.validate_invariants()
        if not is_valid:
            msg = f"Rejected: invariant violation - {error_msg}"
            logger.error(msg)
            return False, msg

        # 6. Commit to Firestore (don't block on embedding generation)
        try:
            memory.user_id = self.user_id
            memory.lifecycle.updated_at = datetime.utcnow()
            self._get_memory_ref().document(memory.id).set(memory.to_dict())
            self._cache = None
        except Exception as e:
            msg = f"Firestore write failed: {e}"
            logger.error(msg)
            return False, msg

        # 7. Generate embedding in background if not already present
        if memory.embedding is None:
            self._generate_embedding_async(memory.id, memory.get_embedding_text())

        return True, ""

    def _generate_embedding_async(self, memory_id: str, embedding_text: str):
        """Generate and store embedding in a background thread."""
        def _do_embed():
            try:
                embedding = embed_text(embedding_text)
                if embedding is not None:
                    self._get_memory_ref().document(memory_id).update({
                        "embedding": embedding_to_list(embedding)
                    })
                    logger.debug(f"Background embedding generated for {memory_id}")
            except Exception as e:
                logger.warning(f"Background embedding failed for {memory_id}: {e}")

        _embedding_executor.submit(_do_embed)

    def quarantine_memory(self, memory_id: str, reason: str) -> bool:
        """
        Quarantine a memory that failed sanitizer check.

        Marks as QUARANTINED and creates audit event.
        Used when retrieval finds poisoned data.
        """
        try:
            self._get_memory_ref().document(memory_id).update({
                "status": MemoryStatus.QUARANTINED.value,
                "lifecycle.updated_at": datetime.utcnow().isoformat()
            })

            self._log_event("quarantine", memory_id, {
                "reason": reason,
                "action": "memory_quarantined"
            })

            logger.warning(f"Quarantined memory {memory_id}: {reason}")
            self._cache = None
            return True

        except Exception as e:
            logger.error(f"Failed to quarantine memory: {e}")
            return False

    def _get_memory_ref(self):
        """Get reference to user's memory_v2 collection."""
        return db.collection('users').document(self.user_id).collection('memory_v2')

    def _get_events_ref(self):
        """Get reference to user's memory_events collection."""
        return db.collection('users').document(self.user_id).collection('memory_events')

    def _log_event(self, action: str, memory_id: str, details: Dict[str, Any] = None):
        """Log memory event for audit trail."""
        try:
            self._get_events_ref().add({
                "action": action,
                "memory_id": memory_id,
                "timestamp": datetime.utcnow().isoformat(),
                "details": details or {}
            })
        except Exception as e:
            logger.debug(f"Failed to log memory event: {e}")

    def save(self, memory: MemoryItem, log_event: bool = True, bypass_source_check: bool = False) -> bool:
        """
        Save a memory item to Firestore.

        All validation goes through _commit_write() centralized gate.

        Args:
            memory: MemoryItem to save
            log_event: Whether to log this action
            bypass_source_check: Only True for admin/system migrations

        Returns:
            True if saved successfully
        """
        # Use centralized write gate
        success, error_msg = self._commit_write(memory, "save", bypass_source_check)

        if not success:
            return False

        if log_event:
            self._log_event("save", memory.id, {
                "type": memory.type.value,
                "key": memory.key,
                "confidence": memory.confidence,
                "status": memory.status.value,
                "source": memory.evidence.source
            })

        logger.info(f"Saved memory {memory.id}: {memory.type.value}/{memory.key}")
        return True

    def get(self, memory_id: str) -> Optional[MemoryItem]:
        """Get a single memory by ID."""
        try:
            doc = self._get_memory_ref().document(memory_id).get()
            if doc.exists:
                return MemoryItem.from_dict(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            return None

    def get_all(self, include_expired: bool = False) -> List[MemoryItem]:
        """
        Get all memories for the user.

        Args:
            include_expired: Whether to include expired/superseded memories

        Returns:
            List of MemoryItem
        """
        try:
            query = self._get_memory_ref()

            if not include_expired:
                query = query.where("status", "in", ["active", "provisional"])

            docs = query.stream()
            memories = []

            for doc in docs:
                memory = MemoryItem.from_dict(doc.to_dict())
                if include_expired or memory.is_valid():
                    memories.append(memory)

            return memories

        except Exception as e:
            logger.error(f"Failed to get all memories: {e}")
            return []

    def get_by_type(
        self,
        memory_type: MemoryType,
        status: Optional[MemoryStatus] = None,
        limit: int = 50
    ) -> List[MemoryItem]:
        """
        Get memories by type.

        Args:
            memory_type: Type to filter by
            status: Optional status filter
            limit: Max results

        Returns:
            List of MemoryItem
        """
        try:
            query = self._get_memory_ref().where("type", "==", memory_type.value)

            if status:
                query = query.where("status", "==", status.value)
            else:
                query = query.where("status", "in", ["active", "provisional"])

            query = query.limit(limit)
            docs = query.stream()

            memories = []
            for doc in docs:
                memory = MemoryItem.from_dict(doc.to_dict())
                if memory.is_valid():
                    memories.append(memory)

            return memories

        except Exception as e:
            logger.error(f"Failed to get memories by type: {e}")
            return []

    def get_by_key(self, key: str) -> Optional[MemoryItem]:
        """Get memory by key (for deduplication)."""
        try:
            docs = (
                self._get_memory_ref()
                .where("key", "==", key)
                .where("status", "in", ["active", "provisional"])
                .limit(1)
                .stream()
            )

            for doc in docs:
                return MemoryItem.from_dict(doc.to_dict())

            return None

        except Exception as e:
            logger.error(f"Failed to get memory by key: {e}")
            return None

    def update(self, memory_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update specific fields of a memory.

        Args:
            memory_id: ID of memory to update
            updates: Fields to update

        Returns:
            True if updated successfully
        """
        try:
            updates["lifecycle.updated_at"] = datetime.utcnow().isoformat()
            self._get_memory_ref().document(memory_id).update(updates)

            self._log_event("update", memory_id, updates)
            self._cache = None

            return True

        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            return False

    def mark_used(self, memory_id: str) -> bool:
        """Mark a memory as used (update last_used_at and use_count)."""
        try:
            from google.cloud.firestore_v1 import Increment

            self._get_memory_ref().document(memory_id).update({
                "lifecycle.last_used_at": datetime.utcnow().isoformat(),
                "lifecycle.use_count": Increment(1)
            })
            return True

        except Exception as e:
            logger.error(f"Failed to mark memory used: {e}")
            return False

    def supersede(self, old_id: str, new_memory: MemoryItem) -> bool:
        """
        Supersede an old memory with a new one.

        Creates bidirectional links for traceability:
        - old_memory.superseded_by = new_memory.id
        - new_memory.supersedes = old_memory.id

        Args:
            old_id: ID of memory being superseded
            new_memory: New memory replacing it

        Returns:
            True if successful
        """
        try:
            # Cycle detection: walk the supersedes chain to ensure no loops
            visited = {new_memory.id}
            current_id = old_id
            max_depth = 20
            for _ in range(max_depth):
                if current_id in visited:
                    logger.error(f"Supersede cycle detected: {current_id} already in chain")
                    return False
                visited.add(current_id)
                predecessor = self.get(current_id)
                if not predecessor or not predecessor.supersedes:
                    break
                current_id = predecessor.supersedes

            # Set supersedes link on new memory for graph traceability
            new_memory.supersedes = old_id

            # Mark old as superseded
            self._get_memory_ref().document(old_id).update({
                "status": MemoryStatus.SUPERSEDED.value,
                "superseded_by": new_memory.id,
                "lifecycle.updated_at": datetime.utcnow().isoformat()
            })

            # Save new memory (with supersedes link)
            self.save(new_memory)

            self._log_event("supersede", old_id, {
                "new_id": new_memory.id,
                "old_id": old_id,
                "reason": "conflict_resolution"
            })

            return True

        except Exception as e:
            logger.error(f"Failed to supersede memory: {e}")
            return False

    def delete(self, memory_id: str, hard_delete: bool = False) -> bool:
        """
        Delete a memory.

        Args:
            memory_id: ID of memory to delete
            hard_delete: If True, permanently remove. If False, mark as deleted.

        Returns:
            True if deleted successfully
        """
        try:
            if hard_delete:
                self._get_memory_ref().document(memory_id).delete()
            else:
                self._get_memory_ref().document(memory_id).update({
                    "status": MemoryStatus.DELETED.value,
                    "lifecycle.updated_at": datetime.utcnow().isoformat()
                })

            self._log_event("delete", memory_id, {"hard": hard_delete})
            self._cache = None

            return True

        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            return False

    def clear_all(self, hard_delete: bool = False) -> int:
        """
        Clear all memories for the user.

        Args:
            hard_delete: If True, permanently remove all.

        Returns:
            Number of memories cleared
        """
        try:
            docs = self._get_memory_ref().stream()
            count = 0

            for doc in docs:
                if hard_delete:
                    doc.reference.delete()
                else:
                    doc.reference.update({
                        "status": MemoryStatus.DELETED.value
                    })
                count += 1

            self._log_event("clear_all", "all", {"count": count, "hard": hard_delete})
            self._cache = None

            return count

        except Exception as e:
            logger.error(f"Failed to clear memories: {e}")
            return 0

    def cleanup_expired(self) -> int:
        """
        Clean up expired memories.

        Returns:
            Number of memories cleaned up
        """
        try:
            now = datetime.utcnow().isoformat()
            docs = (
                self._get_memory_ref()
                .where("lifecycle.expires_at", "<=", now)
                .where("status", "in", ["active", "provisional"])
                .stream()
            )

            count = 0
            for doc in docs:
                doc.reference.update({
                    "status": MemoryStatus.EXPIRED.value
                })
                count += 1

            if count > 0:
                logger.info(f"Cleaned up {count} expired memories for user {self.user_id}")

            return count

        except Exception as e:
            logger.error(f"Failed to cleanup expired memories: {e}")
            return 0

    def promote_provisional(self, memory_id: str) -> bool:
        """
        Promote a provisional memory to active (after confirmation/repeat).

        INVARIANT: Updates same document, preserves all links (supersedes, evidence).
        Does NOT create a new memory - just changes status.

        Args:
            memory_id: ID of provisional memory

        Returns:
            True if promoted successfully
        """
        try:
            memory = self.get(memory_id)
            if memory and memory.status == MemoryStatus.PROVISIONAL:
                now = datetime.utcnow().isoformat()
                self._get_memory_ref().document(memory_id).update({
                    "status": MemoryStatus.ACTIVE.value,
                    "lifecycle.expires_at": None,  # Remove TTL
                    "lifecycle.updated_at": now,
                    "lifecycle.last_confirmed_at": now  # Record when confirmed
                })

                self._log_event("promote", memory_id, {
                    "from": "provisional",
                    "to": "active",
                    "preserved_supersedes": memory.supersedes,
                    "preserved_evidence": bool(memory.evidence.quote)
                })
                self._cache = None
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to promote memory: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics for the user."""
        try:
            all_memories = self.get_all(include_expired=True)

            stats = {
                "total": len(all_memories),
                "by_type": {},
                "by_status": {},
                "active": 0,
                "provisional": 0,
                "pinned": 0
            }

            for memory in all_memories:
                # By type
                type_key = memory.type.value
                stats["by_type"][type_key] = stats["by_type"].get(type_key, 0) + 1

                # By status
                status_key = memory.status.value
                stats["by_status"][status_key] = stats["by_status"].get(status_key, 0) + 1

                # Counts
                if memory.status == MemoryStatus.ACTIVE:
                    stats["active"] += 1
                if memory.status == MemoryStatus.PROVISIONAL:
                    stats["provisional"] += 1
                if memory.pinned:
                    stats["pinned"] += 1

            return stats

        except Exception as e:
            logger.error(f"Failed to get memory stats: {e}")
            return {"error": str(e)}

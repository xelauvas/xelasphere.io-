"""
Memory V2 Background Jobs
=========================
Periodic maintenance jobs for memory hygiene.

Jobs:
- Backfill quarantine scan: Detect and quarantine dormant poisoned data
- Scheduled cleanup: TTL expiration, stale marking, promotion
- Stats collection: Memory usage analytics
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from .schema import MemoryItem, MemoryStatus
from .store import MemoryStoreV2, is_injection_attempt
from .decay import DecayManager
from .safeguards import create_audit_entry, ReasonCode

logger = logging.getLogger(__name__)


def run_quarantine_scan(user_id: str, batch_size: int = 100) -> Dict[str, int]:
    """
    Scan existing memories for injection patterns and quarantine.

    This catches dormant bad data that slipped through before
    the sanitizer was added. Should run periodically (e.g., weekly).

    Args:
        user_id: User to scan
        batch_size: Max memories to scan per run

    Returns:
        Stats dict with counts
    """
    stats = {
        "scanned": 0,
        "quarantined": 0,
        "errors": 0,
    }

    store = MemoryStoreV2(user_id)

    try:
        # Get all memories including expired (catch everything)
        all_memories = store.get_all(include_expired=True)
        stats["scanned"] = min(len(all_memories), batch_size)

        for memory in all_memories[:batch_size]:
            # Skip already quarantined
            if memory.status == MemoryStatus.QUARANTINED:
                continue

            # Check value for injection
            value_str = str(memory.value)
            if is_injection_attempt(value_str):
                if store.quarantine_memory(memory.id, "backfill_scan_injection"):
                    stats["quarantined"] += 1
                    logger.warning(
                        f"Backfill quarantined memory {memory.id} "
                        f"(type={memory.type.value}, key={memory.key})"
                    )
                continue

            # Check evidence quote for injection
            if memory.evidence.quote and is_injection_attempt(memory.evidence.quote):
                if store.quarantine_memory(memory.id, "backfill_scan_evidence_injection"):
                    stats["quarantined"] += 1
                    logger.warning(
                        f"Backfill quarantined memory {memory.id} "
                        f"(injection in evidence quote)"
                    )

    except Exception as e:
        logger.error(f"Quarantine scan failed for {user_id}: {e}")
        stats["errors"] += 1

    logger.info(f"Quarantine scan for {user_id}: {stats}")
    return stats


def run_full_maintenance(user_id: str) -> Dict[str, Dict]:
    """
    Run full maintenance cycle for a user.

    Combines:
    - Quarantine scan
    - TTL cleanup
    - Stale marking
    - Promotion check

    Args:
        user_id: User to maintain

    Returns:
        Combined stats from all operations
    """
    results = {}

    # 1. Quarantine scan first (safety)
    results["quarantine_scan"] = run_quarantine_scan(user_id)

    # 2. Regular cleanup (decay, TTL, etc.)
    store = MemoryStoreV2(user_id)
    decay_manager = DecayManager(store)
    results["cleanup"] = decay_manager.run_cleanup()

    return results


def run_batch_maintenance(user_ids: List[str]) -> Dict[str, Dict]:
    """
    Run maintenance for multiple users.

    Args:
        user_ids: List of user IDs to process

    Returns:
        Results keyed by user_id
    """
    results = {}

    for user_id in user_ids:
        try:
            results[user_id] = run_full_maintenance(user_id)
        except Exception as e:
            logger.error(f"Maintenance failed for {user_id}: {e}")
            results[user_id] = {"error": str(e)}

    return results


def get_memory_health_stats(user_id: str) -> Dict[str, any]:
    """
    Get memory health statistics for monitoring.

    Args:
        user_id: User to check

    Returns:
        Health stats dict
    """
    store = MemoryStoreV2(user_id)

    try:
        all_memories = store.get_all(include_expired=True)

        stats = {
            "total": len(all_memories),
            "by_status": {},
            "by_type": {},
            "low_confidence_count": 0,
            "stale_count": 0,
            "avg_confidence": 0.0,
            "oldest_memory_age_days": 0,
            "pinned_count": 0,
        }

        now = datetime.utcnow()
        confidence_sum = 0.0
        oldest_age = 0

        for memory in all_memories:
            # By status
            status = memory.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By type
            mtype = memory.type.value
            stats["by_type"][mtype] = stats["by_type"].get(mtype, 0) + 1

            # Confidence
            confidence_sum += memory.confidence
            if memory.confidence < 0.55:
                stats["low_confidence_count"] += 1

            # Age
            age_days = (now - memory.lifecycle.created_at).days
            if age_days > oldest_age:
                oldest_age = age_days

            # Stale check
            last_used = memory.lifecycle.last_used_at or memory.lifecycle.created_at
            days_since_use = (now - last_used).days
            if days_since_use > 60:
                stats["stale_count"] += 1

            # Pinned
            if memory.pinned:
                stats["pinned_count"] += 1

        if all_memories:
            stats["avg_confidence"] = confidence_sum / len(all_memories)

        stats["oldest_memory_age_days"] = oldest_age

        return stats

    except Exception as e:
        logger.error(f"Failed to get health stats for {user_id}: {e}")
        return {"error": str(e)}

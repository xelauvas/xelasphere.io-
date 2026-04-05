"""Dream System: Background memory consolidation.

Public API: maybe_consolidate() — call after each chat response in a background thread.
"""
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from app.memory_v2.dream.config import DreamConfig, get_dream_config
from app.memory_v2.dream.report import ConsolidationReport, DreamAction, DreamLog
from app.memory_v2.dream import gates
from app.memory_v2.dream import phase1
from app.memory_v2.dream import phase2
from app.memory_v2.dream import executor
from app.memory_v2.store import MemoryStoreV2

logger = logging.getLogger(__name__)


def maybe_consolidate(
    db,
    user_id: str,
    user_prefs: Optional[Dict[str, Any]] = None,
) -> bool:
    """Main entry point: check gates and run consolidation if due.
    Call this in a background thread after each chat response.
    Returns True if consolidation ran, False if skipped.
    """
    config = get_dream_config(user_prefs)

    if not config.enabled:
        return False

    # Gate 1: Time gate
    dream_state = gates.get_dream_state(db, user_id)
    last_at = dream_state.get('last_consolidated_at')
    if not gates.check_time_gate(last_at, config):
        return False

    # Gate 2: Session count gate
    if not gates.check_session_count_gate(db, user_id, last_at, config):
        return False

    # Gate 3: Lock gate
    if not gates.try_acquire_lock(db, user_id, config=config):
        return False

    # All gates passed
    try:
        return _run_consolidation(db, user_id, config)
    except Exception as e:
        logger.error(f"[dream] Consolidation failed for {user_id}: {e}")
        gates.release_lock(db, user_id)
        return False


def _run_consolidation(db, user_id: str, config: DreamConfig) -> bool:
    """Execute the full consolidation pipeline."""
    started_at = datetime.utcnow()
    dream_log = DreamLog(user_id=user_id, started_at=started_at)

    store = MemoryStoreV2(user_id)
    all_memories = store.get_all(include_expired=False)

    # Pre-check: enough memories?
    active_count = sum(1 for m in all_memories if m.status.value in ('active', 'provisional'))
    if active_count < config.min_active_memories:
        gates.release_lock(db, user_id)
        return False

    # Phase 1
    report = phase1.run_phase1(all_memories, config)
    p1_actions = executor.execute_phase1_actions(
        store=store, pruned_ids=report.pruned, merged_pairs=report.merged,
    )
    dream_log.phase1_actions = p1_actions

    # Phase 2 (conditional)
    p2_actions = []
    if phase2.should_run_phase2(report, config.pattern_synthesis_threshold):
        memories_by_id = {m.id: m for m in report.all_active}
        p2_decisions = phase2.run_phase2(
            report=report, memories_by_id=memories_by_id,
            config_token_budget=config.kimi_token_budget,
            config_max_syntheses=config.max_syntheses_per_cycle,
        )
        if p2_decisions is not None:
            dream_log.kimi_raw_response = str(p2_decisions)
            p2_actions = executor.execute_phase2_actions(
                store=store, decisions=p2_decisions, user_id=user_id,
            )
        else:
            dream_log.errors.append('Phase 2 Kimi call failed')

    dream_log.phase2_actions = p2_actions
    dream_log.completed_at = datetime.utcnow()

    # Save log
    executor.save_dream_log(db, dream_log)

    # Build notification
    summary = dream_log.summary()
    notification = {k: v for k, v in summary.items() if v > 0 and k != 'total_actions'}

    # Update state
    gates.update_dream_state(db, user_id, {
        'last_consolidated_at': datetime.utcnow(),
        'pending_notification': notification if notification else None,
    })

    gates.release_lock(db, user_id)

    # Trigger smart suggestions refresh (fire-and-forget, non-blocking)
    try:
        from app.services.smart_suggestions import refresh_pool_background
        import threading
        threading.Thread(
            target=refresh_pool_background,
            args=(db, user_id, "dream"),
            daemon=True,
        ).start()
    except Exception as e:
        logger.debug(f"[dream] Smart suggestions refresh skipped: {e}")

    logger.info(f"[dream] Consolidation complete for {user_id}: {summary}")
    return True

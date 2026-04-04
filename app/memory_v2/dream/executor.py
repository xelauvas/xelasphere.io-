"""Dream executor: batch writes, snapshots, dream log persistence, undo."""
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional

from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus, create_memory
from app.memory_v2.dream.report import DreamAction, DreamLog

logger = logging.getLogger(__name__)


def _snapshot(mem: Optional[MemoryItem]) -> Optional[Dict[str, Any]]:
    if mem is None:
        return None
    return {
        'id': mem.id,
        'status': mem.status.value if hasattr(mem.status, 'value') else str(mem.status),
        'key': mem.key, 'value': mem.value, 'confidence': mem.confidence,
        'type': mem.type.value if hasattr(mem.type, 'value') else str(mem.type),
    }


def execute_phase1_actions(store, pruned_ids: List[str], merged_pairs: List[Tuple[str, str]]) -> List[DreamAction]:
    actions = []
    for mid in pruned_ids:
        mem = store.get(mid)
        snapshot = _snapshot(mem)
        try:
            store.update(mid, {'status': MemoryStatus.EXPIRED.value})
            actions.append(DreamAction(action_type='prune', memory_id=mid, reason='Expired TTL or stale provisional', phase=1, before_snapshot=snapshot))
        except Exception as e:
            logger.warning(f"[dream] Failed to prune {mid}: {e}")
    for winner_id, loser_id in merged_pairs:
        winner = store.get(winner_id)
        loser = store.get(loser_id)
        snapshot = _snapshot(loser)
        try:
            if winner and loser:
                combined_use = (winner.lifecycle.use_count or 0) + (loser.lifecycle.use_count or 0)
                higher_repeated = max(winner.lifecycle.repeated_by_user or 0, loser.lifecycle.repeated_by_user or 0)
                store.update(winner_id, {'lifecycle.use_count': combined_use, 'lifecycle.repeated_by_user': higher_repeated, 'lifecycle.updated_at': datetime.utcnow()})
            store.update(loser_id, {'status': MemoryStatus.SUPERSEDED.value, 'superseded_by': winner_id})
            actions.append(DreamAction(action_type='merge', memory_id=winner_id, reason='Merged near-duplicate', phase=1, before_snapshot=snapshot, related_ids=[loser_id]))
        except Exception as e:
            logger.warning(f"[dream] Failed to merge {winner_id}/{loser_id}: {e}")
    return actions


def execute_phase2_actions(store, decisions: Dict[str, List[Dict[str, Any]]], user_id: str) -> List[DreamAction]:
    actions = []
    for resolution in decisions.get('resolve_conflicts', []):
        keep_id = resolution.get('keep_id')
        for sid in resolution.get('supersede_ids', []):
            mem = store.get(sid)
            snapshot = _snapshot(mem)
            try:
                store.update(sid, {'status': MemoryStatus.SUPERSEDED.value, 'superseded_by': keep_id})
                actions.append(DreamAction(action_type='supersede', memory_id=sid, reason=resolution.get('reason', 'conflict resolution'), phase=2, before_snapshot=snapshot, related_ids=[keep_id]))
            except Exception as e:
                logger.warning(f"[dream] Failed to supersede {sid}: {e}")
    for prune_decision in decisions.get('prune', []):
        mid = prune_decision.get('memory_id')
        mem = store.get(mid)
        snapshot = _snapshot(mem)
        try:
            store.update(mid, {'status': MemoryStatus.EXPIRED.value})
            actions.append(DreamAction(action_type='prune', memory_id=mid, reason=prune_decision.get('reason', 'low value'), phase=2, before_snapshot=snapshot))
        except Exception as e:
            logger.warning(f"[dream] Failed to prune {mid}: {e}")
    for promote_decision in decisions.get('promote', []):
        mid = promote_decision.get('memory_id')
        mem = store.get(mid)
        snapshot = _snapshot(mem)
        try:
            store.promote_provisional(mid)
            actions.append(DreamAction(action_type='promote', memory_id=mid, reason=promote_decision.get('reason', 'corroborated'), phase=2, before_snapshot=snapshot))
        except Exception as e:
            logger.warning(f"[dream] Failed to promote {mid}: {e}")
    for synth in decisions.get('synthesize', []):
        try:
            type_map = {'profile': MemoryType.PROFILE, 'preference': MemoryType.PREFERENCE, 'project': MemoryType.PROJECT, 'constraint': MemoryType.CONSTRAINT}
            mem_type = type_map.get(synth.get('type', 'preference'), MemoryType.PREFERENCE)
            confidence = min(0.8, float(synth.get('confidence', 0.7)))
            new_mem = create_memory(user_id=user_id, memory_type=mem_type, key=synth.get('key', 'unknown'), value=synth.get('value', ''), confidence=confidence, quote='Synthesized by dream consolidation', provisional=True)
            new_mem.evidence.source = 'inferred'
            new_mem.evidence.extraction_method = 'dream_synthesis'
            store.save(new_mem, bypass_source_check=True)
            actions.append(DreamAction(action_type='synthesize', memory_id=new_mem.id, reason=f"Pattern from {len(synth.get('evidence_ids', []))} memories", phase=2, related_ids=synth.get('evidence_ids', [])))
        except Exception as e:
            logger.warning(f"[dream] Failed to synthesize: {e}")
    return actions


def save_dream_log(db, dream_log: DreamLog) -> Optional[str]:
    try:
        log_ref = db.collection('users').document(dream_log.user_id).collection('dream_logs').document()
        log_ref.set(dream_log.to_dict())
        return log_ref.id
    except Exception as e:
        logger.error(f"[dream] Failed to save dream log: {e}")
        return None


def undo_dream_log(db, store, dream_log: DreamLog) -> bool:
    all_actions = dream_log.phase1_actions + dream_log.phase2_actions
    restored = 0
    for action in reversed(all_actions):
        if action.before_snapshot is None:
            if action.action_type == 'synthesize':
                try:
                    store.delete(action.memory_id, hard_delete=True)
                    restored += 1
                except Exception as e:
                    logger.warning(f"[dream] Undo: failed to delete synthesized {action.memory_id}: {e}")
            continue
        snapshot = action.before_snapshot
        try:
            store.update(action.memory_id, {'status': snapshot.get('status', 'active'), 'superseded_by': None})
            restored += 1
        except Exception as e:
            logger.warning(f"[dream] Undo: failed to restore {action.memory_id}: {e}")
    return restored > 0

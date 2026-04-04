"""Phase 1: Algorithmic memory consolidation — no LLM needed."""
import logging
from datetime import datetime, timedelta
from typing import List, Tuple

import numpy as np

from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
from app.memory_v2.dream.config import DreamConfig
from app.memory_v2.dream.report import ConsolidationReport

logger = logging.getLogger(__name__)

OVERWRITE_TYPES = {MemoryType.PROFILE, MemoryType.TEMPORARY}


def prune_expired(memories: List[MemoryItem]) -> List[str]:
    """Step 1: Find memories that should be expired."""
    now = datetime.utcnow()
    pruned_ids = []
    for mem in memories:
        if mem.lifecycle.expires_at and mem.lifecycle.expires_at < now:
            pruned_ids.append(mem.id)
            continue
        if mem.status == MemoryStatus.PROVISIONAL:
            age_days = (now - mem.lifecycle.created_at).days
            if age_days > 14 and mem.lifecycle.repeated_by_user < 2 and not mem.lifecycle.explicit_confirmed:
                pruned_ids.append(mem.id)
    return pruned_ids


def merge_near_duplicates(memories: List[MemoryItem], threshold: float = 0.92) -> List[Tuple[str, str]]:
    """Step 2: Find near-duplicate memory pairs to merge. Returns (winner_id, loser_id) tuples."""
    merged = []
    consumed = set()
    by_type = {}
    for mem in memories:
        if mem.id in consumed or not mem.embedding:
            continue
        by_type.setdefault(mem.type, []).append(mem)

    for mem_type, group in by_type.items():
        for i, a in enumerate(group):
            if a.id in consumed:
                continue
            for b in group[i + 1:]:
                if b.id in consumed:
                    continue
                if a.key != b.key:
                    continue
                emb_a = np.array(a.embedding, dtype=np.float32)
                emb_b = np.array(b.embedding, dtype=np.float32)
                norm_a = np.linalg.norm(emb_a)
                norm_b = np.linalg.norm(emb_b)
                if norm_a == 0 or norm_b == 0:
                    continue
                similarity = float(np.dot(emb_a, emb_b) / (norm_a * norm_b))
                if similarity >= threshold:
                    if a.confidence >= b.confidence:
                        winner, loser = a, b
                    else:
                        winner, loser = b, a
                    merged.append((winner.id, loser.id))
                    consumed.add(loser.id)
    return merged


def detect_conflicts(memories: List[MemoryItem]) -> List[List[str]]:
    """Step 3: Find groups of memories with conflicting values."""
    conflicts = []
    by_key = {}
    for mem in memories:
        if mem.status != MemoryStatus.ACTIVE:
            continue
        by_key.setdefault(mem.key, []).append(mem)

    for key, group in by_key.items():
        if len(group) < 2:
            continue
        overwrite_mems = [m for m in group if m.type in OVERWRITE_TYPES]
        if len(overwrite_mems) >= 2:
            values = {str(m.value) for m in overwrite_mems}
            if len(values) > 1:
                conflicts.append([m.id for m in overwrite_mems])
            continue
        if group[0].type == MemoryType.PREFERENCE:
            values = {str(m.value) for m in group}
            if len(values) > 1:
                conflicts.append([m.id for m in group])
    return conflicts


def flag_low_value(memories: List[MemoryItem], unused_days: int = 60, confidence_floor: float = 0.5) -> List[str]:
    """Step 4: Flag low-value memories for Phase 2 review."""
    now = datetime.utcnow()
    flagged = []
    for mem in memories:
        if mem.lifecycle.use_count == 0:
            last_touched = mem.lifecycle.last_used_at or mem.lifecycle.created_at
            if (now - last_touched).days > unused_days:
                flagged.append(mem.id)
                continue
        if mem.confidence < confidence_floor and mem.status == MemoryStatus.PROVISIONAL:
            flagged.append(mem.id)
    return flagged


def run_phase1(memories: List[MemoryItem], config: DreamConfig) -> ConsolidationReport:
    """Run the full Phase 1 algorithmic consolidation pipeline."""
    report = ConsolidationReport()
    report.pruned = prune_expired(memories)
    pruned_set = set(report.pruned)
    remaining = [m for m in memories if m.id not in pruned_set]
    report.merged = merge_near_duplicates(remaining, threshold=config.embedding_similarity_threshold)
    merged_losers = {loser for _, loser in report.merged}
    remaining = [m for m in remaining if m.id not in merged_losers]
    report.flagged_conflicts = detect_conflicts(remaining)
    report.flagged_low_value = flag_low_value(remaining, unused_days=config.low_value_unused_days, confidence_floor=config.low_value_confidence_floor)
    report.all_active = [m for m in remaining if m.status in (MemoryStatus.ACTIVE, MemoryStatus.PROVISIONAL)]
    logger.info(f"[dream] Phase 1: pruned={len(report.pruned)}, merged={len(report.merged)}, conflicts={len(report.flagged_conflicts)}, low_value={len(report.flagged_low_value)}, remaining={len(report.all_active)}")
    return report

"""Dataclasses for dream consolidation reports and logs."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class ConsolidationReport:
    """Output of Phase 1 algorithmic consolidation."""
    pruned: List[str] = field(default_factory=list)
    merged: List[Tuple[str, str]] = field(default_factory=list)
    flagged_conflicts: List[List[str]] = field(default_factory=list)
    flagged_low_value: List[str] = field(default_factory=list)
    all_active: List[Any] = field(default_factory=list)


@dataclass
class DreamAction:
    """A single action taken during consolidation."""
    action_type: str
    memory_id: str
    reason: str
    phase: int
    before_snapshot: Optional[Dict[str, Any]] = None
    related_ids: List[str] = field(default_factory=list)


@dataclass
class DreamLog:
    """Full log of a dream consolidation cycle."""
    user_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    phase1_actions: List[DreamAction] = field(default_factory=list)
    phase2_actions: List[DreamAction] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    kimi_raw_response: Optional[str] = None

    def summary(self) -> Dict[str, int]:
        all_actions = self.phase1_actions + self.phase2_actions
        counts: Dict[str, int] = {}
        for action in all_actions:
            display_key = {
                'prune': 'pruned', 'merge': 'merged', 'supersede': 'superseded',
                'promote': 'promoted', 'synthesize': 'synthesized',
            }.get(action.action_type, action.action_type)
            counts[display_key] = counts.get(display_key, 0) + 1
        counts['total_actions'] = len(all_actions)
        return counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            'user_id': self.user_id,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'phase1_actions': [
                {'action_type': a.action_type, 'memory_id': a.memory_id, 'reason': a.reason,
                 'phase': a.phase, 'before_snapshot': a.before_snapshot, 'related_ids': a.related_ids}
                for a in self.phase1_actions
            ],
            'phase2_actions': [
                {'action_type': a.action_type, 'memory_id': a.memory_id, 'reason': a.reason,
                 'phase': a.phase, 'before_snapshot': a.before_snapshot, 'related_ids': a.related_ids}
                for a in self.phase2_actions
            ],
            'errors': self.errors,
            'kimi_raw_response': self.kimi_raw_response,
            'summary': self.summary(),
        }

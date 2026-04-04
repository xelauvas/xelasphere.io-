"""Tests for dream executor."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime


def _mock_store(memories=None):
    store = MagicMock()
    if memories:
        store.get.side_effect = lambda mid: next((m for m in memories if m.id == mid), None)
    else:
        store.get.return_value = None
    store.update.return_value = True
    store.supersede.return_value = True
    store.save.return_value = True
    store.promote_provisional.return_value = True
    return store


class TestExecutePhase1:
    def test_executes_prune(self):
        from app.memory_v2.dream.executor import execute_phase1_actions
        from app.memory_v2.schema import MemoryItem
        mem = MemoryItem(id='m1', key='k1', value='v1')
        store = _mock_store([mem])
        actions = execute_phase1_actions(store=store, pruned_ids=['m1'], merged_pairs=[])
        assert len(actions) == 1
        assert actions[0].action_type == 'prune'
        assert actions[0].before_snapshot is not None
        store.update.assert_called_once()

    def test_executes_merge(self):
        from app.memory_v2.dream.executor import execute_phase1_actions
        from app.memory_v2.schema import MemoryItem
        winner = MemoryItem(id='m1', key='style', value='concise', user_id='u1')
        loser = MemoryItem(id='m2', key='style', value='brief', user_id='u1')
        store = _mock_store([winner, loser])
        actions = execute_phase1_actions(store=store, pruned_ids=[], merged_pairs=[('m1', 'm2')])
        assert len(actions) == 1
        assert actions[0].action_type == 'merge'
        assert 'm2' in actions[0].related_ids


class TestExecutePhase2:
    def test_executes_conflict_resolution(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem, MemoryType
        m1 = MemoryItem(id='m1', key='tz', value='PST', type=MemoryType.PROFILE, user_id='u1')
        m2 = MemoryItem(id='m2', key='tz', value='GMT', type=MemoryType.PROFILE, user_id='u1')
        store = _mock_store([m1, m2])
        decisions = {'resolve_conflicts': [{'keep_id': 'm2', 'supersede_ids': ['m1'], 'reason': 'newer'}], 'prune': [], 'promote': [], 'synthesize': []}
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'supersede' for a in actions)

    def test_executes_prune(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem
        mem = MemoryItem(id='m1', key='old', value='stale', user_id='u1')
        store = _mock_store([mem])
        decisions = {'resolve_conflicts': [], 'prune': [{'memory_id': 'm1', 'reason': 'outdated'}], 'promote': [], 'synthesize': []}
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'prune' for a in actions)

    def test_executes_promote(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem, MemoryStatus
        mem = MemoryItem(id='m1', key='role', value='engineer', status=MemoryStatus.PROVISIONAL, user_id='u1')
        store = _mock_store([mem])
        decisions = {'resolve_conflicts': [], 'prune': [], 'promote': [{'memory_id': 'm1', 'reason': 'corroborated'}], 'synthesize': []}
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'promote' for a in actions)
        store.promote_provisional.assert_called_with('m1')

    def test_executes_synthesize(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        store = _mock_store()
        decisions = {'resolve_conflicts': [], 'prune': [], 'promote': [], 'synthesize': [{'type': 'preference', 'key': 'iteration_style', 'value': 'Prefers iterative design', 'evidence_ids': ['m1', 'm2'], 'confidence': 0.75}]}
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'synthesize' for a in actions)
        store.save.assert_called_once()


class TestSaveDreamLog:
    def test_saves_log_to_firestore(self):
        from app.memory_v2.dream.executor import save_dream_log
        from app.memory_v2.dream.report import DreamLog
        mock_db = MagicMock()
        log = DreamLog(user_id='u1', started_at=datetime(2026, 4, 4, 3, 0), completed_at=datetime(2026, 4, 4, 3, 1))
        save_dream_log(db=mock_db, dream_log=log)
        mock_db.collection.assert_called()


class TestUndoDream:
    def test_restores_snapshots(self):
        from app.memory_v2.dream.executor import undo_dream_log
        from app.memory_v2.dream.report import DreamLog, DreamAction
        mock_db = MagicMock()
        store = _mock_store()
        log = DreamLog(
            user_id='u1', started_at=datetime(2026, 4, 4, 3, 0), completed_at=datetime(2026, 4, 4, 3, 1),
            phase1_actions=[DreamAction(action_type='prune', memory_id='m1', reason='expired', phase=1, before_snapshot={'id': 'm1', 'status': 'active', 'key': 'k1', 'value': 'v1'})],
        )
        result = undo_dream_log(db=mock_db, store=store, dream_log=log)
        assert result is True

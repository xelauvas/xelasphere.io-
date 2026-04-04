"""Tests for dream orchestrator."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestMaybeConsolidate:
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_skips_when_disabled(self, mock_state):
        from app.memory_v2.dream import maybe_consolidate
        mock_db = MagicMock()
        result = maybe_consolidate(db=mock_db, user_id='u1', user_prefs={'dream_enabled': False})
        assert result is False
        mock_state.assert_not_called()

    @patch('app.memory_v2.dream.gates.get_dream_state')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    def test_skips_when_time_gate_fails(self, mock_time, mock_state):
        from app.memory_v2.dream import maybe_consolidate
        mock_db = MagicMock()
        mock_state.return_value = {'last_consolidated_at': datetime.utcnow()}
        mock_time.return_value = False
        result = maybe_consolidate(db=mock_db, user_id='u1')
        assert result is False

    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_runs_full_phase1_only(self, mock_state, mock_time, mock_sessions,
                                    mock_lock, mock_should_p2, mock_phase1,
                                    mock_exec_p1, mock_save_log,
                                    mock_update_state, mock_release):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = [MagicMock(status=MagicMock(value='active')) for _ in range(15)]

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = False
        mock_phase1.return_value = ConsolidationReport(pruned=['m1'])
        mock_exec_p1.return_value = []
        mock_save_log.return_value = 'log_123'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_phase1.assert_called_once()
        mock_save_log.assert_called_once()
        mock_update_state.assert_called_once()
        mock_release.assert_called_once()

    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase2_actions')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase2.run_phase2')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_runs_phase1_and_phase2(self, mock_state, mock_time, mock_sessions,
                                     mock_lock, mock_phase1, mock_should_p2,
                                     mock_run_p2, mock_exec_p1, mock_exec_p2,
                                     mock_save_log, mock_update_state, mock_release):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = [MagicMock(status=MagicMock(value='active')) for _ in range(15)]

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = True
        mock_phase1.return_value = ConsolidationReport(flagged_conflicts=[['m1', 'm2']])
        mock_run_p2.return_value = {'resolve_conflicts': [{'keep_id': 'm2', 'supersede_ids': ['m1'], 'reason': 'newer'}], 'prune': [], 'promote': [], 'synthesize': []}
        mock_exec_p1.return_value = []
        mock_exec_p2.return_value = []
        mock_save_log.return_value = 'log_456'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_run_p2.assert_called_once()
        mock_exec_p2.assert_called_once()

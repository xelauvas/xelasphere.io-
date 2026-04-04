"""Tests for dream gate system."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class TestTimeGate:
    def test_passes_when_no_prior_consolidation(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=24)
        result = check_time_gate(last_consolidated_at=None, config=cfg)
        assert result is True

    def test_passes_when_enough_time_elapsed(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=24)
        old_time = datetime.utcnow() - timedelta(hours=25)
        result = check_time_gate(last_consolidated_at=old_time, config=cfg)
        assert result is True

    def test_fails_when_too_recent(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=24)
        recent = datetime.utcnow() - timedelta(hours=2)
        result = check_time_gate(last_consolidated_at=recent, config=cfg)
        assert result is False

    def test_respects_hard_rate_floor(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=6, hard_rate_floor_hours=6)
        five_hours_ago = datetime.utcnow() - timedelta(hours=5)
        result = check_time_gate(last_consolidated_at=five_hours_ago, config=cfg)
        assert result is False


class TestSessionCountGate:
    def test_passes_with_enough_sessions(self):
        from app.memory_v2.dream.gates import check_session_count_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(min_sessions=5)
        mock_db = MagicMock()
        mock_docs = [MagicMock() for _ in range(6)]
        mock_query = MagicMock()
        mock_query.stream.return_value = mock_docs
        mock_db.collection.return_value.document.return_value.collection.return_value.where.return_value = mock_query
        last = datetime.utcnow() - timedelta(hours=25)
        result = check_session_count_gate(db=mock_db, user_id='user_123', last_consolidated_at=last, config=cfg)
        assert result is True

    def test_fails_with_few_sessions(self):
        from app.memory_v2.dream.gates import check_session_count_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(min_sessions=5)
        mock_db = MagicMock()
        mock_docs = [MagicMock() for _ in range(2)]
        mock_query = MagicMock()
        mock_query.stream.return_value = mock_docs
        mock_db.collection.return_value.document.return_value.collection.return_value.where.return_value = mock_query
        last = datetime.utcnow() - timedelta(hours=25)
        result = check_session_count_gate(db=mock_db, user_id='user_123', last_consolidated_at=last, config=cfg)
        assert result is False


class TestLockGate:
    def test_acquires_lock_when_free(self):
        from app.memory_v2.dream.gates import try_acquire_lock
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc
        result = try_acquire_lock(db=mock_db, user_id='user_123', worker_id='w1')
        assert result is True
        mock_doc.set.assert_called_once()

    def test_fails_when_locked_by_another(self):
        from app.memory_v2.dream.gates import try_acquire_lock
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            'worker_id': 'other_worker',
            'locked_at': datetime.utcnow() - timedelta(minutes=5),
        }
        mock_doc.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc
        result = try_acquire_lock(db=mock_db, user_id='user_123', worker_id='w1')
        assert result is False

    def test_reclaims_stale_lock(self):
        from app.memory_v2.dream.gates import try_acquire_lock
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            'worker_id': 'dead_worker',
            'locked_at': datetime.utcnow() - timedelta(hours=2),
        }
        mock_doc.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc
        cfg = DreamConfig(stale_lock_hours=1)
        result = try_acquire_lock(db=mock_db, user_id='user_123', worker_id='w1', config=cfg)
        assert result is True
        mock_doc.set.assert_called_once()

    def test_release_lock(self):
        from app.memory_v2.dream.gates import release_lock
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc
        release_lock(db=mock_db, user_id='user_123')
        mock_doc.delete.assert_called_once()

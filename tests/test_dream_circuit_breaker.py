"""Tests for dream circuit breaker."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta


class TestCheckBreaker:
    def test_allows_when_no_documents_exist(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        mock_global_snap = MagicMock()
        mock_global_snap.exists = False
        mock_user_snap = MagicMock()
        mock_user_snap.exists = False
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is True

    def test_allows_when_disabled_until_is_in_past(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        expired = datetime.utcnow() - timedelta(minutes=5)
        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {
            'consecutive_failures': 5,
            'disabled_until': expired,
        }
        mock_user_snap = MagicMock()
        mock_user_snap.exists = False
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is True

    def test_blocks_when_global_breaker_active(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        future = datetime.utcnow() + timedelta(minutes=30)
        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {
            'consecutive_failures': 5,
            'disabled_until': future,
        }
        mock_user_snap = MagicMock()
        mock_user_snap.exists = False
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is False

    def test_blocks_when_per_user_breaker_active(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        future = datetime.utcnow() + timedelta(minutes=30)
        mock_global_snap = MagicMock()
        mock_global_snap.exists = False
        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {
            'consecutive_failures': 3,
            'disabled_until': future,
        }
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is False

    def test_allows_when_failures_below_threshold_no_disabled_until(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {
            'consecutive_failures': 2,
            'disabled_until': None,
        }
        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {
            'consecutive_failures': 1,
            'disabled_until': None,
        }
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is True


    def test_allows_on_exception_fail_open(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore unavailable")
        assert check_breaker(mock_db, 'user_123') is True


class TestRecordSuccess:
    def test_resets_both_counters(self):
        from app.memory_v2.dream.circuit_breaker import record_success
        mock_db = MagicMock()
        mock_global_doc = MagicMock()
        mock_user_doc = MagicMock()

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        record_success(mock_db, 'user_123')

        mock_global_doc.set.assert_called_once_with({
            'consecutive_failures': 0,
            'disabled_until': None,
            'last_failure_at': None,
        })
        mock_user_doc.set.assert_called_once_with({
            'consecutive_failures': 0,
            'disabled_until': None,
            'last_failure_at': None,
        })


class TestRecordFailure:
    def test_increments_without_tripping(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {'consecutive_failures': 1}

        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {'consecutive_failures': 1}

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig()
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is False
        assert mock_global_doc.set.called
        assert mock_user_doc.set.called
        global_data = mock_global_doc.set.call_args[0][0]
        user_data = mock_user_doc.set.call_args[0][0]
        assert global_data['consecutive_failures'] == 2
        assert user_data['consecutive_failures'] == 2
        assert global_data['disabled_until'] is None
        assert user_data['disabled_until'] is None

    def test_trips_per_user_breaker_at_threshold(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {'consecutive_failures': 1}

        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {'consecutive_failures': 2}  # will become 3

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig(circuit_breaker_max_failures=3)
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is True
        user_data = mock_user_doc.set.call_args[0][0]
        assert user_data['consecutive_failures'] == 3
        assert user_data['disabled_until'] is not None

    def test_trips_global_breaker_at_threshold(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {'consecutive_failures': 4}  # will become 5

        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {'consecutive_failures': 0}

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig(circuit_breaker_global_max_failures=5)
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is True
        global_data = mock_global_doc.set.call_args[0][0]
        assert global_data['consecutive_failures'] == 5
        assert global_data['disabled_until'] is not None

    def test_handles_missing_documents(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = False

        mock_user_snap = MagicMock()
        mock_user_snap.exists = False

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig()
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is False
        global_data = mock_global_doc.set.call_args[0][0]
        assert global_data['consecutive_failures'] == 1

    def test_returns_false_on_exception(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore unavailable")
        config = DreamConfig()
        assert record_failure(mock_db, 'user_123', config) is False

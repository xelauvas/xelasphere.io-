"""Tests for Phase 1 algorithmic consolidation."""
import pytest
from datetime import datetime, timedelta
import numpy as np
from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus, MemoryLifecycle
from app.memory_v2.dream.phase1 import (
    prune_expired, merge_near_duplicates, detect_conflicts, flag_low_value, run_phase1
)
from app.memory_v2.dream.config import DreamConfig
from app.memory_v2.dream.report import ConsolidationReport


def _make_memory(id, key, value, mem_type='preference', status='active',
                 confidence=0.8, use_count=5, last_used_days_ago=10,
                 expires_at=None, embedding=None, repeated_by_user=0,
                 explicit_confirmed=False, created_days_ago=90):
    type_map = {'profile': MemoryType.PROFILE, 'preference': MemoryType.PREFERENCE,
                'project': MemoryType.PROJECT, 'constraint': MemoryType.CONSTRAINT,
                'temporary': MemoryType.TEMPORARY}
    status_map = {'active': MemoryStatus.ACTIVE, 'provisional': MemoryStatus.PROVISIONAL,
                  'expired': MemoryStatus.EXPIRED}
    last_used = datetime.utcnow() - timedelta(days=last_used_days_ago) if last_used_days_ago else None
    return MemoryItem(
        id=id, user_id='user_123', type=type_map[mem_type], key=key, value=value,
        status=status_map[status], confidence=confidence,
        lifecycle=MemoryLifecycle(
            created_at=datetime.utcnow() - timedelta(days=created_days_ago),
            updated_at=datetime.utcnow() - timedelta(days=last_used_days_ago or 0),
            last_used_at=last_used, expires_at=expires_at,
            use_count=use_count, repeated_by_user=repeated_by_user,
            explicit_confirmed=explicit_confirmed,
        ),
        embedding=embedding,
    )


class TestPruneExpired:
    def test_expired_ttl_gets_pruned(self):
        """Memory with expires_at in the past should be pruned."""
        mem = _make_memory(
            'mem_1', 'session', 'foo', mem_type='temporary',
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        result = prune_expired([mem])
        assert 'mem_1' in result

    def test_future_ttl_not_pruned(self):
        """Memory with expires_at in the future should NOT be pruned."""
        mem = _make_memory(
            'mem_2', 'session', 'foo', mem_type='temporary',
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        result = prune_expired([mem])
        assert 'mem_2' not in result

    def test_old_provisional_without_confirmation_pruned(self):
        """Provisional memory older than 14 days with no confirmation should be pruned."""
        mem = _make_memory(
            'mem_3', 'tone', 'casual', status='provisional',
            created_days_ago=20, repeated_by_user=0, explicit_confirmed=False,
        )
        result = prune_expired([mem])
        assert 'mem_3' in result

    def test_confirmed_provisional_kept(self):
        """Provisional memory that is explicitly confirmed should NOT be pruned."""
        mem = _make_memory(
            'mem_4', 'tone', 'casual', status='provisional',
            created_days_ago=20, repeated_by_user=0, explicit_confirmed=True,
        )
        result = prune_expired([mem])
        assert 'mem_4' not in result

    def test_provisional_with_repeated_user_kept(self):
        """Provisional memory repeated by user >= 2 times should NOT be pruned."""
        mem = _make_memory(
            'mem_5', 'tone', 'casual', status='provisional',
            created_days_ago=20, repeated_by_user=2, explicit_confirmed=False,
        )
        result = prune_expired([mem])
        assert 'mem_5' not in result

    def test_fresh_provisional_kept(self):
        """Provisional memory less than 14 days old should NOT be pruned."""
        mem = _make_memory(
            'mem_6', 'tone', 'casual', status='provisional',
            created_days_ago=5, repeated_by_user=0, explicit_confirmed=False,
        )
        result = prune_expired([mem])
        assert 'mem_6' not in result

    def test_active_memory_not_pruned(self):
        """Active memory with no expiry should never be pruned."""
        mem = _make_memory('mem_7', 'timezone', 'UTC', status='active')
        result = prune_expired([mem])
        assert 'mem_7' not in result

    def test_empty_list(self):
        """Empty input returns empty list."""
        assert prune_expired([]) == []


class TestMergeNearDuplicates:
    def _vec(self, *components):
        v = np.array(components, dtype=np.float32)
        return (v / np.linalg.norm(v)).tolist()

    def test_high_similarity_same_key_merges(self):
        """Two memories with same key and high cosine similarity should be merged."""
        base = self._vec(1.0, 0.0, 0.0)
        almost = self._vec(0.9999, 0.001, 0.0)
        mem_a = _make_memory('mem_a', 'tone', 'formal', confidence=0.9, embedding=base)
        mem_b = _make_memory('mem_b', 'tone', 'formal', confidence=0.7, embedding=almost)
        result = merge_near_duplicates([mem_a, mem_b], threshold=0.92)
        assert len(result) == 1
        winner_id, loser_id = result[0]
        # Higher confidence wins
        assert winner_id == 'mem_a'
        assert loser_id == 'mem_b'

    def test_different_keys_dont_merge(self):
        """Memories with different keys should not merge even if embeddings are similar."""
        base = self._vec(1.0, 0.0, 0.0)
        mem_a = _make_memory('mem_a', 'tone', 'formal', embedding=base)
        mem_b = _make_memory('mem_b', 'style', 'formal', embedding=base)
        result = merge_near_duplicates([mem_a, mem_b], threshold=0.92)
        assert result == []

    def test_low_similarity_doesnt_merge(self):
        """Memories with cosine similarity below threshold should not merge."""
        mem_a = _make_memory('mem_a', 'tone', 'formal', embedding=self._vec(1.0, 0.0, 0.0))
        mem_b = _make_memory('mem_b', 'tone', 'casual', embedding=self._vec(0.0, 1.0, 0.0))
        result = merge_near_duplicates([mem_a, mem_b], threshold=0.92)
        assert result == []

    def test_no_embedding_skipped(self):
        """Memories without embeddings should be skipped."""
        mem_a = _make_memory('mem_a', 'tone', 'formal', embedding=None)
        mem_b = _make_memory('mem_b', 'tone', 'formal', embedding=None)
        result = merge_near_duplicates([mem_a, mem_b], threshold=0.92)
        assert result == []

    def test_lower_confidence_wins_when_reversed(self):
        """When b has higher confidence than a, b should be the winner."""
        base = self._vec(1.0, 0.0, 0.0)
        almost = self._vec(0.9999, 0.001, 0.0)
        mem_a = _make_memory('mem_a', 'tone', 'formal', confidence=0.6, embedding=base)
        mem_b = _make_memory('mem_b', 'tone', 'formal', confidence=0.95, embedding=almost)
        result = merge_near_duplicates([mem_a, mem_b], threshold=0.92)
        assert len(result) == 1
        winner_id, loser_id = result[0]
        assert winner_id == 'mem_b'
        assert loser_id == 'mem_a'

    def test_empty_list(self):
        assert merge_near_duplicates([]) == []


class TestDetectConflicts:
    def test_same_key_different_value_profile_conflict(self):
        """Two active PROFILE memories with same key but different values = conflict."""
        mem_a = _make_memory('mem_a', 'timezone', 'UTC', mem_type='profile', status='active')
        mem_b = _make_memory('mem_b', 'timezone', 'America/New_York', mem_type='profile', status='active')
        result = detect_conflicts([mem_a, mem_b])
        assert len(result) == 1
        assert set(result[0]) == {'mem_a', 'mem_b'}

    def test_same_key_same_value_no_conflict(self):
        """Two active PROFILE memories with same key and same value = no conflict."""
        mem_a = _make_memory('mem_a', 'timezone', 'UTC', mem_type='profile', status='active')
        mem_b = _make_memory('mem_b', 'timezone', 'UTC', mem_type='profile', status='active')
        result = detect_conflicts([mem_a, mem_b])
        assert result == []

    def test_different_keys_no_conflict(self):
        """Memories with different keys should not conflict."""
        mem_a = _make_memory('mem_a', 'timezone', 'UTC', mem_type='constraint', status='active')
        mem_b = _make_memory('mem_b', 'no_emoji', 'true', mem_type='constraint', status='active')
        result = detect_conflicts([mem_a, mem_b])
        assert result == []

    def test_provisional_not_included_in_conflict(self):
        """Provisional memories are excluded from conflict detection."""
        mem_a = _make_memory('mem_a', 'timezone', 'UTC', mem_type='profile', status='active')
        mem_b = _make_memory('mem_b', 'timezone', 'America/New_York', mem_type='profile', status='provisional')
        result = detect_conflicts([mem_a, mem_b])
        # mem_b is provisional, so only one active — no conflict
        assert result == []

    def test_preference_conflict_detected(self):
        """Two active PREFERENCE memories with same key but different values = conflict."""
        mem_a = _make_memory('mem_a', 'code_style', 'tabs', mem_type='preference', status='active')
        mem_b = _make_memory('mem_b', 'code_style', 'spaces', mem_type='preference', status='active')
        result = detect_conflicts([mem_a, mem_b])
        assert len(result) == 1
        assert set(result[0]) == {'mem_a', 'mem_b'}

    def test_empty_list(self):
        assert detect_conflicts([]) == []


class TestFlagLowValue:
    def test_unused_old_memory_flagged(self):
        """Memory never used and older than unused_days threshold should be flagged."""
        mem = _make_memory(
            'mem_1', 'old_pref', 'something',
            use_count=0, last_used_days_ago=None, created_days_ago=90,
        )
        result = flag_low_value([mem], unused_days=60)
        assert 'mem_1' in result

    def test_recently_created_unused_not_flagged(self):
        """Memory never used but recently created should NOT be flagged."""
        mem = _make_memory(
            'mem_2', 'new_pref', 'something',
            use_count=0, last_used_days_ago=None, created_days_ago=10,
        )
        result = flag_low_value([mem], unused_days=60)
        assert 'mem_2' not in result

    def test_low_confidence_provisional_flagged(self):
        """Provisional memory below confidence_floor should be flagged."""
        mem = _make_memory(
            'mem_3', 'guess', 'maybe', status='provisional',
            confidence=0.3, use_count=5, last_used_days_ago=5,
        )
        result = flag_low_value([mem], confidence_floor=0.5)
        assert 'mem_3' in result

    def test_low_confidence_active_not_flagged(self):
        """Active (non-provisional) memory below confidence floor should NOT be flagged by confidence rule."""
        mem = _make_memory(
            'mem_4', 'guess', 'maybe', status='active',
            confidence=0.3, use_count=5, last_used_days_ago=5,
        )
        result = flag_low_value([mem], confidence_floor=0.5)
        assert 'mem_4' not in result

    def test_well_used_memory_kept(self):
        """Memory with use_count > 0 should not be flagged for low usage."""
        mem = _make_memory(
            'mem_5', 'timezone', 'UTC',
            use_count=10, last_used_days_ago=5, confidence=0.9,
        )
        result = flag_low_value([mem], unused_days=60, confidence_floor=0.5)
        assert 'mem_5' not in result

    def test_empty_list(self):
        assert flag_low_value([]) == []


class TestRunPhase1:
    def test_returns_consolidation_report(self):
        """run_phase1 should return a ConsolidationReport instance."""
        mem = _make_memory('mem_1', 'timezone', 'UTC', use_count=5, last_used_days_ago=5)
        config = DreamConfig()
        report = run_phase1([mem], config)
        assert isinstance(report, ConsolidationReport)

    def test_report_has_all_fields(self):
        """ConsolidationReport should have all expected fields."""
        config = DreamConfig()
        report = run_phase1([], config)
        assert hasattr(report, 'pruned')
        assert hasattr(report, 'merged')
        assert hasattr(report, 'flagged_conflicts')
        assert hasattr(report, 'flagged_low_value')
        assert hasattr(report, 'all_active')

    def test_expired_memory_not_in_all_active(self):
        """Pruned memories should not appear in all_active."""
        mem = _make_memory(
            'mem_expired', 'session', 'x', mem_type='temporary',
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        config = DreamConfig()
        report = run_phase1([mem], config)
        assert 'mem_expired' in report.pruned
        active_ids = [m.id for m in report.all_active]
        assert 'mem_expired' not in active_ids

    def test_merged_loser_not_in_all_active(self):
        """The loser of a merge should not appear in all_active."""
        v1 = [1.0, 0.0, 0.0]
        v2_raw = np.array([0.9999, 0.001, 0.0], dtype=np.float32)
        v2 = (v2_raw / np.linalg.norm(v2_raw)).tolist()
        mem_a = _make_memory('mem_winner', 'tone', 'formal', confidence=0.9, embedding=v1)
        mem_b = _make_memory('mem_loser', 'tone', 'formal', confidence=0.7, embedding=v2)
        config = DreamConfig(embedding_similarity_threshold=0.92)
        report = run_phase1([mem_a, mem_b], config)
        assert len(report.merged) == 1
        active_ids = [m.id for m in report.all_active]
        assert 'mem_loser' not in active_ids
        assert 'mem_winner' in active_ids

    def test_clean_memories_pass_through(self):
        """Well-formed active memories should survive all phases and appear in all_active."""
        mems = [
            _make_memory('mem_a', 'timezone', 'UTC', use_count=10, last_used_days_ago=2),
            _make_memory('mem_b', 'language', 'English', use_count=8, last_used_days_ago=3),
        ]
        config = DreamConfig()
        report = run_phase1(mems, config)
        active_ids = [m.id for m in report.all_active]
        assert 'mem_a' in active_ids
        assert 'mem_b' in active_ids
        assert report.pruned == []
        assert report.merged == []

    def test_config_thresholds_applied(self):
        """Config values for unused_days and confidence_floor should be respected."""
        # Memory with use_count=0 and old enough to be flagged with default config
        mem = _make_memory(
            'mem_old', 'old_key', 'val',
            use_count=0, last_used_days_ago=None, created_days_ago=90,
        )
        config = DreamConfig(low_value_unused_days=60)
        report = run_phase1([mem], config)
        assert 'mem_old' in report.flagged_low_value

"""Tests for dream system configuration."""
import pytest
from unittest.mock import MagicMock, patch


class TestDreamConfig:
    def test_default_config(self):
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig()
        assert cfg.enabled is True
        assert cfg.frequency_hours == 24
        assert cfg.min_sessions == 5
        assert cfg.min_active_memories == 10
        assert cfg.max_mutations_per_cycle == 50
        assert cfg.max_syntheses_per_cycle == 3
        assert cfg.kimi_token_budget == 4000
        assert cfg.hard_rate_floor_hours == 6
        assert cfg.stale_lock_hours == 1
        assert cfg.circuit_breaker_max_failures == 3
        assert cfg.circuit_breaker_cooldown_hours == 1
        assert cfg.circuit_breaker_global_max_failures == 5

    def test_get_dream_config_no_prefs(self):
        from app.memory_v2.dream.config import get_dream_config
        cfg = get_dream_config(user_prefs=None)
        assert cfg.enabled is True
        assert cfg.frequency_hours == 24

    def test_get_dream_config_user_overrides(self):
        from app.memory_v2.dream.config import get_dream_config
        prefs = {
            'dream_enabled': False,
            'dream_frequency_hours': 12,
            'dream_min_sessions': 8,
        }
        cfg = get_dream_config(user_prefs=prefs)
        assert cfg.enabled is False
        assert cfg.frequency_hours == 12
        assert cfg.min_sessions == 8

    def test_get_dream_config_clamps_values(self):
        from app.memory_v2.dream.config import get_dream_config
        prefs = {
            'dream_frequency_hours': 2,
            'dream_min_sessions': 100,
        }
        cfg = get_dream_config(user_prefs=prefs)
        assert cfg.frequency_hours == 6
        assert cfg.min_sessions == 20


class TestDreamReport:
    def test_consolidation_report_defaults(self):
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport()
        assert report.pruned == []
        assert report.merged == []
        assert report.flagged_conflicts == []
        assert report.flagged_low_value == []
        assert report.all_active == []

    def test_dream_action_creation(self):
        from app.memory_v2.dream.report import DreamAction
        action = DreamAction(
            action_type='prune', memory_id='mem_abc123',
            reason='Expired TTL', phase=1,
        )
        assert action.action_type == 'prune'
        assert action.memory_id == 'mem_abc123'
        assert action.before_snapshot is None

    def test_dream_log_creation(self):
        from app.memory_v2.dream.report import DreamLog
        from datetime import datetime
        log = DreamLog(
            user_id='user_123',
            started_at=datetime(2026, 4, 4, 3, 0),
            completed_at=datetime(2026, 4, 4, 3, 1),
        )
        assert log.user_id == 'user_123'
        assert log.phase1_actions == []
        assert log.phase2_actions == []
        assert log.errors == []
        assert log.kimi_raw_response is None

    def test_dream_log_summary(self):
        from app.memory_v2.dream.report import DreamLog, DreamAction
        from datetime import datetime
        log = DreamLog(
            user_id='user_123',
            started_at=datetime(2026, 4, 4, 3, 0),
            completed_at=datetime(2026, 4, 4, 3, 1),
            phase1_actions=[
                DreamAction(action_type='prune', memory_id='m1', reason='expired', phase=1),
                DreamAction(action_type='prune', memory_id='m2', reason='expired', phase=1),
                DreamAction(action_type='merge', memory_id='m3', reason='duplicate', phase=1),
            ],
            phase2_actions=[
                DreamAction(action_type='synthesize', memory_id='m4', reason='pattern', phase=2),
            ],
        )
        summary = log.summary()
        assert summary['pruned'] == 2
        assert summary['merged'] == 1
        assert summary['synthesized'] == 1
        assert summary['total_actions'] == 4

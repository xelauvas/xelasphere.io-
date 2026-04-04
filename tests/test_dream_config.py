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

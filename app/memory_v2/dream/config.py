"""Dream system configuration and defaults."""
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class DreamConfig:
    """Configuration for the dream consolidation system."""
    enabled: bool = True
    frequency_hours: int = 24
    min_sessions: int = 5
    min_active_memories: int = 10
    max_mutations_per_cycle: int = 50
    max_syntheses_per_cycle: int = 3
    kimi_token_budget: int = 4000
    hard_rate_floor_hours: int = 6
    stale_lock_hours: int = 1
    circuit_breaker_max_failures: int = 3
    circuit_breaker_cooldown_hours: int = 1
    embedding_similarity_threshold: float = 0.92
    low_value_unused_days: int = 60
    low_value_confidence_floor: float = 0.5
    pattern_synthesis_threshold: int = 15


def get_dream_config(user_prefs: Optional[Dict[str, Any]] = None) -> DreamConfig:
    """Build DreamConfig from defaults + user preference overrides."""
    cfg = DreamConfig()
    if not user_prefs:
        return cfg
    if 'dream_enabled' in user_prefs:
        cfg.enabled = bool(user_prefs['dream_enabled'])
    if 'dream_frequency_hours' in user_prefs:
        val = int(user_prefs['dream_frequency_hours'])
        cfg.frequency_hours = max(6, min(168, val))
    if 'dream_min_sessions' in user_prefs:
        val = int(user_prefs['dream_min_sessions'])
        cfg.min_sessions = max(3, min(20, val))
    return cfg

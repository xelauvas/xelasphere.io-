"""
Memory V3 - Rollout & Metrics
=============================
Safe, measurable rollout infrastructure for V3.

Rollout phases:
- Phase 0: Shadow mode (signals computed, not injected)
- Phase 1: 1% injection cohort
- Phase 2: Ramp (10% → 25% → 50% → 100%)

Key metrics tracked:
- Signal distribution (context_mode, pace, certainty)
- Anti-gaming activation rate
- Follow-up rate (turns to completion proxy)
- Re-ask rate (same question within N minutes)
"""

import os
import logging
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)

# Rollout configuration via environment
V3_ROLLOUT_PERCENT = float(os.getenv("V3_ROLLOUT_PERCENT", "0"))  # 0 = shadow only
V3_ROLLOUT_PHASE = os.getenv("V3_ROLLOUT_PHASE", "shadow")  # shadow, 1pct, ramp, full


class RolloutPhase(Enum):
    """Rollout phases for V3."""
    SHADOW = "shadow"       # Phase 0: compute but don't inject
    ONE_PERCENT = "1pct"    # Phase 1: 1% injection
    RAMP = "ramp"           # Phase 2: gradual increase
    FULL = "full"           # Phase 3: 100% rollout


@dataclass
class V3Metrics:
    """
    Metrics tracked for V3 rollout monitoring.

    These are aggregated per-session or per-conversation,
    then can be logged/exported for analysis.
    """
    # Signal distribution
    context_mode_counts: Dict[str, int] = field(default_factory=dict)
    pace_counts: Dict[str, int] = field(default_factory=dict)
    certainty_counts: Dict[str, int] = field(default_factory=dict)

    # Anti-gaming metrics
    anti_gaming_triggers: int = 0
    total_signals: int = 0

    # Rollout metrics
    signals_injected: int = 0
    signals_shadow_only: int = 0

    # KPI tracking
    conversation_turns: int = 0
    follow_up_turns: int = 0  # Turns that look like follow-ups
    re_ask_count: int = 0     # Same question asked again

    # Timestamps
    started_at: datetime = field(default_factory=datetime.utcnow)
    last_signal_at: Optional[datetime] = None

    def record_signal(self, signal: Any, injected: bool = False):
        """Record a V3 signal for metrics."""
        self.total_signals += 1
        self.last_signal_at = datetime.utcnow()

        if signal:
            # Context mode distribution
            mode = signal.context.mode.value
            self.context_mode_counts[mode] = self.context_mode_counts.get(mode, 0) + 1

            # Pace distribution
            pace = signal.state.pace.value
            self.pace_counts[pace] = self.pace_counts.get(pace, 0) + 1

            # Certainty distribution
            certainty = signal.state.certainty.value
            self.certainty_counts[certainty] = self.certainty_counts.get(certainty, 0) + 1

        if injected:
            self.signals_injected += 1
        else:
            self.signals_shadow_only += 1

    def record_anti_gaming_trigger(self):
        """Record an anti-gaming activation."""
        self.anti_gaming_triggers += 1

    def record_turn(self, is_follow_up: bool = False):
        """Record a conversation turn."""
        self.conversation_turns += 1
        if is_follow_up:
            self.follow_up_turns += 1

    def record_re_ask(self):
        """Record a re-ask (same question repeated)."""
        self.re_ask_count += 1

    def get_anti_gaming_rate(self) -> float:
        """Get anti-gaming activation rate."""
        if self.total_signals == 0:
            return 0.0
        return self.anti_gaming_triggers / self.total_signals

    def get_follow_up_rate(self) -> float:
        """Get follow-up rate (proxy for 'didn't get it first time')."""
        if self.conversation_turns == 0:
            return 0.0
        return self.follow_up_turns / self.conversation_turns

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dict for logging/analysis."""
        return {
            "distribution": {
                "context_mode": self.context_mode_counts,
                "pace": self.pace_counts,
                "certainty": self.certainty_counts,
            },
            "anti_gaming": {
                "triggers": self.anti_gaming_triggers,
                "rate": round(self.get_anti_gaming_rate(), 4),
            },
            "rollout": {
                "total_signals": self.total_signals,
                "injected": self.signals_injected,
                "shadow_only": self.signals_shadow_only,
                "injection_rate": round(self.signals_injected / max(1, self.total_signals), 4),
            },
            "kpis": {
                "turns": self.conversation_turns,
                "follow_up_turns": self.follow_up_turns,
                "follow_up_rate": round(self.get_follow_up_rate(), 4),
                "re_ask_count": self.re_ask_count,
            },
            "timing": {
                "started_at": self.started_at.isoformat(),
                "last_signal_at": self.last_signal_at.isoformat() if self.last_signal_at else None,
                "duration_seconds": (datetime.utcnow() - self.started_at).total_seconds(),
            },
        }


class V3RolloutManager:
    """
    Manages V3 rollout cohort assignment and metrics.

    Usage:
        manager = get_rollout_manager()

        # Check if user should get V3 injection
        if manager.should_inject(user_id):
            # Inject V3 into prompt
            ...

        # Record metrics
        manager.record_signal(signal, injected=True)
    """

    def __init__(
        self,
        rollout_percent: float = None,
        phase: str = None,
    ):
        """
        Initialize rollout manager.

        Args:
            rollout_percent: Override rollout percentage (0-100)
            phase: Override rollout phase
        """
        self.rollout_percent = rollout_percent if rollout_percent is not None else V3_ROLLOUT_PERCENT
        self.phase = RolloutPhase(phase) if phase else RolloutPhase(V3_ROLLOUT_PHASE)
        self.metrics = V3Metrics()

        logger.info(
            f"V3 Rollout: phase={self.phase.value}, percent={self.rollout_percent}%"
        )

    def should_inject(self, user_id: str = None, session_id: str = None) -> bool:
        """
        Determine if V3 should be injected for this user/session.

        Uses consistent hashing so same user always gets same treatment
        within a rollout phase.

        Args:
            user_id: User identifier (for user-level cohorts)
            session_id: Session identifier (fallback)

        Returns:
            True if V3 should be injected, False for shadow-only
        """
        # Shadow phase = never inject
        if self.phase == RolloutPhase.SHADOW:
            return False

        # Full phase = always inject
        if self.phase == RolloutPhase.FULL:
            return True

        # Percentage-based phases
        if self.rollout_percent <= 0:
            return False
        if self.rollout_percent >= 100:
            return True

        # Consistent hash for cohort assignment
        identifier = user_id or session_id or "default"
        hash_input = f"v3_rollout_{identifier}".encode()
        hash_value = int(hashlib.md5(hash_input).hexdigest(), 16)
        bucket = hash_value % 100

        return bucket < self.rollout_percent

    def get_cohort(self, user_id: str = None, session_id: str = None) -> str:
        """
        Get cohort assignment for logging/analysis.

        Returns:
            'treatment' if V3 injected, 'control' if shadow-only
        """
        if self.should_inject(user_id, session_id):
            return "treatment"
        return "control"

    def record_signal(self, signal: Any, injected: bool = False):
        """Record a V3 signal generation."""
        self.metrics.record_signal(signal, injected)

    def record_anti_gaming_trigger(self):
        """Record anti-gaming activation."""
        self.metrics.record_anti_gaming_trigger()

    def _extract_text(self, message) -> str:
        """
        Extract text content from a message (string or dict).

        Handles both raw strings and message dicts from views.py.
        """
        if message is None:
            return ""
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            # Try common keys used in message dicts
            return message.get('content') or message.get('prompt') or message.get('text') or str(message)
        return str(message)

    def record_turn(self, message, previous_messages: List = None):
        """
        Record a conversation turn with follow-up detection.

        Args:
            message: Current user message (string or dict)
            previous_messages: Recent messages for comparison (strings or dicts)
        """
        # Normalize inputs to strings
        message_text = self._extract_text(message)
        prev_texts = None
        if previous_messages:
            prev_texts = [self._extract_text(m) for m in previous_messages]

        is_follow_up = self._detect_follow_up(message_text, prev_texts)
        self.metrics.record_turn(is_follow_up)

        # Check for re-ask
        if prev_texts and self._detect_re_ask(message_text, prev_texts):
            self.metrics.record_re_ask()

    def _detect_follow_up(self, message: str, previous_messages: List[str] = None) -> bool:
        """
        Detect if message looks like a follow-up (user didn't get it first time).

        Indicators:
        - Starts with "no", "not what I meant", "I meant"
        - Contains "still", "again", "doesn't work"
        - Very short clarification
        """
        if not message:
            return False

        message_lower = message.lower().strip()

        # Explicit correction patterns
        correction_patterns = [
            "no,", "no ", "not what i",
            "i meant", "i mean", "what i meant",
            "that's not", "thats not",
            "actually,", "actually ",
            "still doesn't", "still not",
            "doesn't work", "didn't work",
            "try again", "do it again",
        ]

        for pattern in correction_patterns:
            if message_lower.startswith(pattern) or f" {pattern}" in message_lower:
                return True

        return False

    def _detect_re_ask(self, message: str, previous_messages: List[str]) -> bool:
        """
        Detect if user is asking the same thing again.

        Simple heuristic: high word overlap with recent message.
        """
        if not previous_messages:
            return False

        current_words = set(message.lower().split())
        if len(current_words) < 3:
            return False

        for prev in previous_messages[-3:]:  # Check last 3 messages
            prev_words = set(prev.lower().split())
            if len(prev_words) < 3:
                continue

            overlap = len(current_words & prev_words)
            overlap_ratio = overlap / min(len(current_words), len(prev_words))

            if overlap_ratio > 0.7:  # 70%+ word overlap
                return True

        return False

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        return self.metrics.to_dict()

    def log_metrics(self):
        """Log current metrics for monitoring."""
        metrics = self.get_metrics()
        logger.info(f"V3 Metrics: {metrics}")
        return metrics

    def reset_metrics(self):
        """Reset metrics (e.g., for new conversation)."""
        self.metrics = V3Metrics()


# Global rollout manager instance
_rollout_manager: Optional[V3RolloutManager] = None


def get_rollout_manager() -> V3RolloutManager:
    """Get or create the global rollout manager."""
    global _rollout_manager
    if _rollout_manager is None:
        _rollout_manager = V3RolloutManager()
    return _rollout_manager


def should_inject_v3(user_id: str = None, session_id: str = None) -> bool:
    """Convenience function to check V3 injection."""
    return get_rollout_manager().should_inject(user_id, session_id)


def get_v3_cohort(user_id: str = None, session_id: str = None) -> str:
    """Convenience function to get cohort assignment."""
    return get_rollout_manager().get_cohort(user_id, session_id)


def record_v3_metrics(signal: Any, injected: bool = False):
    """Convenience function to record metrics."""
    get_rollout_manager().record_signal(signal, injected)


# Phase exit criteria checkers
def check_shadow_exit_criteria(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check if shadow mode exit criteria are met.

    Criteria:
    - No obvious misclassification patterns (manual review)
    - Anti-gaming rate < 2%
    - Reasonable distribution across modes
    """
    results = {
        "ready_to_exit": True,
        "checks": {},
    }

    # Check anti-gaming rate
    anti_gaming_rate = metrics.get("anti_gaming", {}).get("rate", 0)
    results["checks"]["anti_gaming_rate"] = {
        "value": anti_gaming_rate,
        "threshold": 0.02,
        "passed": anti_gaming_rate < 0.02,
    }
    if anti_gaming_rate >= 0.02:
        results["ready_to_exit"] = False

    # Check that we have enough signals
    total_signals = metrics.get("rollout", {}).get("total_signals", 0)
    results["checks"]["sample_size"] = {
        "value": total_signals,
        "threshold": 100,
        "passed": total_signals >= 100,
    }
    if total_signals < 100:
        results["ready_to_exit"] = False

    # Check distribution isn't dominated by one mode
    context_dist = metrics.get("distribution", {}).get("context_mode", {})
    if context_dist:
        total = sum(context_dist.values())
        max_pct = max(context_dist.values()) / total if total > 0 else 0
        results["checks"]["mode_diversity"] = {
            "value": round(max_pct, 2),
            "threshold": 0.8,  # No single mode > 80%
            "passed": max_pct < 0.8,
        }
        if max_pct >= 0.8:
            results["ready_to_exit"] = False

    return results


def check_injection_exit_criteria(metrics: Dict[str, Any], baseline_metrics: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Check if injection phase exit criteria are met.

    Criteria:
    - No spike in follow-up rate vs baseline
    - No spike in re-ask rate
    - Anti-gaming rate still healthy
    """
    results = {
        "ready_to_ramp": True,
        "checks": {},
    }

    # Check follow-up rate
    follow_up_rate = metrics.get("kpis", {}).get("follow_up_rate", 0)
    baseline_follow_up = baseline_metrics.get("kpis", {}).get("follow_up_rate", 0.2) if baseline_metrics else 0.2

    results["checks"]["follow_up_rate"] = {
        "value": follow_up_rate,
        "baseline": baseline_follow_up,
        "max_increase": 0.1,  # Allow 10% increase max
        "passed": follow_up_rate <= baseline_follow_up + 0.1,
    }
    if follow_up_rate > baseline_follow_up + 0.1:
        results["ready_to_ramp"] = False

    # Check anti-gaming rate
    anti_gaming_rate = metrics.get("anti_gaming", {}).get("rate", 0)
    results["checks"]["anti_gaming_rate"] = {
        "value": anti_gaming_rate,
        "threshold": 0.02,
        "passed": anti_gaming_rate < 0.02,
    }
    if anti_gaming_rate >= 0.02:
        results["ready_to_ramp"] = False

    return results

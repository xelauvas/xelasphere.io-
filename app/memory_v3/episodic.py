"""
Memory V3 Week 2 - Episodic Hints
=================================
Lets the AI notice patterns, not facts, across interactions.

CRITICAL RULES:
- Episodes are summaries, not logs
- No raw text stored
- No quotes
- Pattern labels only
- Max 10 items
- TTL enforced

This is the riskiest Week 2 feature - deploy last!
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from enum import Enum
import re

# Feature flag - OFF by default (ship last)
V3_EPISODIC_ENABLED = os.getenv("V3_EPISODIC_ENABLED", "false").lower() in ("true", "1", "yes")

# Safety constraints
MAX_EPISODES = 10
EPISODE_TTL_DAYS = 30
MIN_OCCURRENCES_TO_EMIT = 3  # Pattern must occur 3+ times to become a hint

# Enable criteria for episodic hints (operator checklist):
# 1. V3 at 25%+ rollout stable (no regression in follow_up_rate)
# 2. follow_up_rate improved vs baseline
# 3. re_ask_rate improved vs baseline
# 4. No increase in opt-out or "creepy" feedback
# 5. Episode hint count stays capped (<= MAX_EPISODES per user)
# Start with 1% rollout, keep TTL short (30-90 days max)
EPISODIC_ENABLE_CHECKLIST = [
    "v3_rollout_stable_25pct",
    "follow_up_rate_improved",
    "re_ask_rate_improved",
    "no_opt_out_increase",
    "no_creepy_feedback",
    "episode_count_capped",
]


class EpisodeCategory(Enum):
    """Categories of behavioral patterns."""
    RESPONSE_STYLE = "response_style"      # How user wants responses
    ITERATION_PATTERN = "iteration"         # How user iterates
    VERIFICATION = "verification"           # How user verifies/challenges
    LEARNING_STYLE = "learning"             # How user learns


@dataclass
class EpisodicHint:
    """
    A behavioral pattern hint.

    NOT stored - computed on the fly from V2 memories or conversation state.
    """
    hint_id: str                           # e.g., "often_requests_simplification"
    category: EpisodeCategory
    confidence: float                       # 0-1, how strong the pattern is
    last_observed: Optional[datetime] = None
    occurrence_count: int = 0

    def to_label(self) -> str:
        """Get the hint label for prompt injection."""
        return self.hint_id


@dataclass
class EpisodicContext:
    """
    Container for episodic hints to inject into prompt.

    Computed fresh each session - nothing stored.
    """
    hints: List[EpisodicHint] = field(default_factory=list)
    computed_at: datetime = field(default_factory=datetime.utcnow)

    def to_prompt_block(self) -> str:
        """Generate prompt injection for episodic hints."""
        if not V3_EPISODIC_ENABLED or not self.hints:
            return ""

        # Only include confident hints
        confident_hints = [h for h in self.hints if h.confidence >= 0.6]

        if not confident_hints:
            return ""

        # Cap at MAX_EPISODES
        confident_hints = confident_hints[:MAX_EPISODES]

        labels = [h.to_label() for h in confident_hints]
        return f"episode_hints: {labels}"

    def get_labels(self) -> List[str]:
        """Get just the hint labels."""
        return [h.to_label() for h in self.hints if h.confidence >= 0.6][:MAX_EPISODES]


# Pattern detectors for deriving episodes from conversation state
# These work on aggregated metrics, NOT on raw messages

def _detect_simplification_pattern(metrics: Dict[str, Any]) -> Optional[EpisodicHint]:
    """Detect if user often requests simplification after first answer."""
    follow_up_rate = metrics.get("follow_up_rate", 0)
    simplification_triggers = metrics.get("simplification_requests", 0)

    if follow_up_rate > 0.3 and simplification_triggers >= MIN_OCCURRENCES_TO_EMIT:
        return EpisodicHint(
            hint_id="often_requests_simplification_after_first_answer",
            category=EpisodeCategory.RESPONSE_STYLE,
            confidence=min(0.9, 0.5 + simplification_triggers * 0.1),
            occurrence_count=simplification_triggers
        )
    return None


def _detect_iteration_pattern(metrics: Dict[str, Any]) -> Optional[EpisodicHint]:
    """Detect if user iterates designs over many turns."""
    avg_turns = metrics.get("avg_conversation_turns", 0)
    design_context_rate = metrics.get("design_context_rate", 0)

    if avg_turns > 5 and design_context_rate > 0.3:
        return EpisodicHint(
            hint_id="iterates_designs_over_many_turns",
            category=EpisodeCategory.ITERATION_PATTERN,
            confidence=min(0.85, 0.5 + design_context_rate * 0.5),
            occurrence_count=int(avg_turns)
        )
    return None


def _detect_challenger_pattern(metrics: Dict[str, Any]) -> Optional[EpisodicHint]:
    """Detect if user often challenges assumptions."""
    correction_rate = metrics.get("correction_rate", 0)
    re_ask_count = metrics.get("re_ask_count", 0)

    if correction_rate > 0.2 or re_ask_count >= MIN_OCCURRENCES_TO_EMIT:
        return EpisodicHint(
            hint_id="often_challenges_assumptions",
            category=EpisodeCategory.VERIFICATION,
            confidence=min(0.8, 0.4 + correction_rate),
            occurrence_count=re_ask_count
        )
    return None


def _detect_code_only_pattern(metrics: Dict[str, Any]) -> Optional[EpisodicHint]:
    """Detect if user prefers code-only responses when executing."""
    execute_context_rate = metrics.get("execute_context_rate", 0)
    avg_response_requested = metrics.get("avg_requested_verbosity", "normal")

    if execute_context_rate > 0.5 and avg_response_requested == "minimal":
        return EpisodicHint(
            hint_id="prefers_code_only_when_executing",
            category=EpisodeCategory.RESPONSE_STYLE,
            confidence=0.75,
            occurrence_count=1
        )
    return None


def _detect_example_seeker_pattern(metrics: Dict[str, Any]) -> Optional[EpisodicHint]:
    """Detect if user often asks for examples."""
    example_requests = metrics.get("example_requests", 0)

    if example_requests >= MIN_OCCURRENCES_TO_EMIT:
        return EpisodicHint(
            hint_id="often_asks_for_examples",
            category=EpisodeCategory.LEARNING_STYLE,
            confidence=min(0.9, 0.5 + example_requests * 0.1),
            occurrence_count=example_requests
        )
    return None


def _detect_tradeoff_preference(metrics: Dict[str, Any]) -> Optional[EpisodicHint]:
    """Detect if user appreciates tradeoff discussions."""
    design_rate = metrics.get("design_context_rate", 0)
    deep_pace_rate = metrics.get("deep_pace_rate", 0)

    if design_rate > 0.3 and deep_pace_rate > 0.4:
        return EpisodicHint(
            hint_id="appreciates_tradeoff_discussions",
            category=EpisodeCategory.RESPONSE_STYLE,
            confidence=min(0.8, 0.5 + design_rate * 0.5),
            occurrence_count=1
        )
    return None


def get_episode_hints(
    v2_memories: List[Dict[str, Any]] = None,
    conversation_metrics: Dict[str, Any] = None,
    rollout_metrics: Dict[str, Any] = None
) -> EpisodicContext:
    """
    Derive episodic hints from available data.

    IMPORTANT: This is computed fresh each time - NO storage.

    Args:
        v2_memories: V2 memory items (for preference patterns)
        conversation_metrics: Aggregated conversation stats
        rollout_metrics: V3 rollout metrics

    Returns:
        EpisodicContext with derived hints
    """
    if not V3_EPISODIC_ENABLED:
        return EpisodicContext(hints=[])

    # Aggregate metrics for pattern detection
    metrics = {}

    # Extract from rollout metrics
    if rollout_metrics:
        kpis = rollout_metrics.get("kpis", {})
        metrics["follow_up_rate"] = kpis.get("follow_up_rate", 0)
        metrics["re_ask_count"] = kpis.get("re_ask_count", 0)
        metrics["avg_conversation_turns"] = kpis.get("turns", 0)

        distribution = rollout_metrics.get("distribution", {})
        context_dist = distribution.get("context_mode", {})
        total_contexts = sum(context_dist.values()) or 1

        metrics["design_context_rate"] = context_dist.get("design", 0) / total_contexts
        metrics["execute_context_rate"] = context_dist.get("execute", 0) / total_contexts

        pace_dist = distribution.get("pace", {})
        total_pace = sum(pace_dist.values()) or 1
        metrics["deep_pace_rate"] = pace_dist.get("deep", 0) / total_pace

    # Extract from conversation metrics
    if conversation_metrics:
        metrics.update(conversation_metrics)

    # Derive from V2 preferences
    if v2_memories:
        # Count preference patterns
        for mem in v2_memories:
            mem_type = mem.get("type", "")
            value = str(mem.get("value", "")).lower()

            if "example" in value:
                metrics["example_requests"] = metrics.get("example_requests", 0) + 1
            if "simple" in value or "simplify" in value:
                metrics["simplification_requests"] = metrics.get("simplification_requests", 0) + 1

    # Run pattern detectors
    detectors = [
        _detect_simplification_pattern,
        _detect_iteration_pattern,
        _detect_challenger_pattern,
        _detect_code_only_pattern,
        _detect_example_seeker_pattern,
        _detect_tradeoff_preference,
    ]

    hints = []
    for detector in detectors:
        hint = detector(metrics)
        if hint:
            hints.append(hint)

    # Sort by confidence, cap at MAX_EPISODES
    hints.sort(key=lambda h: h.confidence, reverse=True)
    hints = hints[:MAX_EPISODES]

    return EpisodicContext(hints=hints)

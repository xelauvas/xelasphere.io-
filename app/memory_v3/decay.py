"""
Memory V3 Week 2 - Relevance-based Decay
========================================
Stops the AI from using outdated signals.

Key principle:
- Nothing is deleted
- Nothing is stored
- Just down-weighting influence based on relevance

This prevents stale preferences from influencing current behavior.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from enum import Enum

# Feature flag
V3_DECAY_ENABLED = os.getenv("V3_DECAY_ENABLED", "true").lower() in ("true", "1", "yes")

# Decay configuration
RELEVANCE_DECAY_DAYS = 90      # Start decay after 90 days of no relevance
FULL_DECAY_DAYS = 180          # Minimum weight after 180 days
MIN_WEIGHT = 0.1               # Never go below this weight
CONTEXT_MISMATCH_PENALTY = 0.5 # Penalty for context mismatch


class DecayReason(Enum):
    """Why a signal was decayed."""
    NONE = "none"
    TIME_BASED = "time_based"          # Not used recently
    CONTEXT_MISMATCH = "context_mismatch"  # Doesn't match current context
    TOPIC_IRRELEVANT = "topic_irrelevant"   # Topic doesn't match query


@dataclass
class DecayedAdjustment:
    """An adjustment with its decay weight applied."""
    adjustment: str
    original_weight: float
    decayed_weight: float
    decay_reason: DecayReason
    included: bool  # Whether to include in final prompt


@dataclass
class DecayResult:
    """Result of applying decay to adjustments."""
    adjustments: List[str]          # Final adjustments to inject
    decayed_out: List[str]          # Adjustments removed due to decay
    decay_details: List[DecayedAdjustment]  # Full details for logging


def _calculate_time_decay(last_used: Optional[datetime]) -> float:
    """
    Calculate decay weight based on time since last use.

    Returns 1.0 for recent use, down to MIN_WEIGHT for old.
    """
    if last_used is None:
        return 1.0  # No data = assume relevant

    days_since = (datetime.utcnow() - last_used).days

    if days_since <= RELEVANCE_DECAY_DAYS:
        return 1.0

    if days_since >= FULL_DECAY_DAYS:
        return MIN_WEIGHT

    # Linear decay between thresholds
    decay_range = FULL_DECAY_DAYS - RELEVANCE_DECAY_DAYS
    decay_progress = (days_since - RELEVANCE_DECAY_DAYS) / decay_range
    return max(MIN_WEIGHT, 1.0 - (1.0 - MIN_WEIGHT) * decay_progress)


def _calculate_context_relevance(
    adjustment: str,
    context_mode: str,
    topics: List[str] = None
) -> float:
    """
    Calculate relevance weight based on context match.

    Some adjustments are more relevant in certain contexts.
    """
    # Context-adjustment relevance matrix
    relevance_matrix = {
        "debug": {
            "concise": 1.0,
            "direct": 1.0,
            "step_by_step": 0.8,
            "examples": 0.6,
            "tradeoffs": 0.4,
            "reassurance": 0.5,
        },
        "execute": {
            "concise": 1.0,
            "direct": 1.0,
            "step_by_step": 0.7,
            "examples": 0.8,
            "tradeoffs": 0.3,
            "reassurance": 0.3,
        },
        "design": {
            "concise": 0.5,
            "direct": 0.7,
            "step_by_step": 0.9,
            "examples": 0.8,
            "tradeoffs": 1.0,
            "reassurance": 0.6,
        },
        "explain": {
            "concise": 0.4,
            "direct": 0.6,
            "step_by_step": 1.0,
            "examples": 1.0,
            "tradeoffs": 0.7,
            "reassurance": 0.8,
        },
    }

    # Get relevance from matrix
    context_adjustments = relevance_matrix.get(context_mode, {})
    return context_adjustments.get(adjustment, 0.7)  # Default 0.7 for unknown


def apply_relevance_decay(
    adjustments: List[str],
    context_mode: str,
    v2_memories: List[Dict[str, Any]] = None,
    topics: List[str] = None,
    threshold: float = 0.3
) -> DecayResult:
    """
    Apply relevance-based decay to behavior adjustments.

    Args:
        adjustments: Raw adjustments from V3 state
        context_mode: Current context (debug, design, execute, explain)
        v2_memories: V2 memory items for time-based decay (optional)
        topics: Current query topics (optional)
        threshold: Minimum weight to include adjustment (default 0.3)

    Returns:
        DecayResult with filtered adjustments
    """
    if not V3_DECAY_ENABLED:
        return DecayResult(
            adjustments=adjustments,
            decayed_out=[],
            decay_details=[]
        )

    # Build memory usage map (adjustment -> last used time)
    memory_usage = {}
    if v2_memories:
        for mem in v2_memories:
            # Check if memory relates to any adjustment
            mem_value = str(mem.get("value", "")).lower()
            lifecycle = mem.get("lifecycle", {})

            # Try to get last_used_at
            last_used = None
            if isinstance(lifecycle, dict):
                last_used_str = lifecycle.get("last_used_at")
                if last_used_str:
                    try:
                        last_used = datetime.fromisoformat(last_used_str)
                    except (ValueError, TypeError):
                        pass

            # Map common preference values to adjustments
            if "concise" in mem_value or "brief" in mem_value:
                memory_usage["concise"] = last_used
            if "example" in mem_value:
                memory_usage["examples"] = last_used
            if "step" in mem_value:
                memory_usage["step_by_step"] = last_used
            if "tradeoff" in mem_value or "trade-off" in mem_value:
                memory_usage["tradeoffs"] = last_used

    # Apply decay to each adjustment
    details = []
    final_adjustments = []
    decayed_out = []

    for adj in adjustments:
        # Calculate time-based decay
        last_used = memory_usage.get(adj)
        time_weight = _calculate_time_decay(last_used)

        # Calculate context relevance
        context_weight = _calculate_context_relevance(adj, context_mode, topics)

        # Combined weight
        final_weight = time_weight * context_weight

        # Determine decay reason
        if time_weight < 1.0:
            reason = DecayReason.TIME_BASED
        elif context_weight < 0.5:
            reason = DecayReason.CONTEXT_MISMATCH
        else:
            reason = DecayReason.NONE

        # Include if above threshold
        included = final_weight >= threshold

        details.append(DecayedAdjustment(
            adjustment=adj,
            original_weight=1.0,
            decayed_weight=final_weight,
            decay_reason=reason,
            included=included
        ))

        if included:
            final_adjustments.append(adj)
        else:
            decayed_out.append(adj)

    return DecayResult(
        adjustments=final_adjustments,
        decayed_out=decayed_out,
        decay_details=details
    )


def get_decay_summary(result: DecayResult) -> str:
    """Get a summary of decay effects for logging."""
    if not result.decayed_out:
        return "no decay applied"

    return f"decayed {len(result.decayed_out)}: {', '.join(result.decayed_out)}"

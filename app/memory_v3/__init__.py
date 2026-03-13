"""
Memory V3 - Adaptive Intelligence Layer
========================================

V3 sits on top of V2 as a behavior intelligence layer.
It produces signals, never facts. Signals never touch the DB.

V2 = what to remember (safe facts)
V3 = how to behave (adaptive judgment)

Components:
- context.py: Task mode detection (DESIGN, EXPLAIN, EXECUTE, DEBUG)
- state.py: Situational awareness (mode, pace, certainty)
- orchestrator.py: Combines V2 + V3 signals

Usage:
    from app.memory_v3 import get_v3_context, V3_ENABLED

    # Check if V3 is enabled
    if V3_ENABLED:
        v3_block = get_v3_context(user_message, conversation_history)
        # Inject v3_block into prompt

CONTRACT (non-negotiable):
- V3 produces signals, never facts
- Signals never touch the DB
- Signals expire or are recomputed
- V3 never bypasses V2 safeguards
"""

import os
import logging

logger = logging.getLogger(__name__)

# Feature flag - can be toggled via environment variable
V3_ENABLED = os.getenv("MEMORY_V3_ENABLED", "true").lower() in ("true", "1", "yes")

# Public API
from .context import (
    ContextMode,
    ContextSignal,
    detect_context_mode,
    get_behavior_adjustments as get_context_adjustments,
)

from .state import (
    UserMode,
    UserPace,
    UserCertainty,
    UserState,
    infer_user_state,
    get_behavior_adjustments as get_state_adjustments,
)

from .orchestrator import (
    V3Signal,
    V3Orchestrator,
    get_v3_orchestrator,
    generate_v3_signal,
    get_v3_prompt_block,
    get_v3_eval_metadata,
    run_shadow_analysis,
    V3_INTENSITY,
)

from .rollout import (
    V3Metrics,
    V3RolloutManager,
    RolloutPhase,
    get_rollout_manager,
    should_inject_v3,
    get_v3_cohort,
    record_v3_metrics,
    check_shadow_exit_criteria,
    check_injection_exit_criteria,
)

from .config import (
    SuccessThresholds,
    DEFAULT_THRESHOLDS,
    CustomerType,
    CustomerPreset,
    CUSTOMER_PRESETS,
    get_preset,
    get_preset_by_name,
    get_v3_config,
    apply_preset,
)

# Week 2 features
from .meta import (
    MetaConfidence,
    ConfidenceLevel,
    ClarifyingStyle,
    infer_meta_confidence,
    V3_META_ENABLED,
)

from .decay import (
    DecayResult,
    DecayReason,
    apply_relevance_decay,
    get_decay_summary,
    V3_DECAY_ENABLED,
)

from .episodic import (
    EpisodicHint,
    EpisodicContext,
    EpisodeCategory,
    get_episode_hints,
    V3_EPISODIC_ENABLED,
)


def get_v3_context(
    user_message: str,
    conversation_history: list = None,
    v2_context: str = "",
) -> str:
    """
    Main entry point for V3 behavior signals.

    Combines V2 memory context with V3 behavior signals
    to produce a complete context block for the prompt.

    Args:
        user_message: Current user message
        conversation_history: Recent conversation
        v2_context: Memory context from V2 retrieval

    Returns:
        Combined context string for prompt injection
    """
    if not V3_ENABLED:
        return v2_context

    try:
        # Generate V3 signal
        v3_block = get_v3_prompt_block(
            user_message,
            conversation_history,
            enabled=V3_ENABLED
        )

        # Combine V2 + V3
        if v2_context and v3_block:
            return f"{v2_context}\n\n{v3_block}"
        elif v3_block:
            return v3_block
        else:
            return v2_context

    except Exception as e:
        logger.error(f"V3 context generation failed: {e}")
        return v2_context  # Fall back to V2 only


def reset_v3_state():
    """Reset V3 ephemeral state (call on conversation switch)."""
    orchestrator = get_v3_orchestrator()
    orchestrator.reset_state()


# Log V3 status on import
if V3_ENABLED:
    logger.info("Memory V3 enabled - adaptive behavior layer active")
else:
    logger.info("Memory V3 disabled - using V2 only")


__all__ = [
    # Feature flag
    "V3_ENABLED",
    # Main entry point
    "get_v3_context",
    "reset_v3_state",
    # Context
    "ContextMode",
    "ContextSignal",
    "detect_context_mode",
    "get_context_adjustments",
    # State
    "UserMode",
    "UserPace",
    "UserCertainty",
    "UserState",
    "infer_user_state",
    "get_state_adjustments",
    # Orchestrator
    "V3Signal",
    "V3Orchestrator",
    "get_v3_orchestrator",
    "generate_v3_signal",
    "get_v3_prompt_block",
    "get_v3_eval_metadata",
    "run_shadow_analysis",
    "V3_INTENSITY",
    # Rollout
    "V3Metrics",
    "V3RolloutManager",
    "RolloutPhase",
    "get_rollout_manager",
    "should_inject_v3",
    "get_v3_cohort",
    "record_v3_metrics",
    "check_shadow_exit_criteria",
    "check_injection_exit_criteria",
    # Config & Presets
    "SuccessThresholds",
    "DEFAULT_THRESHOLDS",
    "CustomerType",
    "CustomerPreset",
    "CUSTOMER_PRESETS",
    "get_preset",
    "get_preset_by_name",
    "get_v3_config",
    "apply_preset",
    # Week 2: Meta-confidence
    "MetaConfidence",
    "ConfidenceLevel",
    "ClarifyingStyle",
    "infer_meta_confidence",
    "V3_META_ENABLED",
    # Week 2: Relevance decay
    "DecayResult",
    "DecayReason",
    "apply_relevance_decay",
    "get_decay_summary",
    "V3_DECAY_ENABLED",
    # Week 2: Episodic hints
    "EpisodicHint",
    "EpisodicContext",
    "EpisodeCategory",
    "get_episode_hints",
    "V3_EPISODIC_ENABLED",
]

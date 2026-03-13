"""
Memory V3 - Orchestrator
========================
Thin layer that combines V2 memory with V3 behavior signals.

The orchestrator is intentionally DUMB. It just:
1. Collects V3 signals
2. Combines them with V2 context
3. Produces posture hints for the prompt

CONTRACT:
- Never bypasses V2 safeguards
- Never stores anything
- Only adds signals, not facts
- Can be disabled via flag
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any

from .context import ContextSignal, ContextMode, detect_context_mode, get_behavior_adjustments as get_context_adjustments
from .state import UserState, UserCertainty, infer_user_state, get_behavior_adjustments as get_state_adjustments

# Week 2 imports
from .meta import MetaConfidence, infer_meta_confidence, V3_META_ENABLED
from .decay import apply_relevance_decay, DecayResult, V3_DECAY_ENABLED
from .episodic import get_episode_hints, EpisodicContext, V3_EPISODIC_ENABLED

logger = logging.getLogger(__name__)

# Shadow mode logger - separate for easy filtering
shadow_logger = logging.getLogger(__name__ + ".shadow")

# Anti-gaming configuration
FRUSTRATED_COOLDOWN_SECONDS = 60  # Min time between frustrated state escalations
MAX_FRUSTRATED_PER_CONVERSATION = 5  # Max frustrated states per conversation

# V3 Intensity - tuning knob for behavior adjustment strength
# 0.0 = V3 computes signals but applies no adjustments (effectively shadow mode)
# 0.5 = moderate adjustments
# 1.0 = full adjustments
# Default 0.6 is a safe starting point
import os
V3_INTENSITY = float(os.getenv("V3_INTENSITY", "0.6"))


@dataclass
class AntiGamingState:
    """
    Tracks state for anti-gaming protection.

    Prevents users from gaming V3 by pretending to be frustrated
    or cycling through states rapidly.
    """
    last_frustrated_at: Optional[datetime] = None
    frustrated_count: int = 0
    total_state_changes: int = 0
    last_state_mode: Optional[str] = None


@dataclass
class V3Signal:
    """
    Combined V3 behavior signal - ephemeral, never stored.

    This is what gets injected into the prompt as "posture".
    """
    context: ContextSignal
    state: UserState
    generated_at: datetime = field(default_factory=datetime.utcnow)

    # Combined behavior adjustments
    verbosity: str = "medium"  # low, medium, high
    tone: str = "neutral"      # efficient, neutral, thorough, patient
    structure: str = "normal"  # direct, normal, step_by_step, diagnostic
    ask_clarifying: bool = False
    offer_alternatives: bool = False
    be_reassuring: bool = False
    include_tradeoffs: bool = False

    # Week 2 additions
    meta_confidence: Optional[MetaConfidence] = None
    episodic_context: Optional[EpisodicContext] = None
    decay_result: Optional[DecayResult] = None

    def to_prompt_block(self, intensity: float = None) -> str:
        """
        Generate the V3 behavior block for prompt injection.

        This is posture, not instructions. Format is stable YAML-like
        for easy debugging and diffing.

        Intensity levels:
        - 0.0: No block (disabled)
        - 0.1-0.3: Context only (task type)
        - 0.4-0.5: + State info (pace, mode, certainty)
        - 0.6+: + Behavior adjustments

        Args:
            intensity: Override intensity (0.0-1.0). If None, uses V3_INTENSITY env var.
        """
        if intensity is None:
            intensity = V3_INTENSITY

        # At intensity 0, return empty (disabled)
        if intensity <= 0:
            return ""

        lines = [
            "<!-- V3 Behavior Signal v1 -->",
            f"context_mode: {self.context.mode.value.upper()}",
        ]

        # Low intensity (0.1-0.3): Context only - just task type
        if intensity < 0.4:
            lines.append(f"intensity: {intensity}")
            lines.append("adjustments_enabled: false")
            lines.append("<!-- End V3 Signal -->")
            return "\n".join(lines)

        # Medium intensity (0.4-0.5): Add state info
        lines.append("state:")
        lines.append(f"  pace: {self.state.pace.value}")
        lines.append(f"  mode: {self.state.mode.value}")
        lines.append(f"  certainty: {self.state.certainty.value}")
        lines.append(f"intensity: {intensity}")

        # Below 0.6: No behavior adjustments yet
        if intensity < 0.6:
            lines.append("adjustments_enabled: false")
            lines.append("<!-- End V3 Signal -->")
            return "\n".join(lines)

        # High intensity (0.6+): Add behavior adjustments
        lines.append("adjustments_enabled: true")

        # Build adjustment list
        adjustments = []

        # Verbosity adjustments
        if self.verbosity == "low":
            adjustments.append("concise")
        elif self.verbosity == "high":
            adjustments.append("thorough")

        # Tone adjustments
        if self.tone == "patient":
            adjustments.append("patient")
        elif self.tone == "efficient":
            adjustments.append("direct")

        # Structure adjustments
        if self.structure == "step_by_step":
            adjustments.append("step_by_step")
        elif self.structure == "diagnostic":
            adjustments.append("diagnostic")

        # Boolean adjustments (0.6+)
        if self.ask_clarifying:
            adjustments.append("ask_clarifying")
        if self.offer_alternatives:
            adjustments.append("offer_alternatives")

        # Higher intensity (0.7+)
        if intensity >= 0.7:
            if self.be_reassuring:
                adjustments.append("reassuring")
            if self.include_tradeoffs:
                adjustments.append("include_tradeoffs")

        # Safety rule: Even in concise mode, always include minimum context
        # This prevents over-terse responses that cause re-asks
        if "concise" in adjustments and intensity >= 0.8:
            adjustments.append("include_one_line_context")

        # Apply decay to adjustments if enabled
        if self.decay_result and V3_DECAY_ENABLED:
            adjustments = self.decay_result.adjustments  # Use decayed adjustments

        if adjustments:
            lines.append("behavior_adjustments:")
            for adj in adjustments:
                lines.append(f"  - {adj}")
        else:
            lines.append("behavior_adjustments: []")

        # Week 2: Add meta-confidence (if enabled and relevant)
        if self.meta_confidence and V3_META_ENABLED:
            meta_block = self.meta_confidence.to_prompt_block()
            if meta_block:
                lines.append("# Week 2: Meta-confidence")
                lines.extend(meta_block.split("\n"))

        # Week 2: Add episodic hints (if enabled and available)
        if self.episodic_context and V3_EPISODIC_ENABLED:
            episode_block = self.episodic_context.to_prompt_block()
            if episode_block:
                lines.append("# Week 2: Episodic hints")
                lines.append(episode_block)

        lines.append("<!-- End V3 Signal -->")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for logging/debugging (never for storage)."""
        return {
            "context_mode": self.context.mode.value,
            "context_confidence": self.context.confidence,
            "state_mode": self.state.mode.value,
            "state_pace": self.state.pace.value,
            "state_certainty": self.state.certainty.value,
            "state_ttl_remaining": self.state.remaining_ttl_seconds(),
            "verbosity": self.verbosity,
            "tone": self.tone,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_eval_metadata(self, v3_enabled: bool = True) -> Dict[str, Any]:
        """
        Generate eval metadata for analysis.

        This is safe to log/analyze - no user data, only behavioral signals.
        Used for:
        - Eval pipelines
        - Response quality analysis
        - V3 effectiveness tracking

        Returns:
            Dict with context_mode, user_state, state_expires_at, v3_enabled, signals_used
        """
        metadata = {
            "v3_enabled": v3_enabled,
            "signals_used": True,
            "context_mode": self.context.mode.value,
            "context_confidence": round(self.context.confidence, 2),
            "context_reason": self.context.reason,
            "user_state": {
                "mode": self.state.mode.value,
                "pace": self.state.pace.value,
                "certainty": self.state.certainty.value,
            },
            "state_expires_at": self.state.expires_at.isoformat(),
            "state_ttl_remaining_seconds": self.state.remaining_ttl_seconds(),
            "behavior_adjustments": {
                "verbosity": self.verbosity,
                "tone": self.tone,
                "structure": self.structure,
                "ask_clarifying": self.ask_clarifying,
                "offer_alternatives": self.offer_alternatives,
                "be_reassuring": self.be_reassuring,
                "include_tradeoffs": self.include_tradeoffs,
            },
            "generated_at": self.generated_at.isoformat(),
        }

        # Week 2: Add meta-confidence metadata
        if self.meta_confidence and V3_META_ENABLED:
            metadata["meta_confidence"] = {
                "confidence_level": self.meta_confidence.confidence_level.value,
                "should_ask_clarifying": self.meta_confidence.should_ask_clarifying,
                "max_clarifying_questions": self.meta_confidence.max_clarifying_questions,
                "clarifying_style": self.meta_confidence.clarifying_style.value,
                "restrict_to_info_only": self.meta_confidence.restrict_to_info_only,
                "reason": self.meta_confidence.reason,
            }

        # Week 2: Add decay metadata
        if self.decay_result and V3_DECAY_ENABLED:
            metadata["decay"] = {
                "final_adjustments": self.decay_result.adjustments,
                "decayed_out": self.decay_result.decayed_out,
                "decay_applied": len(self.decay_result.decayed_out) > 0,
            }

        # Week 2: Add episodic metadata (if enabled)
        if self.episodic_context and V3_EPISODIC_ENABLED:
            metadata["episodic"] = {
                "hints": self.episodic_context.get_labels(),
                "hint_count": len(self.episodic_context.hints),
            }

        return metadata


class V3Orchestrator:
    """
    Orchestrates V3 behavior signals.

    Intentionally simple - just collects signals and combines them.
    Does NOT make complex decisions.

    Modes:
    - enabled=True, shadow=False: Full V3 (inject into prompts)
    - enabled=True, shadow=True: Shadow mode (run silently, log only)
    - enabled=False: Disabled (no V3 processing)
    """

    def __init__(self, enabled: bool = True, shadow_mode: bool = False):
        """
        Initialize orchestrator.

        Args:
            enabled: Whether V3 is enabled (feature flag)
            shadow_mode: If True, run V3 but don't inject into prompts (log only)
        """
        self.enabled = enabled
        self.shadow_mode = shadow_mode
        self._previous_state: Optional[UserState] = None
        self._shadow_signal_count = 0  # Track shadow signals for metrics
        self._anti_gaming = AntiGamingState()  # Anti-gaming protection

    def generate_signal(
        self,
        user_message: str,
        conversation_history: Optional[List[dict]] = None,
    ) -> Optional[V3Signal]:
        """
        Generate V3 behavior signal for current message.

        Args:
            user_message: Current user message
            conversation_history: Recent conversation

        Returns:
            V3Signal if enabled, None if disabled
        """
        if not self.enabled:
            return None

        if not user_message or not user_message.strip():
            return None

        try:
            # Detect context
            context = detect_context_mode(user_message, conversation_history)

            # Infer state (considers previous state if not expired)
            state = infer_user_state(
                user_message,
                conversation_history,
                previous_state=self._previous_state
            )

            # Apply anti-gaming protection
            state = self._apply_anti_gaming(state)

            # Store for next message
            self._previous_state = state

            # Combine adjustments
            signal = self._combine_signals(context, state)

            # Log differently based on mode
            if self.shadow_mode:
                self._shadow_signal_count += 1
                shadow_logger.info(
                    f"[SHADOW #{self._shadow_signal_count}] V3 Signal: "
                    f"context={context.mode.value}, "
                    f"state={state.mode.value}/{state.pace.value}/{state.certainty.value}, "
                    f"verbosity={signal.verbosity}, tone={signal.tone}"
                )
                # In shadow mode, return None so it doesn't get injected
                # but signal was generated and logged for analysis
                return None
            else:
                logger.info(
                    f"V3 Signal: context={context.mode.value}, "
                    f"state={state.mode.value}/{state.pace.value}/{state.certainty.value}"
                )

            return signal

        except Exception as e:
            logger.error(f"V3 orchestrator error: {e}")
            return None

    def _apply_anti_gaming(self, state: UserState) -> UserState:
        """
        Apply anti-gaming protection to state.

        Prevents gaming by:
        1. Rate-limiting frustrated state (cooldown period)
        2. Capping total frustrated states per conversation
        3. Detecting rapid state cycling (future)

        Returns modified state if gaming detected, otherwise original state.
        """
        from .state import UserCertainty as UC

        # Track state changes
        current_mode = state.certainty.value
        if self._anti_gaming.last_state_mode != current_mode:
            self._anti_gaming.total_state_changes += 1
            self._anti_gaming.last_state_mode = current_mode

        # Check for frustrated state
        if state.certainty == UC.FRUSTRATED:
            now = datetime.utcnow()

            # Check cooldown
            if self._anti_gaming.last_frustrated_at:
                elapsed = (now - self._anti_gaming.last_frustrated_at).total_seconds()
                if elapsed < FRUSTRATED_COOLDOWN_SECONDS:
                    logger.info(
                        f"Anti-gaming: Frustrated state rate-limited "
                        f"({elapsed:.0f}s < {FRUSTRATED_COOLDOWN_SECONDS}s cooldown)"
                    )
                    # Downgrade to uncertain instead of frustrated
                    return UserState(
                        mode=state.mode,
                        pace=state.pace,
                        certainty=UC.UNCERTAIN,  # Downgrade
                        created_at=state.created_at,
                        expires_at=state.expires_at,
                    )

            # Check max count
            if self._anti_gaming.frustrated_count >= MAX_FRUSTRATED_PER_CONVERSATION:
                logger.info(
                    f"Anti-gaming: Frustrated state capped "
                    f"({self._anti_gaming.frustrated_count} >= {MAX_FRUSTRATED_PER_CONVERSATION})"
                )
                # Downgrade to uncertain
                return UserState(
                    mode=state.mode,
                    pace=state.pace,
                    certainty=UC.UNCERTAIN,  # Downgrade
                    created_at=state.created_at,
                    expires_at=state.expires_at,
                )

            # Valid frustrated state - track it
            self._anti_gaming.last_frustrated_at = now
            self._anti_gaming.frustrated_count += 1
            logger.debug(
                f"Anti-gaming: Frustrated state #{self._anti_gaming.frustrated_count} allowed"
            )

        return state

    def _combine_signals(self, context: ContextSignal, state: UserState) -> V3Signal:
        """Combine context and state into unified signal."""
        context_adj = get_context_adjustments(context)
        state_adj = get_state_adjustments(state)

        # Merge adjustments - state overrides context for immediate behavior
        verbosity = state_adj.get("verbosity", context_adj.get("verbosity", "medium"))
        tone = state_adj.get("tone", context_adj.get("tone", "neutral"))

        # Structure comes from context (what we're doing)
        structure_map = {
            ContextMode.DESIGN: "organized",
            ContextMode.EXPLAIN: "step_by_step",
            ContextMode.EXECUTE: "direct",
            ContextMode.DEBUG: "diagnostic",
        }
        structure = structure_map.get(context.mode, "normal")

        # Boolean flags - OR them together
        ask_clarifying = context_adj.get("ask_clarifying", False) or state_adj.get("ask_clarifying", False)
        offer_alternatives = state_adj.get("offer_alternatives", False)
        be_reassuring = state_adj.get("be_reassuring", False)
        include_tradeoffs = context_adj.get("include_tradeoffs", False)

        return V3Signal(
            context=context,
            state=state,
            verbosity=verbosity,
            tone=tone,
            structure=structure,
            ask_clarifying=ask_clarifying,
            offer_alternatives=offer_alternatives,
            be_reassuring=be_reassuring,
            include_tradeoffs=include_tradeoffs,
        )

    def apply_week2(
        self,
        signal: V3Signal,
        user_message: str,
        v2_memories: List[Dict[str, Any]] = None,
        rollout_metrics: Dict[str, Any] = None
    ) -> V3Signal:
        """
        Apply Week 2 enhancements to a V3 signal.

        Week 2 adds:
        1. Meta-confidence (ask vs assume)
        2. Relevance decay (down-weight stale signals)
        3. Episodic hints (behavioral patterns)

        All are optional, read-only, and behavior-only.

        Args:
            signal: Base V3 signal from Week 1
            user_message: Current user message
            v2_memories: V2 memory items (for decay/episodes)
            rollout_metrics: V3 rollout metrics (for episodes)

        Returns:
            Enhanced V3Signal with Week 2 features
        """
        # 1. Meta-confidence: Should we ask or assume?
        if V3_META_ENABLED:
            signal.meta_confidence = infer_meta_confidence(
                context_mode=signal.context.mode.value,
                state_certainty=signal.state.certainty.value,
                state_pace=signal.state.pace.value,
                user_message=user_message
            )

        # 2. Relevance decay: Down-weight stale adjustments
        if V3_DECAY_ENABLED:
            # Build current adjustments list
            adjustments = []
            if signal.verbosity == "low":
                adjustments.append("concise")
            elif signal.verbosity == "high":
                adjustments.append("thorough")
            if signal.tone == "patient":
                adjustments.append("patient")
            elif signal.tone == "efficient":
                adjustments.append("direct")
            if signal.structure == "step_by_step":
                adjustments.append("step_by_step")
            if signal.include_tradeoffs:
                adjustments.append("tradeoffs")
            if signal.be_reassuring:
                adjustments.append("reassurance")

            signal.decay_result = apply_relevance_decay(
                adjustments=adjustments,
                context_mode=signal.context.mode.value,
                v2_memories=v2_memories
            )

        # 3. Episodic hints: Behavioral patterns
        if V3_EPISODIC_ENABLED:
            signal.episodic_context = get_episode_hints(
                v2_memories=v2_memories,
                rollout_metrics=rollout_metrics
            )

        return signal

    def generate_signal_with_week2(
        self,
        user_message: str,
        conversation_history: Optional[List[dict]] = None,
        v2_memories: List[Dict[str, Any]] = None,
        rollout_metrics: Dict[str, Any] = None
    ) -> Optional[V3Signal]:
        """
        Generate V3 signal with Week 2 enhancements.

        Use this instead of generate_signal when you want
        full Week 2 features (meta-confidence, decay, episodic).

        Args:
            user_message: Current user message
            conversation_history: Recent conversation
            v2_memories: V2 memory items
            rollout_metrics: V3 rollout metrics

        Returns:
            Enhanced V3Signal or None
        """
        # First generate base signal
        signal = self.generate_signal(user_message, conversation_history)

        if signal is None:
            return None

        # Then apply Week 2 enhancements
        return self.apply_week2(
            signal=signal,
            user_message=user_message,
            v2_memories=v2_memories,
            rollout_metrics=rollout_metrics
        )

    def reset_state(self):
        """Reset ephemeral state (e.g., on conversation change)."""
        self._previous_state = None
        self._anti_gaming = AntiGamingState()  # Also reset anti-gaming state

    def generate_shadow_signal(
        self,
        user_message: str,
        conversation_history: Optional[List[dict]] = None,
    ) -> Optional[V3Signal]:
        """
        Generate V3 signal in shadow mode (for eval/analysis).

        Unlike generate_signal, this ALWAYS returns the signal (doesn't return None).
        Use this when you want to analyze what V3 would do without affecting the prompt.

        Args:
            user_message: Current user message
            conversation_history: Recent conversation

        Returns:
            V3Signal (always, unless error or empty message)
        """
        if not user_message or not user_message.strip():
            return None

        try:
            # Detect context
            context = detect_context_mode(user_message, conversation_history)

            # Infer state (considers previous state if not expired)
            state = infer_user_state(
                user_message,
                conversation_history,
                previous_state=self._previous_state
            )

            # Don't store state in shadow mode (keep it read-only)
            # self._previous_state = state

            # Combine adjustments
            signal = self._combine_signals(context, state)

            self._shadow_signal_count += 1
            shadow_logger.info(
                f"[SHADOW #{self._shadow_signal_count}] Analysis: "
                f"context={context.mode.value}, "
                f"state={state.mode.value}/{state.pace.value}/{state.certainty.value}"
            )

            return signal

        except Exception as e:
            shadow_logger.error(f"Shadow signal error: {e}")
            return None

    def get_shadow_stats(self) -> Dict[str, Any]:
        """Get shadow mode statistics."""
        return {
            "shadow_signals_generated": self._shadow_signal_count,
            "shadow_mode_enabled": self.shadow_mode,
        }


# Global orchestrator instance (can be replaced for testing)
_orchestrator: Optional[V3Orchestrator] = None


def get_v3_orchestrator(enabled: bool = True, shadow_mode: bool = False) -> V3Orchestrator:
    """Get or create the V3 orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = V3Orchestrator(enabled=enabled, shadow_mode=shadow_mode)
    else:
        _orchestrator.enabled = enabled
        _orchestrator.shadow_mode = shadow_mode
    return _orchestrator


def generate_v3_signal(
    user_message: str,
    conversation_history: Optional[List[dict]] = None,
    enabled: bool = True
) -> Optional[V3Signal]:
    """
    Convenience function to generate V3 signal.

    Args:
        user_message: Current message
        conversation_history: Recent conversation
        enabled: Whether V3 is enabled

    Returns:
        V3Signal or None
    """
    orchestrator = get_v3_orchestrator(enabled)
    return orchestrator.generate_signal(user_message, conversation_history)


def get_v3_prompt_block(
    user_message: str,
    conversation_history: Optional[List[dict]] = None,
    enabled: bool = True
) -> str:
    """
    Convenience function to get V3 prompt block.

    Args:
        user_message: Current message
        conversation_history: Recent conversation
        enabled: Whether V3 is enabled

    Returns:
        Prompt block string (empty if disabled or no signal)
    """
    signal = generate_v3_signal(user_message, conversation_history, enabled)
    if signal:
        return signal.to_prompt_block()
    return ""


def run_shadow_analysis(
    user_message: str,
    conversation_history: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    Run V3 analysis in shadow mode (doesn't affect prompts).

    Use this to analyze what V3 would do without actually changing behavior.
    Perfect for A/B testing, eval pipelines, and gradual rollouts.

    Args:
        user_message: Current message
        conversation_history: Recent conversation

    Returns:
        Dict with signal analysis and metadata
    """
    orchestrator = get_v3_orchestrator(enabled=True, shadow_mode=True)
    signal = orchestrator.generate_shadow_signal(user_message, conversation_history)

    if signal:
        return {
            "analyzed": True,
            "signal": signal.to_eval_metadata(v3_enabled=True),
            "would_inject": signal.to_prompt_block(),
            "shadow_stats": orchestrator.get_shadow_stats(),
        }

    return {
        "analyzed": False,
        "signal": None,
        "would_inject": "",
        "shadow_stats": orchestrator.get_shadow_stats(),
    }


def get_v3_eval_metadata(
    user_message: str,
    conversation_history: Optional[List[dict]] = None,
    enabled: bool = True
) -> Dict[str, Any]:
    """
    Get V3 eval metadata for analysis pipelines.

    Safe to log/store - contains no user PII, only behavioral signals.

    Args:
        user_message: Current message
        conversation_history: Recent conversation
        enabled: Whether V3 is enabled

    Returns:
        Dict with eval metadata (always returns something, even if disabled)
    """
    if not enabled:
        return {
            "v3_enabled": False,
            "signals_used": False,
            "context_mode": None,
            "user_state": None,
            "state_expires_at": None,
            "generated_at": datetime.utcnow().isoformat(),
        }

    signal = generate_v3_signal(user_message, conversation_history, enabled)
    if signal:
        return signal.to_eval_metadata(v3_enabled=True)

    # Signal generation failed
    return {
        "v3_enabled": True,
        "signals_used": False,
        "context_mode": None,
        "user_state": None,
        "state_expires_at": None,
        "error": "signal_generation_failed",
        "generated_at": datetime.utcnow().isoformat(),
    }

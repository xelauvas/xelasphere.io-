"""
Memory V3 - Context Detection
=============================
Detects what kind of task the conversation is about.

NOT who the user is. NOT how they feel.
Just: what are we doing right now?

CONTRACT:
- Produces signals, never facts
- Never writes to DB
- Signals are recomputed per message
"""

import re
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger(__name__)


class ContextMode(Enum):
    """The four fundamental task modes."""
    DESIGN = "design"       # architecture, systems, planning
    EXPLAIN = "explain"     # teaching, understanding
    EXECUTE = "execute"     # do X, generate Y
    DEBUG = "debug"         # fix, diagnose, why broken


@dataclass
class ContextSignal:
    """Context detection result - ephemeral, never stored."""
    mode: ContextMode
    confidence: float  # 0.0 - 1.0
    reason: str

    def to_prompt_hint(self) -> str:
        """Generate behavior hint for prompt injection."""
        hints = {
            ContextMode.DESIGN: "User is designing/planning. Be structured, discuss trade-offs, take time to be thorough.",
            ContextMode.EXPLAIN: "User wants to understand. Use simpler language, provide examples, teach step-by-step.",
            ContextMode.EXECUTE: "User wants action. Minimal explanation, direct output, get it done.",
            ContextMode.DEBUG: "User is debugging. Form hypotheses, be methodical, step-by-step diagnosis.",
        }
        return hints.get(self.mode, "")


# Detection patterns - rules first, fast and reliable
DEBUG_PATTERNS = [
    r'\b(bug|error|broken|fix|crash|crashing|fail|failing|issue|problem|wrong|doesn\'t work)\b',
    r'\b(why (is|does|did|isn\'t|doesn\'t))\b',
    r'\b(not working|stopped working|broke|keeps? crashing)\b',
    r'\b(traceback|exception|stack trace)\b',
    r'\b(debug|diagnose|troubleshoot)\b',
]

EXECUTE_PATTERNS = [
    r'^(create|make|build|generate|write|add|implement|do)\b',
    r'^(just|can you|please)\s+(create|make|build|generate|write|add)\b',
    r'\b(give me|show me|send me)\b',
    r'^(run|execute|deploy|install|setup)\b',
    r'\b(code for|script for|function for)\b',
]

EXPLAIN_PATTERNS = [
    r'^(what is|what are|what does)\b(?!.*\b(best|better|approach|architecture)\b)',
    r'^(how does|how do|how is|how are)\b',
    r'^(explain|describe|tell me about|help me understand)\b',
    r'^(why do|why does|why is|why are)\b(?!.*(?:broken|error|fail))',
    r'\b(mean|meaning|definition|concept)\b',
    r'^(teach me|show me how|walk me through)\b',
]

DESIGN_PATTERNS = [
    r'\b(architect|architecture|design|structure|organize)\b',
    r'\b(plan|planning|strategy|approach)\b',
    r'\b(should i|which is better|trade-?off|pros and cons)\b',
    r'\b(what\'?s?|what is).*\b(best|better|approach|architecture)\b',
    r'\b(best practice|pattern|how should)\b',
    r'\b(system|infrastructure|scalab|modular)\b',
    r'^(help me think|let\'s think|thinking about)\b',
]


def _matches_patterns(text: str, patterns: List[str]) -> bool:
    """Check if text matches any of the patterns."""
    text_lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def _count_pattern_matches(text: str, patterns: List[str]) -> int:
    """Count how many patterns match."""
    text_lower = text.lower()
    count = 0
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            count += 1
    return count


def detect_context_mode(
    user_message: str,
    conversation_history: Optional[List[dict]] = None,
    use_llm: bool = False,
    llm_client=None
) -> ContextSignal:
    """
    Detect the context mode for the current message.

    Uses hybrid approach:
    1. Rules first (fast, reliable)
    2. LLM only if ambiguous

    Args:
        user_message: Current user message
        conversation_history: Recent conversation for context
        use_llm: Whether to use LLM for ambiguous cases
        llm_client: LLM client for ambiguous detection

    Returns:
        ContextSignal with mode, confidence, and reason
    """
    if not user_message or not user_message.strip():
        return ContextSignal(
            mode=ContextMode.EXECUTE,
            confidence=0.5,
            reason="empty_message"
        )

    # Score each mode
    scores = {
        ContextMode.DEBUG: _count_pattern_matches(user_message, DEBUG_PATTERNS),
        ContextMode.EXECUTE: _count_pattern_matches(user_message, EXECUTE_PATTERNS),
        ContextMode.EXPLAIN: _count_pattern_matches(user_message, EXPLAIN_PATTERNS),
        ContextMode.DESIGN: _count_pattern_matches(user_message, DESIGN_PATTERNS),
    }

    # Find the highest scoring mode
    max_score = max(scores.values())

    if max_score == 0:
        # No clear signal - default to EXECUTE (most common)
        return ContextSignal(
            mode=ContextMode.EXECUTE,
            confidence=0.5,
            reason="no_clear_signal_default_execute"
        )

    # Get winning mode(s)
    winners = [mode for mode, score in scores.items() if score == max_score]

    if len(winners) == 1:
        # Clear winner
        mode = winners[0]
        confidence = min(0.6 + (max_score * 0.1), 0.95)
        return ContextSignal(
            mode=mode,
            confidence=confidence,
            reason=f"pattern_match_{mode.value}"
        )

    # Ambiguous - multiple modes tied
    # Priority: DEBUG > EXECUTE > EXPLAIN > DESIGN
    priority_order = [ContextMode.DEBUG, ContextMode.EXECUTE, ContextMode.EXPLAIN, ContextMode.DESIGN]
    for mode in priority_order:
        if mode in winners:
            return ContextSignal(
                mode=mode,
                confidence=0.6,
                reason=f"ambiguous_priority_{mode.value}"
            )

    # Fallback
    return ContextSignal(
        mode=ContextMode.EXECUTE,
        confidence=0.5,
        reason="fallback"
    )


def get_behavior_adjustments(context: ContextSignal) -> dict:
    """
    Get specific behavior adjustments based on context.

    Returns dict of adjustments that can be used in prompt or behavior tuning.
    """
    adjustments = {
        ContextMode.DESIGN: {
            "verbosity": "high",
            "structure": "organized",
            "examples": "architectural",
            "tone": "thoughtful",
            "include_tradeoffs": True,
            "ask_clarifying": True,
        },
        ContextMode.EXPLAIN: {
            "verbosity": "medium",
            "structure": "step_by_step",
            "examples": "simple",
            "tone": "educational",
            "include_tradeoffs": False,
            "ask_clarifying": False,
        },
        ContextMode.EXECUTE: {
            "verbosity": "low",
            "structure": "direct",
            "examples": "minimal",
            "tone": "efficient",
            "include_tradeoffs": False,
            "ask_clarifying": False,
        },
        ContextMode.DEBUG: {
            "verbosity": "medium",
            "structure": "diagnostic",
            "examples": "specific",
            "tone": "methodical",
            "include_tradeoffs": False,
            "ask_clarifying": True,
        },
    }

    return adjustments.get(context.mode, adjustments[ContextMode.EXECUTE])

"""
Memory V3 Week 2 - Meta-confidence
==================================
Teaches the AI when to ask instead of assume.

This module outputs confidence signals that help the AI know
when to ask clarifying questions vs proceed with assumptions.

Key principle: No storage, no profiling - behavior tuning only.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import re

# Feature flag
V3_META_ENABLED = os.getenv("V3_META_ENABLED", "true").lower() in ("true", "1", "yes")


class ConfidenceLevel(Enum):
    """How confident the AI should be in its interpretation."""
    HIGH = "high"      # Proceed without asking
    MEDIUM = "medium"  # Proceed but offer to clarify
    LOW = "low"        # Ask before assuming


class ClarifyingStyle(Enum):
    """How to ask clarifying questions."""
    NONE = "none"      # Don't ask
    SHORT = "short"    # One quick question
    NORMAL = "normal"  # Standard clarification


@dataclass
class MetaConfidence:
    """
    Meta-confidence signal for a user message.

    Tells the AI whether to ask or assume.
    """
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    should_ask_clarifying: bool = False
    clarifying_style: ClarifyingStyle = ClarifyingStyle.NONE
    max_clarifying_questions: int = 0
    restrict_to_info_only: bool = False  # Only ask "what" questions, not "why"
    reason: str = ""

    def to_prompt_block(self) -> str:
        """Generate prompt injection for meta-confidence."""
        if not V3_META_ENABLED:
            return ""

        if self.confidence_level == ConfidenceLevel.HIGH and not self.should_ask_clarifying:
            return ""  # No injection needed for high confidence

        lines = [
            f"interpretation_confidence: {self.confidence_level.value}",
        ]

        if self.should_ask_clarifying:
            lines.append(f"ask_before_assuming: true")
            lines.append(f"max_clarifying_questions: {self.max_clarifying_questions}")
            if self.clarifying_style != ClarifyingStyle.NONE:
                lines.append(f"clarifying_style: {self.clarifying_style.value}")
            if self.restrict_to_info_only:
                lines.append("question_type: info_only  # only ask what/which, not why")

        return "\n".join(lines)


# Patterns indicating underspecified requests
UNDERSPECIFIED_PATTERNS = [
    r"^(do|make|fix|create|build|add)\s+(it|this|that|something)(\s|$)",
    r"^(help|can you|could you)\s*$",
    r"^(it|this|that)\s+(doesn't|doesn't|isn't|is not)\s+work",
    r"(the thing|the stuff|the code|the file)\s*$",
    r"^(change|update|modify)\s+(it|this|that)(\s|$)",
]

# Patterns indicating clear/specific requests
SPECIFIC_PATTERNS = [
    r"(function|class|method|variable)\s+\w+",
    r"(file|path):\s*\S+",
    r"(line|lines?)\s+\d+",
    r"```[\s\S]+```",  # Code block included
    r"(error|exception):\s*\w+",
    r"(using|with)\s+(python|javascript|typescript|react|flask|django)",
]

# Patterns indicating ambiguous intent
AMBIGUOUS_PATTERNS = [
    r"(or|either|maybe|perhaps|possibly)\b",
    r"(best|better|right|correct)\s+(way|approach|method)",
    r"(should i|which one|what if)",
    r"(not sure|don't know|confused about)",
]

# Patterns indicating user wants no explanation (don't ask "why" questions)
NO_EXPLANATION_PATTERNS = [
    r"(no|skip|without)\s+(explanation|context|details|commentary)",
    r"just\s+(the\s+)?(code|fix|answer|solution)",
    r"code\s+only",
    r"(don't|do not)\s+explain",
    r"straight\s+to\s+(the\s+)?(point|code|fix)",
]


def _is_underspecified(message: str) -> bool:
    """Check if request lacks specificity."""
    msg_lower = message.lower().strip()

    for pattern in UNDERSPECIFIED_PATTERNS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return True

    # Very short messages are often underspecified
    if len(msg_lower.split()) <= 3 and not any(
        re.search(p, msg_lower, re.IGNORECASE) for p in SPECIFIC_PATTERNS
    ):
        return True

    return False


def _is_specific(message: str) -> bool:
    """Check if request is clearly specified."""
    msg_lower = message.lower()

    matches = sum(
        1 for p in SPECIFIC_PATTERNS
        if re.search(p, msg_lower, re.IGNORECASE)
    )

    return matches >= 2


def _is_ambiguous(message: str) -> bool:
    """Check if request has ambiguous intent."""
    msg_lower = message.lower()

    for pattern in AMBIGUOUS_PATTERNS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return True

    return False


def _wants_no_explanation(message: str) -> bool:
    """Check if user explicitly wants no explanation (don't ask "why" questions)."""
    msg_lower = message.lower()

    for pattern in NO_EXPLANATION_PATTERNS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return True

    return False


def infer_meta_confidence(
    context_mode: str,
    state_certainty: str,
    state_pace: str,
    user_message: str,
    previous_messages: List[str] = None
) -> MetaConfidence:
    """
    Infer meta-confidence from context, state, and message.

    Args:
        context_mode: The detected context (debug, design, execute, explain)
        state_certainty: User certainty (confident, uncertain, frustrated)
        state_pace: User pace (rushed, normal, deep)
        user_message: The current user message
        previous_messages: Recent message history

    Returns:
        MetaConfidence with ask/assume guidance
    """
    if not V3_META_ENABLED:
        return MetaConfidence(
            confidence_level=ConfidenceLevel.HIGH,
            should_ask_clarifying=False,
            reason="meta-confidence disabled"
        )

    # Start with medium confidence
    confidence = ConfidenceLevel.MEDIUM
    should_ask = False
    style = ClarifyingStyle.NONE
    max_questions = 0
    restrict_info_only = False
    reason = ""

    # Check message specificity
    is_underspec = _is_underspecified(user_message)
    is_specific = _is_specific(user_message)
    is_ambiguous = _is_ambiguous(user_message)
    no_explanation = _wants_no_explanation(user_message)

    # Rule 1: Underspecified + EXECUTE/DEBUG → LOW, ask
    if is_underspec and context_mode in ("execute", "debug"):
        confidence = ConfidenceLevel.LOW
        should_ask = True
        reason = "underspecified request in action context"

    # Rule 2: Specific request → HIGH
    elif is_specific and not is_ambiguous:
        confidence = ConfidenceLevel.HIGH
        should_ask = False
        reason = "specific request detected"

    # Rule 3: User is uncertain → ask to confirm
    elif state_certainty == "uncertain":
        confidence = ConfidenceLevel.LOW
        should_ask = True
        reason = "user expressed uncertainty"

    # Rule 4: Ambiguous intent → MEDIUM, maybe ask
    elif is_ambiguous:
        confidence = ConfidenceLevel.MEDIUM
        should_ask = True
        reason = "ambiguous intent detected"

    # Rule 5: DESIGN context often benefits from clarification
    elif context_mode == "design" and not is_specific:
        confidence = ConfidenceLevel.MEDIUM
        should_ask = True
        reason = "design context benefits from requirements clarity"

    # Determine clarifying style based on pace
    if should_ask:
        if state_pace == "rushed":
            style = ClarifyingStyle.SHORT
            max_questions = 1
        elif state_pace == "deep":
            style = ClarifyingStyle.NORMAL
            max_questions = 3
        else:
            style = ClarifyingStyle.SHORT
            max_questions = 2

    # Guardrail: "no explanation" mode - only ask info-gathering questions
    # Ask "What file?" or "Which error?" but NOT "Why do you need this?"
    if no_explanation and should_ask:
        restrict_info_only = True
        style = ClarifyingStyle.SHORT  # Force short style
        max_questions = 1  # Only ask what's necessary to proceed

    # Override: frustrated user → don't ask more questions
    if state_certainty == "frustrated":
        should_ask = False
        style = ClarifyingStyle.NONE
        max_questions = 0
        restrict_info_only = False
        reason = "user is frustrated, avoid more questions"

    return MetaConfidence(
        confidence_level=confidence,
        should_ask_clarifying=should_ask,
        clarifying_style=style,
        max_clarifying_questions=max_questions,
        restrict_to_info_only=restrict_info_only,
        reason=reason
    )

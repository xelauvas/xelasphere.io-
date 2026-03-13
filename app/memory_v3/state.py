"""
Memory V3 - State Inference
===========================
Short-lived situational awareness.

NOT psychological profiling.
NOT emotional data storage.
Just: how should I behave right now?

CONTRACT:
- TTL is mandatory (default 5 minutes)
- Never written to DB
- Never shown as "about the user"
- Reset on strong signals
- Never carried across long gaps
"""

import re
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Literal
from enum import Enum

logger = logging.getLogger(__name__)

# TTL defaults
DEFAULT_STATE_TTL_SECONDS = 300  # 5 minutes
MIN_STATE_TTL_SECONDS = 60      # 1 minute
MAX_STATE_TTL_SECONDS = 900     # 15 minutes


class UserMode(Enum):
    """What the user is doing."""
    EXPLORING = "exploring"     # trying things, open-ended
    DECIDING = "deciding"       # weighing options
    EXECUTING = "executing"     # getting things done
    STUCK = "stuck"            # frustrated, blocked


class UserPace(Enum):
    """How fast the user wants to move."""
    RUSHED = "rushed"           # quick answers needed
    NORMAL = "normal"           # standard pace
    DEEP = "deep"              # wants thoroughness


class UserCertainty(Enum):
    """How confident the user seems."""
    CONFIDENT = "confident"     # knows what they want
    UNCERTAIN = "uncertain"     # not sure, exploring
    FRUSTRATED = "frustrated"   # things aren't working


@dataclass
class UserState:
    """
    Ephemeral user state - situational awareness.

    This is NOT profiling. This is reading the room.
    """
    mode: UserMode = UserMode.EXPLORING
    pace: UserPace = UserPace.NORMAL
    certainty: UserCertainty = UserCertainty.CONFIDENT
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(seconds=DEFAULT_STATE_TTL_SECONDS))

    def is_expired(self) -> bool:
        """Check if state has expired."""
        return datetime.utcnow() > self.expires_at

    def remaining_ttl_seconds(self) -> int:
        """Get remaining TTL in seconds."""
        remaining = (self.expires_at - datetime.utcnow()).total_seconds()
        return max(0, int(remaining))

    def to_prompt_hint(self) -> str:
        """Generate behavior hint for prompt injection."""
        hints = []

        # Mode hints
        mode_hints = {
            UserMode.EXPLORING: "User is exploring options. Be open to different approaches.",
            UserMode.DECIDING: "User is making a decision. Help compare options clearly.",
            UserMode.EXECUTING: "User is executing. Be direct, minimize explanation.",
            UserMode.STUCK: "User seems stuck. Be patient, offer alternative approaches.",
        }
        hints.append(mode_hints.get(self.mode, ""))

        # Pace hints
        pace_hints = {
            UserPace.RUSHED: "User wants quick answers. Be concise.",
            UserPace.NORMAL: "",
            UserPace.DEEP: "User wants depth. Be thorough with explanations.",
        }
        if pace_hints.get(self.pace):
            hints.append(pace_hints[self.pace])

        # Certainty hints
        certainty_hints = {
            UserCertainty.CONFIDENT: "",
            UserCertainty.UNCERTAIN: "User seems uncertain. Ask clarifying questions if needed.",
            UserCertainty.FRUSTRATED: "User may be frustrated. Be reassuring and clear.",
        }
        if certainty_hints.get(self.certainty):
            hints.append(certainty_hints[self.certainty])

        return " ".join(filter(None, hints))


# Detection patterns
RUSHED_PATTERNS = [
    r'^(quick|fast|asap|urgent|hurry)\b',
    r'\b(quickly|briefly|short|tldr|tl;dr)\b',
    r'^(just)\s+\w+',  # "just do X"
    r'^\w+\??$',  # Single word messages
]

DEEP_PATTERNS = [
    r'\b(detail|detailed|thorough|comprehensive|in-depth|deep dive)\b',
    r'\b(in detail|with detail)\b',
    r'\b(explain.*fully|walk me through|step by step)\b',
    r'\b(why does|how does.*work|understand deeply)\b',
    r'\b(all the|every|everything about)\b',
]

FRUSTRATED_PATTERNS = [
    r'\b(still|again|another|keeps?|won\'t|doesn\'t|isn\'t)\b.*\b(error|fail|wrong|broken|work)\b',
    r'\b(frustrated|annoying|annoyed|ugh|argh)\b',
    r'(!{2,}|\?{2,})',  # Multiple punctuation
    r'\b(tried everything|nothing works|give up)\b',
    r'\b(what the|why won\'t|why doesn\'t|why isn\'t)\b',
]

UNCERTAIN_PATTERNS = [
    r'\b(maybe|perhaps|not sure|don\'t know|unsure)\b',
    r'\b(should i|could i|would it|is it better)\b',
    r'\b(confused|confusing|unclear)\b',
    r'\?.*\?',  # Multiple questions
]

EXPLORING_PATTERNS = [
    r'\b(what if|how about|alternatively|another way)\b',
    r'\b(options|possibilities|approaches|ideas)\b',
    r'\b(try|experiment|test|see if)\b',
    r'\b(curious|wondering|interested in)\b',
]

DECIDING_PATTERNS = [
    r'\b(which|choose|pick|select|decide|decision)\b',
    r'\b(better|best|prefer|versus|vs|or)\b',
    r'\b(compare|comparison|trade-?off|pros|cons)\b',
    r'\b(recommend|suggestion|advice)\b',
]

EXECUTING_PATTERNS = [
    r'^(do|run|execute|make|create|build|deploy)\b',
    r'\b(now|go ahead|proceed|start|begin)\b',
    r'\b(implement|apply|use|ship)\b',
]

STUCK_PATTERNS = [
    r'\b(stuck|blocked|can\'t figure|no idea)\b',
    r'\b(help|lost|confused)\b',
    r'\b(doesn\'t make sense|don\'t understand)\b',
    r'\b(tried|attempted).*\b(but|still|doesn\'t)\b',
]


def _count_matches(text: str, patterns: List[str]) -> int:
    """Count pattern matches in text."""
    text_lower = text.lower()
    count = 0
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            count += 1
    return count


def infer_user_state(
    user_message: str,
    conversation_history: Optional[List[dict]] = None,
    previous_state: Optional[UserState] = None,
    ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS
) -> UserState:
    """
    Infer current user state from message and context.

    Rules first - fast and reliable.
    Only considers current message and recent history.

    Args:
        user_message: Current user message
        conversation_history: Recent messages for context
        previous_state: Previous state (if not expired, influences new state)
        ttl_seconds: How long this state should live

    Returns:
        UserState with mode, pace, certainty, and expiration
    """
    if not user_message:
        return UserState(ttl_seconds=ttl_seconds)

    # Clamp TTL
    ttl_seconds = max(MIN_STATE_TTL_SECONDS, min(ttl_seconds, MAX_STATE_TTL_SECONDS))

    # Detect pace
    pace = _detect_pace(user_message)

    # Detect certainty
    certainty = _detect_certainty(user_message)

    # Detect mode
    mode = _detect_mode(user_message, certainty)

    # Consider previous state if not expired
    if previous_state and not previous_state.is_expired():
        # Strong signals override, weak signals blend
        if certainty == UserCertainty.CONFIDENT and previous_state.certainty == UserCertainty.FRUSTRATED:
            # User recovered - keep confident
            pass
        elif certainty == UserCertainty.CONFIDENT:
            # No strong signal - slight influence from previous
            pass

    expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)

    state = UserState(
        mode=mode,
        pace=pace,
        certainty=certainty,
        created_at=datetime.utcnow(),
        expires_at=expires_at
    )

    logger.debug(f"Inferred state: mode={mode.value}, pace={pace.value}, certainty={certainty.value}, ttl={ttl_seconds}s")

    return state


def _detect_pace(text: str) -> UserPace:
    """Detect user's desired pace."""
    rushed_score = _count_matches(text, RUSHED_PATTERNS)
    deep_score = _count_matches(text, DEEP_PATTERNS)

    # If explicit deep signal, don't apply short message bonus
    if deep_score == 0:
        # Short messages suggest rushed (only if no deep signal)
        if len(text.split()) <= 3:
            rushed_score += 1

    # Long messages suggest deep
    if len(text.split()) > 30:
        deep_score += 1

    if rushed_score > deep_score and rushed_score > 0:
        return UserPace.RUSHED
    elif deep_score > 0:  # Deep wins ties if there's an explicit signal
        return UserPace.DEEP
    return UserPace.NORMAL


def _detect_certainty(text: str) -> UserCertainty:
    """Detect user's certainty level."""
    frustrated_score = _count_matches(text, FRUSTRATED_PATTERNS)
    uncertain_score = _count_matches(text, UNCERTAIN_PATTERNS)

    # Frustrated takes priority (stronger signal)
    if frustrated_score >= 2:
        return UserCertainty.FRUSTRATED
    if frustrated_score > uncertain_score and frustrated_score > 0:
        return UserCertainty.FRUSTRATED
    if uncertain_score > 0:
        return UserCertainty.UNCERTAIN
    return UserCertainty.CONFIDENT


def _detect_mode(text: str, certainty: UserCertainty) -> UserMode:
    """Detect user's current mode."""
    scores = {
        UserMode.EXPLORING: _count_matches(text, EXPLORING_PATTERNS),
        UserMode.DECIDING: _count_matches(text, DECIDING_PATTERNS),
        UserMode.EXECUTING: _count_matches(text, EXECUTING_PATTERNS),
        UserMode.STUCK: _count_matches(text, STUCK_PATTERNS),
    }

    # Frustrated certainty boosts stuck score
    if certainty == UserCertainty.FRUSTRATED:
        scores[UserMode.STUCK] += 1

    max_score = max(scores.values())

    if max_score == 0:
        # No clear signal - default based on certainty
        if certainty == UserCertainty.UNCERTAIN:
            return UserMode.EXPLORING
        return UserMode.EXECUTING

    # Get winner
    for mode, score in scores.items():
        if score == max_score:
            return mode

    return UserMode.EXECUTING


def get_behavior_adjustments(state: UserState) -> dict:
    """
    Get specific behavior adjustments based on state.

    Returns dict that can be used in prompt or behavior tuning.
    """
    adjustments = {
        "verbosity": "medium",
        "tone": "neutral",
        "ask_clarifying": False,
        "offer_alternatives": False,
        "be_reassuring": False,
    }

    # Pace adjustments
    if state.pace == UserPace.RUSHED:
        adjustments["verbosity"] = "low"
        adjustments["tone"] = "efficient"
    elif state.pace == UserPace.DEEP:
        adjustments["verbosity"] = "high"
        adjustments["tone"] = "thorough"

    # Certainty adjustments
    if state.certainty == UserCertainty.UNCERTAIN:
        adjustments["ask_clarifying"] = True
        adjustments["offer_alternatives"] = True
    elif state.certainty == UserCertainty.FRUSTRATED:
        adjustments["be_reassuring"] = True
        adjustments["tone"] = "patient"
        adjustments["offer_alternatives"] = True

    # Mode adjustments
    if state.mode == UserMode.EXPLORING:
        adjustments["offer_alternatives"] = True
    elif state.mode == UserMode.STUCK:
        adjustments["be_reassuring"] = True
        adjustments["offer_alternatives"] = True

    return adjustments

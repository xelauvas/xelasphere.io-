"""
Conversation Context Tracking
=============================
Tracks action types and referents to enable natural continuations.

Architecture:
    1. Action tagging with confidence - tracks what AI did (joke, list, code, etc.)
    2. Typed referent stack - artifacts indexed by type with stable list indices
    3. Continuation detection - uses novelty check (not just word count)
    4. Referent scoring - disambiguates "it/that/this" via scoring
    5. Missing slot detection - deterministic table of action→required_object

Usage:
    from app.conversation_context import ConversationContext

    ctx = ConversationContext(session_id)
    ctx.tag_response(ai_response, user_prompt)  # After AI responds

    # Before next AI call
    if ctx.is_continuation(user_input):
        pass  # Let it flow to main LLM

    # Check for missing slots
    missing = ctx.get_missing_slot(user_input)
    if missing:
        return f"What {missing} would you like me to work with?"

    # Resolve "it/that/this"
    referent = ctx.resolve_pronoun(user_input)
"""

import re
import random
import hashlib
import logging
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple, Callable
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# =============================================================================
# Instrumentation & Metrics Configuration
# =============================================================================

# Sampling rate for trace logging (0.0-1.0)
# Set to 1.0 for full logging, 0.1 for 10% sampling
TRACE_SAMPLE_RATE = 0.1

# Enable/disable detailed trace logging
TRACE_ENABLED = True

# Shadow metrics collection
SHADOW_METRICS_ENABLED = True


# =============================================================================
# Instrumentation - Trace Logging with Sampling
# =============================================================================

@dataclass
class TraceEvent:
    """Single trace event for instrumentation."""
    timestamp: str
    session_id: str
    event_type: str  # continuation_check, slot_check, pronoun_resolution, action_tag
    user_input: str = ""

    # Continuation check fields
    is_continuation: Optional[bool] = None
    novelty_score: Optional[float] = None
    novelty_method: str = ""  # "token" or "embedding"

    # Action tag fields
    action_type: str = ""
    action_confidence: float = 0.0

    # Pronoun resolution fields
    referent_selected: str = ""
    referent_score: float = 0.0
    score_gap: float = 0.0  # Gap between top 2 candidates

    # Slot check fields
    slot_triggered: str = ""
    slot_missing: bool = False

    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != ""}


class TraceLogger:
    """Sampled trace logger for instrumentation."""

    def __init__(self, sample_rate: float = TRACE_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._buffer: deque = deque(maxlen=200)  # Keep last 200 events

    def should_log(self) -> bool:
        """Determine if this event should be logged based on sampling."""
        return TRACE_ENABLED and random.random() < self.sample_rate

    def log_continuation_check(
        self,
        session_id: str,
        user_input: str,
        is_continuation: bool,
        novelty_score: float,
        novelty_method: str = "token"
    ):
        if not self.should_log():
            return

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            session_id=session_id,
            event_type="continuation_check",
            user_input=user_input[:50],
            is_continuation=is_continuation,
            novelty_score=novelty_score,
            novelty_method=novelty_method
        )
        self._buffer.append(event)
        logger.debug(f"TRACE continuation: {event.to_dict()}")

    def log_action_tag(
        self,
        session_id: str,
        action_type: str,
        confidence: float
    ):
        if not self.should_log():
            return

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            session_id=session_id,
            event_type="action_tag",
            action_type=action_type,
            action_confidence=confidence
        )
        self._buffer.append(event)
        logger.debug(f"TRACE action_tag: {event.to_dict()}")

    def log_pronoun_resolution(
        self,
        session_id: str,
        user_input: str,
        referent_type: str,
        score: float,
        score_gap: float
    ):
        if not self.should_log():
            return

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            session_id=session_id,
            event_type="pronoun_resolution",
            user_input=user_input[:50],
            referent_selected=referent_type,
            referent_score=score,
            score_gap=score_gap
        )
        self._buffer.append(event)
        logger.debug(f"TRACE pronoun: {event.to_dict()}")

    def log_slot_check(
        self,
        session_id: str,
        user_input: str,
        slot_triggered: str,
        slot_missing: bool
    ):
        if not self.should_log():
            return

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            session_id=session_id,
            event_type="slot_check",
            user_input=user_input[:50],
            slot_triggered=slot_triggered,
            slot_missing=slot_missing
        )
        self._buffer.append(event)
        logger.debug(f"TRACE slot: {event.to_dict()}")

    def get_recent_events(self, limit: int = 100) -> List[Dict]:
        """Get recent trace events for debugging."""
        return [e.to_dict() for e in list(self._buffer)[-limit:]]


# Global trace logger instance
_trace_logger = TraceLogger()


# =============================================================================
# Shadow-Mode Metrics
# =============================================================================

@dataclass
class InterceptMetrics:
    """Tracks interception effectiveness."""
    total_checks: int = 0
    total_intercepts: int = 0

    # Rephrase tracking (user immediately sends another message)
    rephrases_after_intercept: int = 0

    # False intercept signals
    false_intercept_signals: int = 0  # "no, I meant...", "that's not what I asked"

    # By action type
    intercepts_by_type: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Continuation detection stats
    continuation_true: int = 0
    continuation_false: int = 0
    avg_novelty_score: float = 0.0
    _novelty_sum: float = 0.0
    _novelty_count: int = 0

    def record_check(self, intercepted: bool, action_type: str = ""):
        self.total_checks += 1
        if intercepted:
            self.total_intercepts += 1
            if action_type:
                self.intercepts_by_type[action_type] += 1

    def record_continuation(self, is_continuation: bool, novelty_score: float):
        if is_continuation:
            self.continuation_true += 1
        else:
            self.continuation_false += 1
        self._novelty_sum += novelty_score
        self._novelty_count += 1
        self.avg_novelty_score = self._novelty_sum / self._novelty_count

    def record_rephrase(self):
        """Call when user rephrases after intercept."""
        self.rephrases_after_intercept += 1

    def record_false_intercept(self):
        """Call when user indicates intercept was wrong."""
        self.false_intercept_signals += 1

    @property
    def intercept_rate(self) -> float:
        return self.total_intercepts / max(1, self.total_checks)

    @property
    def false_intercept_rate(self) -> float:
        return self.false_intercept_signals / max(1, self.total_intercepts)

    def to_dict(self) -> Dict:
        return {
            "total_checks": self.total_checks,
            "total_intercepts": self.total_intercepts,
            "intercept_rate": round(self.intercept_rate, 4),
            "rephrases_after_intercept": self.rephrases_after_intercept,
            "false_intercept_signals": self.false_intercept_signals,
            "false_intercept_rate": round(self.false_intercept_rate, 4),
            "intercepts_by_type": dict(self.intercepts_by_type),
            "continuation_true": self.continuation_true,
            "continuation_false": self.continuation_false,
            "avg_novelty_score": round(self.avg_novelty_score, 4),
        }


# Patterns that suggest user is correcting a false intercept
FALSE_INTERCEPT_PATTERNS = [
    r"\bno[,.]?\s*i\s*meant\b",
    r"\bthat'?s?\s*not\s*what\s*i\b",
    r"\bi\s*said\b",
    r"\bi\s*was\s*(?:asking|talking)\s*about\b",
    r"\bnot\s*what\s*i\s*(?:asked|wanted)\b",
    r"\byou\s*misunderstood\b",
    r"\bwrong\b.*\bi\s*(?:meant|wanted)\b",
]


def is_false_intercept_signal(user_input: str) -> bool:
    """Check if user input suggests previous intercept was wrong."""
    input_lower = user_input.lower()
    return any(re.search(p, input_lower) for p in FALSE_INTERCEPT_PATTERNS)


def is_likely_rephrase(
    current_input: str,
    previous_input: str,
    time_gap_seconds: float
) -> bool:
    """Check if current input is likely a rephrase of previous."""
    # Quick rephrase: same length-ish, within 30 seconds
    if time_gap_seconds > 30:
        return False

    # Similar length (within 50%)
    len_ratio = len(current_input) / max(1, len(previous_input))
    if not (0.5 <= len_ratio <= 2.0):
        return False

    # Some token overlap
    curr_tokens = set(current_input.lower().split())
    prev_tokens = set(previous_input.lower().split())
    overlap = len(curr_tokens & prev_tokens) / max(1, len(curr_tokens | prev_tokens))

    return overlap > 0.3


# Global metrics instance (per-process)
_shadow_metrics = InterceptMetrics()


def get_shadow_metrics() -> InterceptMetrics:
    """Get the global shadow metrics instance."""
    return _shadow_metrics


def reset_shadow_metrics():
    """Reset shadow metrics (e.g., for testing)."""
    global _shadow_metrics
    _shadow_metrics = InterceptMetrics()


# =============================================================================
# Multilingual Support - Embedding-based Novelty
# =============================================================================

# Optional embedding function (set externally if available)
_embedding_fn: Optional[Callable[[str], List[float]]] = None
_cosine_sim_fn: Optional[Callable[[List[float], List[float]], float]] = None


def set_embedding_functions(
    embed_fn: Callable[[str], List[float]],
    cosine_fn: Callable[[List[float], List[float]], float]
):
    """
    Set embedding functions for multilingual novelty detection.

    Args:
        embed_fn: Function that takes text and returns embedding vector
        cosine_fn: Function that computes cosine similarity between two vectors

    Example:
        from app.hybrid_retrieval import get_embedding, cosine_similarity
        set_embedding_functions(get_embedding, cosine_similarity)
    """
    global _embedding_fn, _cosine_sim_fn
    _embedding_fn = embed_fn
    _cosine_sim_fn = cosine_fn
    logger.info("Embedding functions set for multilingual novelty detection")


def has_embedding_support() -> bool:
    """Check if embedding-based novelty is available."""
    return _embedding_fn is not None and _cosine_sim_fn is not None


def calculate_novelty_embedding(
    user_input: str,
    last_user_prompt: str = "",
    last_ai_response: str = ""
) -> Tuple[float, str]:
    """
    Calculate novelty using embeddings (multilingual, typo-tolerant).

    Returns:
        (novelty_score 0-1, method_used)
    """
    if not has_embedding_support():
        return 0.5, "fallback"

    try:
        input_emb = _embedding_fn(user_input)

        similarities = []

        if last_user_prompt:
            prompt_emb = _embedding_fn(last_user_prompt)
            similarities.append(_cosine_sim_fn(input_emb, prompt_emb))

        if last_ai_response:
            # Only embed first 500 chars of response
            response_emb = _embedding_fn(last_ai_response[:500])
            similarities.append(_cosine_sim_fn(input_emb, response_emb))

        if not similarities:
            return 0.5, "embedding"

        # Max similarity to context = how related the input is
        max_similarity = max(similarities)

        # Novelty = 1 - similarity (high similarity = low novelty)
        novelty = 1.0 - max_similarity

        return novelty, "embedding"

    except Exception as e:
        logger.debug(f"Embedding novelty failed: {e}")
        return 0.5, "fallback"


# =============================================================================
# Referent Stack Hygiene
# =============================================================================

# Maximum content size before summarization (chars)
MAX_REFERENT_CONTENT_SIZE = 500

# Maximum items to store in a list referent
MAX_REFERENT_ITEMS = 10

# Turn threshold for staleness (referents older than this are considered stale)
STALE_TURN_THRESHOLD = 5


def content_hash(content: str) -> str:
    """Generate a short hash for content identification."""
    return hashlib.md5(content.encode()).hexdigest()[:8]


def summarize_content(content: str, max_length: int = 100) -> str:
    """Create a summary/preview of long content."""
    if len(content) <= max_length:
        return content

    # Try to break at sentence boundary
    truncated = content[:max_length]
    last_period = truncated.rfind('.')
    last_newline = truncated.rfind('\n')

    break_point = max(last_period, last_newline)
    if break_point > max_length // 2:
        return content[:break_point + 1] + "..."

    return truncated + "..."


def is_topic_shift(
    current_novelty: float,
    current_input: str,
    threshold: float = 0.7
) -> bool:
    """
    Detect if there's a sharp topic shift that should evict stale referents.

    A topic shift is indicated by:
    - High novelty score (> threshold)
    - New intent markers in input
    """
    if current_novelty < threshold:
        return False

    # Check for new intent markers
    input_tokens = set(current_input.lower().split())
    has_new_intent = bool(input_tokens & NEW_INTENT_MARKERS)

    return has_new_intent


# =============================================================================
# Constants
# =============================================================================

# Stopwords for novelty detection (common words that don't indicate new topics)
STOPWORDS = {
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'between', 'under', 'again', 'further', 'then', 'once', 'here',
    'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few',
    'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
    'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
    'and', 'but', 'if', 'or', 'because', 'as', 'until', 'while',
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves',
    'you', 'your', 'yours', 'yourself', 'yourselves', 'he', 'him',
    'his', 'himself', 'she', 'her', 'hers', 'herself', 'it', 'its',
    'itself', 'they', 'them', 'their', 'theirs', 'themselves',
    'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those',
    'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'please', 'thanks', 'thank', 'okay', 'ok', 'yes', 'no', 'yeah',
    'another', 'one', 'next', 'last', 'first', 'second', 'third',
    # List/selection references
    'number', 'option', 'choice', 'item', 'pick',
    # Modification words (not new topics, just modifiers)
    'make', 'faster', 'slower', 'bigger', 'smaller', 'better', 'worse',
    'simpler', 'shorter', 'longer', 'cleaner', 'clearer',
}

# Intent markers that suggest a NEW request (not continuation)
# Note: 'make' is NOT here because "make it faster" is a continuation/modification
NEW_INTENT_MARKERS = {
    'help', 'explain', 'summarize', 'translate', 'write', 'create',
    'generate', 'build', 'show', 'tell', 'give', 'find',
    'search', 'look', 'check', 'analyze', 'compare', 'convert',
    'calculate', 'solve', 'recommend', 'suggest', 'list', 'describe', 'define',
}

# Continuation markers (words that suggest continuing previous action)
CONTINUATION_MARKERS = {
    'another', 'other', 'more', 'again', 'next', 'different',
    'better', 'worse', 'shorter', 'longer', 'simpler', 'funnier',
    'continue', 'go', 'keep', 'repeat',
}


# =============================================================================
# Action Types - What the AI just did
# =============================================================================

@dataclass
class ActionTag:
    """Lightweight tag describing what the AI just did."""
    action_type: str  # joke, list, explanation, code, recipe, quote, etc.
    confidence: float = 1.0  # 0.0-1.0 confidence in the detection
    subject: str = ""  # Optional: the topic (e.g., "python", "chicken")
    has_items: bool = False  # True if response contained numbered/bulleted items
    item_count: int = 0
    turn_number: int = 0  # Which turn this was created
    timestamp: str = ""

    def to_dict(self) -> Dict:
        return {
            "action_type": self.action_type,
            "confidence": self.confidence,
            "subject": self.subject,
            "has_items": self.has_items,
            "item_count": self.item_count,
            "turn_number": self.turn_number,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ActionTag":
        return cls(
            action_type=data.get("action_type", "unknown"),
            confidence=data.get("confidence", 1.0),
            subject=data.get("subject", ""),
            has_items=data.get("has_items", False),
            item_count=data.get("item_count", 0),
            turn_number=data.get("turn_number", 0),
            timestamp=data.get("timestamp", "")
        )


@dataclass
class Referent:
    """An artifact/object that can be referenced in follow-up messages."""
    ref_type: str  # joke, code, list, document, image, explanation, etc.
    ref_id: str = ""  # Stable identifier
    turn_number: int = 0  # Which turn this was created
    content_preview: str = ""  # First ~100 chars for context
    content_hash: str = ""  # Hash of full content for identification
    content_size: int = 0  # Original content size (before summarization)
    items: List[str] = field(default_factory=list)  # For lists (stable indices)
    item_indices: Dict[int, str] = field(default_factory=dict)  # index -> item mapping
    language: str = ""  # For code: python, javascript, etc.
    timestamp: str = ""

    def is_stale(self, current_turn: int) -> bool:
        """Check if this referent is stale based on turn age."""
        return (current_turn - self.turn_number) > STALE_TURN_THRESHOLD

    def is_heavy(self) -> bool:
        """Check if this referent is heavy (large content)."""
        return self.content_size > MAX_REFERENT_CONTENT_SIZE

    def to_dict(self) -> Dict:
        return {
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
            "turn_number": self.turn_number,
            "content_preview": self.content_preview,
            "content_hash": self.content_hash,
            "content_size": self.content_size,
            "items": self.items[:MAX_REFERENT_ITEMS],
            "item_indices": dict(list(self.item_indices.items())[:MAX_REFERENT_ITEMS]),
            "language": self.language,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Referent":
        return cls(
            ref_type=data.get("ref_type", "unknown"),
            ref_id=data.get("ref_id", ""),
            turn_number=data.get("turn_number", 0),
            content_preview=data.get("content_preview", ""),
            content_hash=data.get("content_hash", ""),
            content_size=data.get("content_size", 0),
            items=data.get("items", []),
            item_indices={int(k): v for k, v in data.get("item_indices", {}).items()},
            language=data.get("language", ""),
            timestamp=data.get("timestamp", "")
        )


# =============================================================================
# Action Type Detection with Confidence
# =============================================================================

# Patterns to detect what type of content the AI produced
# Each pattern has a weight for confidence scoring
ACTION_PATTERNS = {
    "joke": [
        (r"(?:here'?s?|tell you|got) (?:a |one |another )?joke", 0.9),
        (r"why did (?:the |a )", 0.8),
        (r"knock[- ]?knock", 0.9),
        (r"what do you (?:call|get)", 0.7),
        (r"a man walks into", 0.8),
        (r"(?:ha ?){2,}|😂|🤣|😄", 0.5),
    ],
    "recipe": [
        (r"(?:here'?s?|is) (?:a |the )?recipe", 0.95),
        (r"ingredients?:", 0.9),
        (r"instructions?:", 0.8),
        (r"(?:prep|cook(?:ing)?|bake) time", 0.85),
        (r"\d+\s*(?:cups?|tbsp|tsp|oz|grams?|ml)", 0.6),
    ],
    "code": [
        (r"```(?:python|javascript|java|cpp|c\+\+|rust|go|typescript|html|css|sql|bash|sh)", 0.95),
        (r"def \w+\s*\(", 0.85),
        (r"function \w+\s*\(", 0.85),
        (r"class \w+\s*[:{]", 0.8),
        (r"import \w+|from \w+ import", 0.7),
    ],
    "explanation": [
        (r"(?:let me |i'?ll? )explain", 0.9),
        (r"(?:this|that|it) (?:means|refers to|is when)", 0.8),
        (r"in (?:simple|other) (?:terms|words)", 0.85),
        (r"(?:basically|essentially|fundamentally)", 0.6),
    ],
    "list": [
        # More specific list patterns - require actual list context
        (r"(?:here (?:are|is) (?:some|the|your|my|a few|several))", 0.8),
        (r"(?:top \d+|list of|\d+ (?:best|top|ways|tips|things|options|choices))", 0.9),
        (r"(?:options|choices|alternatives):", 0.85),
        (r"^\s*\d+[.)]\s+.+$", 0.7),  # Numbered items (lower confidence alone)
    ],
    "quote": [
        (r'"[^"]{20,}"', 0.7),
        (r"(?:famous|inspirational|motivational) quote", 0.9),
        (r"— ?\w+|– ?\w+", 0.6),  # Attribution dash
    ],
    "recommendation": [
        (r"(?:i |would |i'd )?(?:recommend|suggest)", 0.85),
        (r"you (?:should|could|might)(?: want to)?", 0.7),
        (r"(?:best|top|great) (?:choice|option|pick)", 0.75),
    ],
    "story": [
        (r"once upon a time", 0.95),
        (r"(?:let me tell|here'?s?) (?:you )?(?:a |the )?story", 0.9),
        (r"(?:the |a )(?:tale|story) (?:of|about)", 0.85),
    ],
    "fact": [
        (r"(?:fun|interesting|did you know|here'?s? a) fact", 0.9),
        (r"(?:actually|interestingly|surprisingly),? ", 0.5),
    ],
}

# Prompt hints with confidence weights
PROMPT_HINTS = {
    "joke": [("joke", 0.95), ("funny", 0.8), ("make me laugh", 0.9), ("humor", 0.85)],
    "recipe": [("recipe", 0.95), ("how to cook", 0.9), ("how to make", 0.7), ("ingredients", 0.85)],
    "code": [("code", 0.9), ("function", 0.8), ("script", 0.85), ("program", 0.8), ("implement", 0.85)],
    "explanation": [("explain", 0.9), ("what is", 0.85), ("what does", 0.85), ("how does", 0.85), ("why does", 0.85)],
    "list": [("list", 0.9), ("top", 0.7), ("best", 0.6), ("ways to", 0.8), ("tips", 0.75)],
    "quote": [("quote", 0.95), ("saying", 0.8), ("words of wisdom", 0.9)],
    "story": [("story", 0.95), ("tale", 0.9), ("once upon", 0.95)],
    "fact": [("fact", 0.9), ("did you know", 0.85), ("tell me about", 0.6)],
    "recommendation": [("recommend", 0.9), ("suggest", 0.85), ("should i", 0.8), ("what should", 0.8)],
}


def detect_action_type(response: str, user_prompt: str = "", turn_number: int = 0) -> ActionTag:
    """
    Detect what type of action/content the AI just produced with confidence scoring.

    Args:
        response: The AI's response text
        user_prompt: The user's original prompt (helps disambiguation)
        turn_number: Current conversation turn

    Returns:
        ActionTag with action_type and confidence
    """
    response_lower = response.lower()
    prompt_lower = user_prompt.lower() if user_prompt else ""

    detected_type = "general"
    max_confidence = 0.3  # Default confidence for "general"
    detected_subject = ""

    # Check user prompt first for explicit requests
    for action, hints in PROMPT_HINTS.items():
        for hint, weight in hints:
            if hint in prompt_lower:
                if weight > max_confidence:
                    detected_type = action
                    max_confidence = weight
                break

    # Check response patterns (may override or boost confidence)
    for action, patterns in ACTION_PATTERNS.items():
        for pattern, weight in patterns:
            if re.search(pattern, response_lower, re.MULTILINE | re.IGNORECASE):
                # Combine with existing confidence if same type
                if action == detected_type:
                    max_confidence = min(1.0, max_confidence + weight * 0.2)
                elif weight > max_confidence:
                    detected_type = action
                    max_confidence = weight
                break

    # Detect if response has list items
    has_items = False
    item_count = 0

    numbered = re.findall(r'^\s*\d+[.)]\s+.+$', response, re.MULTILINE)
    if numbered:
        has_items = True
        item_count = len(numbered)

    if not has_items:
        bullets = re.findall(r'^\s*[-•*]\s+.+$', response, re.MULTILINE)
        if bullets:
            has_items = True
            item_count = len(bullets)

    # If detected as "list" but has no actual items, demote to "general"
    # This prevents "Here is some info..." from being tagged as a list
    if detected_type == "list" and not has_items:
        detected_type = "general"
        max_confidence = 0.3

    # Extract subject from prompt
    subject_match = re.search(r'(?:about|on|for|regarding)\s+(\w+(?:\s+\w+)?)', prompt_lower)
    if subject_match:
        detected_subject = subject_match.group(1)

    return ActionTag(
        action_type=detected_type,
        confidence=max_confidence,
        subject=detected_subject,
        has_items=has_items,
        item_count=item_count,
        turn_number=turn_number,
        timestamp=datetime.utcnow().isoformat()
    )


def extract_referents(response: str, action_tag: ActionTag, turn_number: int = 0) -> List[Referent]:
    """
    Extract referenceable artifacts from the response with stable indices.
    Applies content hygiene: summarizes large content, stores hash for identification.
    """
    referents = []
    timestamp = datetime.utcnow().isoformat()
    ref_counter = 0

    # Main response referent with hygiene
    content_size = len(response)
    main_ref = Referent(
        ref_type=action_tag.action_type,
        ref_id=f"ref_{turn_number}_{ref_counter}",
        turn_number=turn_number,
        content_preview=summarize_content(response, MAX_REFERENT_CONTENT_SIZE // 5),
        content_hash=content_hash(response),
        content_size=content_size,
        timestamp=timestamp
    )
    ref_counter += 1

    # Extract list items with stable indices
    if action_tag.has_items:
        items = []
        item_indices = {}

        # Numbered items (preserve original numbering)
        for match in re.finditer(r'^\s*(\d+)[.)]\s+(.+)$', response, re.MULTILINE):
            idx = int(match.group(1))
            item_text = match.group(2).strip()[:80]
            items.append(item_text)
            item_indices[idx] = item_text

        # Bulleted items (sequential indexing)
        if not items:
            for i, match in enumerate(re.finditer(r'^\s*[-•*]\s+(.+)$', response, re.MULTILINE), 1):
                item_text = match.group(1).strip()[:80]
                items.append(item_text)
                item_indices[i] = item_text

        main_ref.items = items[:15]
        main_ref.item_indices = {k: v for k, v in list(item_indices.items())[:15]}

    referents.append(main_ref)

    # Extract code blocks as separate referents with hygiene
    code_blocks = re.findall(r'```(\w*)\n(.*?)```', response, re.DOTALL)
    for i, (lang, code) in enumerate(code_blocks):
        code_size = len(code)
        referents.append(Referent(
            ref_type="code",
            ref_id=f"code_{turn_number}_{i}",
            turn_number=turn_number,
            content_preview=summarize_content(code, MAX_REFERENT_CONTENT_SIZE // 5),
            content_hash=content_hash(code),
            content_size=code_size,
            language=lang or "unknown",
            timestamp=timestamp
        ))

    return referents


# =============================================================================
# Novelty Detection
# =============================================================================

def get_content_tokens(text: str) -> Set[str]:
    """Extract content tokens (non-stopwords) from text."""
    words = re.findall(r'\b[a-z]+\b', text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def calculate_novelty(
    user_input: str,
    last_user_prompt: str = "",
    last_ai_response: str = ""
) -> Tuple[float, Set[str]]:
    """
    Calculate how novel the user input is compared to recent context.

    Returns:
        (novelty_score 0-1, set of new tokens)
        0 = very similar to context (continuation)
        1 = completely new topic
    """
    input_tokens = get_content_tokens(user_input)

    if not input_tokens:
        return 0.0, set()  # No content tokens = likely continuation

    # Get context tokens
    context_tokens = set()
    if last_user_prompt:
        context_tokens.update(get_content_tokens(last_user_prompt))
    if last_ai_response:
        # Only use first 500 chars of response for efficiency
        context_tokens.update(get_content_tokens(last_ai_response[:500]))

    if not context_tokens:
        return 0.5, input_tokens  # No context to compare

    # Calculate Jaccard-like overlap
    overlap = input_tokens & context_tokens
    new_tokens = input_tokens - context_tokens

    # Novelty = proportion of new tokens
    if len(input_tokens) == 0:
        return 0.0, set()

    novelty = len(new_tokens) / len(input_tokens)

    # Boost novelty if new tokens include intent markers
    has_new_intent = bool(new_tokens & NEW_INTENT_MARKERS)
    if has_new_intent:
        novelty = min(1.0, novelty + 0.3)

    # Reduce novelty if input has continuation markers
    has_continuation = bool(input_tokens & CONTINUATION_MARKERS)
    if has_continuation:
        novelty = max(0.0, novelty - 0.4)

    return novelty, new_tokens


# =============================================================================
# Continuation Detection (with novelty check)
# =============================================================================

def is_continuation_input(
    user_input: str,
    last_action: Optional[ActionTag],
    referents: List[Referent],
    last_user_prompt: str = "",
    last_ai_response: str = ""
) -> bool:
    """
    Detect if user input is a continuation using: short + low-novelty.

    NOT just "short" - also checks for novelty to avoid treating
    "Pricing?" or "Email?" as continuations.
    """
    if not last_action and not referents:
        return False

    user_input = user_input.strip()
    input_lower = user_input.lower()
    words = input_lower.split()
    word_count = len(words)

    # Long inputs = new requests
    if word_count > 5:
        return False

    # Questions with new topics are NEW requests
    question_words = ['what', 'how', 'why', 'where', 'when', 'which', 'who']
    if words and words[0] in question_words and word_count >= 2:
        # But "why?" or "how?" alone could be continuation
        if word_count > 2:
            return False

    # Check for explicit continuation markers (high confidence)
    content_tokens = get_content_tokens(user_input)
    if content_tokens & CONTINUATION_MARKERS:
        return True

    # Check for new topic indicators
    if re.search(r'https?://|www\.', input_lower):
        return False
    if re.search(r'\d+\s*(?:dollars?|euros?|miles?|km|hours?|minutes?|days?|years?)', input_lower):
        return False

    # Explicit topic introducers
    if any(ti in input_lower for ti in ['about', 'regarding', 'concerning']) and word_count > 2:
        return False

    # NOVELTY CHECK - the key improvement
    novelty, new_tokens = calculate_novelty(user_input, last_user_prompt, last_ai_response)

    # Short + low novelty = continuation
    if word_count <= 3:
        # If novelty is high, it might be a new topic despite being short
        # e.g., "Pricing?" after a joke is high novelty
        if novelty > 0.7 and new_tokens - CONTINUATION_MARKERS:
            logger.debug(f"Short but high novelty ({novelty:.2f}): {new_tokens}")
            return False
        return True

    # Medium length with continuation structure
    continuation_patterns = [
        r'^(?:an)?other\b',
        r'^(?:one )?more\b',
        r'^next\b',
        r'^again\b',
        r'^different\b',
        r'^(?:try|do|show|tell|give)\s+(?:me\s+)?(?:another|more|again)',
        r'^(?:shorter|longer|simpler|funnier|better|worse)\b',
        r'^make (?:it|that)\b',
        r'^(?:the|that)\s+(?:one|first|second|third|last)\b',
    ]

    for pattern in continuation_patterns:
        if re.match(pattern, input_lower):
            return True

    return False


# =============================================================================
# Referent Scoring & Resolution
# =============================================================================

def score_referent(
    referent: Referent,
    user_input: str,
    current_turn: int,
    last_action: Optional[ActionTag] = None
) -> float:
    """
    Score a referent for how likely it is to be what the user refers to.

    Scoring factors:
    - Recency (more recent = higher score)
    - Type match (if user hints at type)
    - Action type match (continuation of same type)
    - Structural hints ("third one" → list)
    """
    score = 0.0
    input_lower = user_input.lower()

    # Recency score (0-0.4): newer is better
    turn_diff = current_turn - referent.turn_number
    recency_score = max(0, 0.4 - (turn_diff * 0.1))
    score += recency_score

    # Type hint matching (0-0.3)
    type_hints = {
        'code': ['code', 'function', 'script', 'program', 'snippet'],
        'joke': ['joke', 'funny'],
        'list': ['list', 'options', 'items', 'choices'],
        'recipe': ['recipe', 'dish', 'meal'],
        'explanation': ['explanation', 'answer'],
        'story': ['story', 'tale'],
        'quote': ['quote', 'saying'],
    }

    for ref_type, hints in type_hints.items():
        if referent.ref_type == ref_type and any(h in input_lower for h in hints):
            score += 0.3
            break

    # Action type continuation (0-0.2)
    if last_action and referent.ref_type == last_action.action_type:
        score += 0.2

    # Structural hints for lists (0-0.3)
    if referent.items or referent.item_indices:
        ordinal_patterns = [
            (r'\b(?:first|1st)\b', 1),
            (r'\b(?:second|2nd)\b', 2),
            (r'\b(?:third|3rd)\b', 3),
            (r'\b(?:fourth|4th)\b', 4),
            (r'\b(?:fifth|5th)\b', 5),
            (r'\b(?:last)\b', -1),
            (r'\bnumber\s*(\d+)\b', None),
            (r'\b(\d+)(?:st|nd|rd|th)\b', None),
        ]

        for pattern, idx in ordinal_patterns:
            match = re.search(pattern, input_lower)
            if match:
                score += 0.3
                break

    return min(1.0, score)


def resolve_pronoun(
    user_input: str,
    referents: List[Referent],
    last_by_type: Dict[str, Referent],
    current_turn: int,
    last_action: Optional[ActionTag] = None
) -> Tuple[Optional[Referent], float]:
    """
    Resolve "it/that/this/one" to a referent using scoring.

    Returns:
        (best_referent, confidence)
        If confidence is low (< 0.4), caller might want to clarify.
    """
    input_lower = user_input.lower()

    # Check if input contains pronouns that need resolution
    pronoun_patterns = [r'\bit\b', r'\bthat\b', r'\bthis\b', r'\bthe one\b', r'\bone\b']
    has_pronoun = any(re.search(p, input_lower) for p in pronoun_patterns)

    if not has_pronoun:
        return None, 0.0

    if not referents:
        return None, 0.0

    # Score all referents
    scored = []
    for ref in referents:
        score = score_referent(ref, user_input, current_turn, last_action)
        scored.append((ref, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    best_ref, best_score = scored[0]

    # Check if disambiguation is needed
    if len(scored) > 1:
        second_score = scored[1][1]
        # If top two are close, confidence is low
        if best_score - second_score < 0.15:
            return best_ref, best_score * 0.5  # Reduce confidence

    return best_ref, best_score


def resolve_list_index(user_input: str, referent: Referent) -> Optional[Tuple[int, str]]:
    """
    Resolve ordinal references like "the third one" to list item.

    Returns:
        (index, item_text) or None
    """
    if not referent.item_indices:
        return None

    input_lower = user_input.lower()

    # Ordinal words to numbers
    ordinal_map = {
        'first': 1, '1st': 1,
        'second': 2, '2nd': 2,
        'third': 3, '3rd': 3,
        'fourth': 4, '4th': 4,
        'fifth': 5, '5th': 5,
        'sixth': 6, '6th': 6,
        'seventh': 7, '7th': 7,
        'eighth': 8, '8th': 8,
        'ninth': 9, '9th': 9,
        'tenth': 10, '10th': 10,
        'last': -1,
    }

    for word, idx in ordinal_map.items():
        if word in input_lower:
            if idx == -1:
                # "last" = highest index
                max_idx = max(referent.item_indices.keys())
                return max_idx, referent.item_indices[max_idx]
            elif idx in referent.item_indices:
                return idx, referent.item_indices[idx]

    # Check for "number N" or "N" patterns
    num_match = re.search(r'\bnumber\s*(\d+)\b|\b(\d+)(?:st|nd|rd|th)?\s*(?:one)?\b', input_lower)
    if num_match:
        num = int(num_match.group(1) or num_match.group(2))
        if num in referent.item_indices:
            return num, referent.item_indices[num]

    return None


# =============================================================================
# Missing Slot Detection (deterministic table)
# =============================================================================

# Operations that require a specific referent type
# (pattern, required_types, slot_description, strict)
# strict=True means ONLY those types work; strict=False means any context is acceptable
SLOT_REQUIREMENTS = [
    # Text operations - any context is acceptable (strict=False)
    (r'\b(?:summarize|summarise)\s+(?:it|this|that)\b', None, 'text to summarize', False),
    (r'\b(?:translate)\s+(?:it|this|that)\b', None, 'text to translate', False),
    (r'\b(?:rewrite|rephrase)\s+(?:it|this|that)\b', None, 'text to rewrite', False),

    # Code operations - STRICTLY require code context
    (r'\b(?:fix|debug|correct)\s+(?:it|this|that|the code)\b', {'code'}, 'code to fix', True),
    (r'\b(?:run|execute)\s+(?:it|this|that|the code)\b', {'code'}, 'code to run', True),
    (r'\b(?:improve|optimize)\s+(?:it|this|that|the code)\b', {'code'}, 'code to improve', True),
    (r'\b(?:explain)\s+(?:the code|this code|that code)\b', {'code'}, 'code to explain', True),

    # Content operations - any context is acceptable
    (r'\b(?:send|email|post)\s+(?:it|this|that)\b', None, 'content to send', False),
    (r'\b(?:save|store)\s+(?:it|this|that)\b', None, 'content to save', False),
    (r'\b(?:convert)\s+(?:it|this|that)\b', None, 'content to convert', False),

    # List operations - require list context
    (r'\bthe\s+(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)\s+(?:one|option|item)\b',
     {'list'}, 'list to select from', True),
]

# Operations that do NOT require a referent (continuations)
CONTINUATION_OPERATIONS = {
    r'\b(?:another|other)\s*(?:one)?\b',
    r'\bmore\b',
    r'\bcontinue\b',
    r'\bnext\b',
    r'\bagain\b',
    r'\bgo\s+on\b',
    r'\bkeep\s+going\b',
}


def needs_slot_clarification(
    user_input: str,
    referents: List[Referent],
    last_by_type: Dict[str, Referent]
) -> Optional[str]:
    """
    Check if user input requires a slot/referent that's missing.

    Only returns a slot name when:
    1. The operation explicitly requires an object
    2. No matching referent exists in context

    Returns None for continuation operations.
    """
    input_lower = user_input.lower().strip()

    # First check: is this a continuation operation? If so, never intercept.
    for pattern in CONTINUATION_OPERATIONS:
        if re.search(pattern, input_lower):
            return None

    # Get available referent types
    ref_types = {r.ref_type for r in referents} if referents else set()
    ref_types.update(last_by_type.keys())
    has_any_context = bool(ref_types)

    # Check each slot requirement
    for pattern, required_types, slot_desc, strict in SLOT_REQUIREMENTS:
        if re.search(pattern, input_lower):
            if strict:
                # Strict mode: must have one of the required types
                if required_types and not (ref_types & required_types):
                    return slot_desc
            else:
                # Non-strict mode: any context is acceptable
                if not has_any_context:
                    return slot_desc

    return None


# =============================================================================
# Conversation Context Manager
# =============================================================================

class ConversationContext:
    """
    Manages conversation state for natural continuations.

    Features:
    - Action tagging with confidence
    - Typed referent stack with stable indices
    - Novelty-based continuation detection (token + embedding)
    - Pronoun resolution via scoring
    - Deterministic slot detection
    - Instrumentation & shadow metrics
    - Referent hygiene (eviction, staleness)
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_action: Optional[ActionTag] = None
        self.turn_number: int = 0
        self.last_user_prompt: str = ""
        self.last_ai_response: str = ""
        self.last_user_input_time: Optional[datetime] = None

        # Referent storage
        self._recent_referents: deque = deque(maxlen=10)  # Recent referents (any type)
        self._last_by_type: Dict[str, Referent] = {}  # Quick lookup: type -> most recent

        # Shadow metrics tracking
        self._last_intercepted: bool = False
        self._last_intercept_input: str = ""
        self._last_novelty_score: float = 0.0

    def tag_response(self, response: str, user_prompt: str = "") -> ActionTag:
        """
        Analyze and tag the AI's response.
        """
        self.turn_number += 1
        self.last_user_prompt = user_prompt
        self.last_ai_response = response

        # Detect action type with confidence
        self.last_action = detect_action_type(response, user_prompt, self.turn_number)

        # Extract and store referents (with hygiene applied)
        new_refs = extract_referents(response, self.last_action, self.turn_number)

        for ref in new_refs:
            self._recent_referents.appendleft(ref)
            self._last_by_type[ref.ref_type] = ref

        # Trace logging
        _trace_logger.log_action_tag(
            self.session_id,
            self.last_action.action_type,
            self.last_action.confidence
        )

        logger.debug(
            f"ConversationContext: Turn {self.turn_number}, "
            f"tagged as {self.last_action.action_type} "
            f"(confidence: {self.last_action.confidence:.2f}), "
            f"{len(self._recent_referents)} referents"
        )

        return self.last_action

    def is_continuation(self, user_input: str) -> bool:
        """
        Check if user input is a continuation (short + low novelty).
        Uses embedding-based novelty if available for multilingual support.
        """
        # Calculate novelty - prefer embedding if available
        if has_embedding_support():
            novelty, method = calculate_novelty_embedding(
                user_input,
                self.last_user_prompt,
                self.last_ai_response
            )
        else:
            novelty, _ = calculate_novelty(
                user_input,
                self.last_user_prompt,
                self.last_ai_response
            )
            method = "token"

        self._last_novelty_score = novelty

        # Check for topic shift and clean stale referents if needed
        if is_topic_shift(novelty, user_input):
            self._evict_stale_referents()

        # Determine continuation
        result = is_continuation_input(
            user_input,
            self.last_action,
            list(self._recent_referents),
            self.last_user_prompt,
            self.last_ai_response
        )

        # Trace logging
        _trace_logger.log_continuation_check(
            self.session_id,
            user_input,
            result,
            novelty,
            method
        )

        # Shadow metrics
        if SHADOW_METRICS_ENABLED:
            _shadow_metrics.record_continuation(result, novelty)

        return result

    def get_missing_slot(self, user_input: str) -> Optional[str]:
        """
        Check if user input requires a slot that's missing.
        Records shadow metrics for interception tracking.
        """
        # Check for false intercept signals from previous turn
        if self._last_intercepted and SHADOW_METRICS_ENABLED:
            if is_false_intercept_signal(user_input):
                _shadow_metrics.record_false_intercept()
            elif self.last_user_input_time:
                time_gap = (datetime.utcnow() - self.last_user_input_time).total_seconds()
                if is_likely_rephrase(user_input, self._last_intercept_input, time_gap):
                    _shadow_metrics.record_rephrase()

        # Update timing
        self.last_user_input_time = datetime.utcnow()

        missing_slot = needs_slot_clarification(
            user_input,
            list(self._recent_referents),
            self._last_by_type
        )

        # Track interception
        intercepted = missing_slot is not None
        self._last_intercepted = intercepted
        if intercepted:
            self._last_intercept_input = user_input

        # Trace logging
        _trace_logger.log_slot_check(
            self.session_id,
            user_input,
            missing_slot or "",
            intercepted
        )

        # Shadow metrics
        if SHADOW_METRICS_ENABLED:
            action_type = self.last_action.action_type if self.last_action else ""
            _shadow_metrics.record_check(intercepted, action_type)

        return missing_slot

    def resolve_pronoun(self, user_input: str) -> Tuple[Optional[Referent], float]:
        """
        Resolve "it/that/this" to a referent.
        Returns (referent, confidence).
        """
        ref, score = resolve_pronoun(
            user_input,
            list(self._recent_referents),
            self._last_by_type,
            self.turn_number,
            self.last_action
        )

        # Calculate score gap for logging
        score_gap = 0.0
        if ref:
            all_refs = list(self._recent_referents)
            if len(all_refs) > 1:
                scores = sorted(
                    [score_referent(r, user_input, self.turn_number, self.last_action) for r in all_refs],
                    reverse=True
                )
                if len(scores) > 1:
                    score_gap = scores[0] - scores[1]

        # Trace logging
        _trace_logger.log_pronoun_resolution(
            self.session_id,
            user_input,
            ref.ref_type if ref else "",
            score,
            score_gap
        )

        return ref, score

    def _evict_stale_referents(self):
        """
        Remove stale referents on topic shift.
        Keeps only recent referents and clears type index for stale types.
        """
        # Keep only non-stale referents
        fresh_refs = [
            ref for ref in self._recent_referents
            if not ref.is_stale(self.turn_number)
        ]

        self._recent_referents.clear()
        for ref in fresh_refs:
            self._recent_referents.append(ref)

        # Update type index - remove stale entries
        stale_types = [
            ref_type for ref_type, ref in self._last_by_type.items()
            if ref.is_stale(self.turn_number)
        ]
        for ref_type in stale_types:
            del self._last_by_type[ref_type]

        logger.debug(
            f"ConversationContext: Evicted stale referents, "
            f"{len(self._recent_referents)} remaining"
        )

    def resolve_list_reference(self, user_input: str) -> Optional[Tuple[int, str]]:
        """
        Resolve "the third one" etc. to a list item.
        Returns (index, item_text) or None.
        """
        # Try the most recent list-type referent
        list_ref = self._last_by_type.get('list')
        if list_ref and list_ref.item_indices:
            return resolve_list_index(user_input, list_ref)

        # Try any referent with items
        for ref in self._recent_referents:
            if ref.item_indices:
                result = resolve_list_index(user_input, ref)
                if result:
                    return result

        return None

    def get_last_action_type(self) -> str:
        """Get the type of the last AI action."""
        return self.last_action.action_type if self.last_action else "unknown"

    def get_last_action_confidence(self) -> float:
        """Get the confidence of the last action type detection."""
        return self.last_action.confidence if self.last_action else 0.0

    def get_referent_by_type(self, ref_type: str) -> Optional[Referent]:
        """Get most recent referent of a specific type."""
        return self._last_by_type.get(ref_type)

    def get_recent_referents(self, limit: int = 5) -> List[Referent]:
        """Get recent referents."""
        return list(self._recent_referents)[:limit]

    def get_referent_stats(self) -> Dict:
        """Get statistics about the referent stack for monitoring."""
        total_size = sum(r.content_size for r in self._recent_referents)
        heavy_count = sum(1 for r in self._recent_referents if r.is_heavy())
        stale_count = sum(1 for r in self._recent_referents if r.is_stale(self.turn_number))

        return {
            "total_referents": len(self._recent_referents),
            "total_content_size": total_size,
            "heavy_referents": heavy_count,
            "stale_referents": stale_count,
            "types_tracked": list(self._last_by_type.keys()),
            "turn_number": self.turn_number,
        }

    def get_last_novelty_score(self) -> float:
        """Get the novelty score from the last continuation check."""
        return self._last_novelty_score

    def clear(self):
        """Clear context (e.g., on new conversation)."""
        self.last_action = None
        self.turn_number = 0
        self.last_user_prompt = ""
        self.last_ai_response = ""
        self.last_user_input_time = None
        self._recent_referents.clear()
        self._last_by_type.clear()
        self._last_intercepted = False
        self._last_intercept_input = ""
        self._last_novelty_score = 0.0

    def to_dict(self) -> Dict:
        """Serialize for storage."""
        return {
            "session_id": self.session_id,
            "turn_number": self.turn_number,
            "last_user_prompt": self.last_user_prompt,
            "last_ai_response": self.last_ai_response[:200],  # Truncate for storage
            "last_action": self.last_action.to_dict() if self.last_action else None,
            "recent_referents": [r.to_dict() for r in self._recent_referents],
            "last_by_type": {k: v.to_dict() for k, v in self._last_by_type.items()}
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ConversationContext":
        """Deserialize from storage."""
        ctx = cls(data.get("session_id", ""))
        ctx.turn_number = data.get("turn_number", 0)
        ctx.last_user_prompt = data.get("last_user_prompt", "")
        ctx.last_ai_response = data.get("last_ai_response", "")

        if data.get("last_action"):
            ctx.last_action = ActionTag.from_dict(data["last_action"])

        for ref_data in data.get("recent_referents", []):
            ctx._recent_referents.append(Referent.from_dict(ref_data))

        for ref_type, ref_data in data.get("last_by_type", {}).items():
            ctx._last_by_type[ref_type] = Referent.from_dict(ref_data)

        return ctx


# =============================================================================
# Cache for contexts (in-memory, per session)
# =============================================================================

_context_cache: Dict[str, ConversationContext] = {}


def get_conversation_context(session_id: str) -> ConversationContext:
    """Get or create conversation context for a session."""
    if session_id not in _context_cache:
        _context_cache[session_id] = ConversationContext(session_id)
    return _context_cache[session_id]


def clear_conversation_context(session_id: str):
    """Clear context for a session."""
    if session_id in _context_cache:
        del _context_cache[session_id]


# =============================================================================
# Test Suite
# =============================================================================

def run_tests():
    """
    Run test matrix to validate the routing logic.
    """
    print("=" * 60)
    print("CONVERSATION CONTEXT TEST SUITE")
    print("=" * 60)

    results = {"passed": 0, "failed": 0}

    def test(name: str, condition: bool):
        status = "✓ PASS" if condition else "✗ FAIL"
        print(f"  {status}: {name}")
        if condition:
            results["passed"] += 1
        else:
            results["failed"] += 1

    # ===========================================
    # Test 1: Continuation Detection
    # ===========================================
    print("\n1. CONTINUATION DETECTION")
    print("-" * 40)

    ctx = ConversationContext("test")

    # Joke -> "other one"
    ctx.tag_response(
        "Why did the chicken cross the road? To get to the other side!",
        "tell me a joke"
    )
    test("joke → 'other one' = continuation", ctx.is_continuation("other one"))
    test("joke → 'another' = continuation", ctx.is_continuation("another"))
    test("joke → 'next' = continuation", ctx.is_continuation("next"))

    # List of options -> "third one"
    ctx.tag_response(
        "Here are your options:\n1. Pizza\n2. Burger\n3. Salad\n4. Pasta",
        "what should I eat"
    )
    test("list → 'the third one' = continuation", ctx.is_continuation("the third one"))
    test("list → 'number 2' = continuation", ctx.is_continuation("number 2"))

    # Code -> "make it faster"
    ctx.tag_response(
        "```python\ndef slow_func():\n    time.sleep(1)\n```",
        "write a function"
    )
    test("code → 'make it faster' = continuation", ctx.is_continuation("make it faster"))
    test("code → 'shorter' = continuation", ctx.is_continuation("shorter"))

    # Explanation -> "why?" / "go on"
    ctx.tag_response(
        "Python is a programming language. It was created by Guido van Rossum.",
        "explain python"
    )
    test("explanation → 'why?' = continuation", ctx.is_continuation("why?"))
    test("explanation → 'go on' = continuation", ctx.is_continuation("go on"))

    # ===========================================
    # Test 2: Short-but-new-topic Detection
    # ===========================================
    print("\n2. SHORT-BUT-NEW-TOPIC DETECTION")
    print("-" * 40)

    # After joke -> "pricing?" (short but new topic)
    ctx.tag_response(
        "Why did the chicken cross the road? To get to the other side!",
        "tell me a joke"
    )
    test("after joke → 'pricing?' = NOT continuation", not ctx.is_continuation("pricing?"))
    test("after joke → 'weather?' = NOT continuation", not ctx.is_continuation("weather?"))
    test("after joke → 'email?' = NOT continuation", not ctx.is_continuation("email?"))

    # After recipe -> new topic
    ctx.tag_response(
        "Here's a recipe for pasta:\nIngredients: pasta, sauce...",
        "give me a recipe"
    )
    test("after recipe → 'jokes?' = NOT continuation", not ctx.is_continuation("jokes?"))
    test("after recipe → 'python?' = NOT continuation", not ctx.is_continuation("python?"))

    # ===========================================
    # Test 3: Missing Slot Detection
    # ===========================================
    print("\n3. MISSING SLOT DETECTION")
    print("-" * 40)

    ctx.clear()
    test("no artifact → 'summarize it' = missing slot",
         ctx.get_missing_slot("summarize it") is not None)
    test("no list → 'the second one' = missing slot",
         ctx.get_missing_slot("the second one") is not None)
    test("no code → 'fix that code' = missing slot",
         ctx.get_missing_slot("fix that code") is not None)

    # With context - should NOT need slot
    ctx.tag_response("Here's some info...", "tell me about X")
    test("with context → 'summarize it' = NO missing slot",
         ctx.get_missing_slot("summarize it") is None)

    ctx.tag_response("```python\nprint('hi')\n```", "write code")
    test("with code → 'fix it' = NO missing slot",
         ctx.get_missing_slot("fix it") is None)

    # Continuation operations should never need slots
    ctx.clear()
    test("'another one' = NO missing slot (continuation)",
         ctx.get_missing_slot("another one") is None)
    test("'more please' = NO missing slot (continuation)",
         ctx.get_missing_slot("more please") is None)

    # ===========================================
    # Test 4: Action Type Detection with Confidence
    # ===========================================
    print("\n4. ACTION TYPE DETECTION")
    print("-" * 40)

    tag = detect_action_type("Why did the chicken cross the road?", "tell me a joke")
    test("joke detection", tag.action_type == "joke")
    test("joke confidence > 0.7", tag.confidence > 0.7)

    tag = detect_action_type("```python\ndef hello():\n    pass\n```", "write code")
    test("code detection", tag.action_type == "code")
    test("code confidence > 0.8", tag.confidence > 0.8)

    tag = detect_action_type("Here are 5 tips:\n1. First\n2. Second", "give me tips")
    test("list detection", tag.action_type == "list")
    test("list has_items", tag.has_items)
    test("list item_count >= 2", tag.item_count >= 2)

    # ===========================================
    # Test 5: Novelty Calculation
    # ===========================================
    print("\n5. NOVELTY CALCULATION")
    print("-" * 40)

    novelty, new_tokens = calculate_novelty("another joke", "tell me a joke", "Here's a joke...")
    test("'another joke' after joke = low novelty", novelty < 0.5)

    novelty, new_tokens = calculate_novelty("what about pricing", "tell me a joke", "Here's a joke...")
    test("'what about pricing' after joke = high novelty", novelty > 0.5)

    novelty, new_tokens = calculate_novelty("other one", "tell me a joke", "Here's a joke...")
    test("'other one' = very low novelty", novelty < 0.3)

    # ===========================================
    # Summary
    # ===========================================
    print("\n" + "=" * 60)
    print(f"RESULTS: {results['passed']} passed, {results['failed']} failed")
    print("=" * 60)

    return results["failed"] == 0


if __name__ == "__main__":
    run_tests()

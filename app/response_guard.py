"""
Response Guard - Server-Side Enforcement
=========================================
Enforces rules that LLMs might ignore:
    1. Duplicate detection - block repeated responses
    2. Ambiguity detection - intercept vague inputs before LLM
    3. Fact consistency - track and check for contradictions

Problem:
    LLM has rules in system prompt but still:
    - Repeats same content when asked "yes please"
    - Contradicts itself (Thyatira in Usak vs Akhisar)
    - Doesn't ask clarifying questions

Solution:
    Server-side checks BEFORE and AFTER LLM call.

Usage:
    from app.response_guard import ResponseGuard

    guard = ResponseGuard(user_id, session_id)

    # Before LLM call
    intercept = guard.check_input(user_prompt, previous_messages)
    if intercept:
        return intercept  # Return clarification question directly

    # After LLM call
    validation = guard.validate_response(response, previous_messages)
    if not validation.is_valid:
        # Handle duplicate or contradiction
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Set
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# =============================================================================
# Ambiguity Detection Patterns
# =============================================================================

# ACTION CONFIRMATIONS — these are NOT vague. They mean "do it."
# These should NEVER be intercepted. The user is telling us to proceed.
ACTION_CONFIRMATIONS = [
    r"^yes\s*(?:please)?\.?$",
    r"^yeah?\s*(?:please)?\.?$",
    r"^sure\.?$",
    r"^ok(?:ay)?\.?$",
    r"^go\s*(?:ahead|on)\.?$",
    r"^do\s*it\.?$",
    r"^please\s*do\.?$",
    r"^that\s*(?:would\s+be\s+)?(?:great|nice|helpful)\.?$",
    r"^just\s*do\s*it\.?$",
    r"^skip\.?$",
    r"^proceed\.?$",
    r"^continue\.?$",
    r"^definitely\.?$",
    r"^absolutely\.?$",
]

# Vague references — only intercept "more"/"elaborate" when there are
# 3+ distinct items to expand on. Otherwise let the LLM handle it.
VAGUE_REFERENCES = [
    r"^(?:more|elaborate|expand|explain)\.?$",
    r"^(?:more|tell\s+me\s+more)(?:\s+(?:about|on)\s+(?:that|it|this))?\.?$",
]


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ValidationResult:
    """Result of response validation."""
    is_valid: bool = True
    issues: List[str] = field(default_factory=list)
    similarity_score: float = 0.0
    is_duplicate: bool = False
    contradictions: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "issues": self.issues,
            "similarity_score": self.similarity_score,
            "is_duplicate": self.is_duplicate,
            "contradictions": self.contradictions,
        }


@dataclass
class InterceptResult:
    """Result when input should be intercepted."""
    should_intercept: bool = False
    reason: str = ""
    clarification_prompt: str = ""
    context_from_history: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_intercept": self.should_intercept,
            "reason": self.reason,
            "clarification_prompt": self.clarification_prompt,
        }


# =============================================================================
# Response Guard
# =============================================================================

class ResponseGuard:
    """
    Server-side enforcement of conversation rules.

    Checks:
        1. Input ambiguity - intercept vague messages
        2. Response duplication - detect repeated content
        3. Fact consistency - track and check contradictions
    """

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id
        self._facts: Dict[str, str] = {}  # key -> value facts
        self._loaded = False

    # =========================================================================
    # Input Checking (Before LLM)
    # =========================================================================

    def check_input(
        self,
        user_input: str,
        previous_messages: List[Dict[str, Any]]
    ) -> Optional[InterceptResult]:
        """
        Check if user input should be intercepted before sending to LLM.

        Returns InterceptResult if should intercept, None otherwise.
        """
        user_input = user_input.strip()
        input_lower = user_input.lower()

        # Action confirmations ("yes", "go ahead", "do it") are NEVER intercepted.
        # The user is telling us to proceed — let the LLM act on it.
        for pattern in ACTION_CONFIRMATIONS:
            if re.match(pattern, input_lower):
                return None

        # Only intercept truly vague expansion requests ("more", "elaborate")
        # when there are multiple distinct items that could be expanded.
        for pattern in VAGUE_REFERENCES:
            if re.match(pattern, input_lower):
                return self._handle_vague_reference(user_input, previous_messages)

        return None

    def _handle_vague_affirmation(
        self,
        user_input: str,
        previous_messages: List[Dict[str, Any]]
    ) -> Optional[InterceptResult]:
        """
        Handle vague affirmations like "yes please", "sure".

        Try to extract what they're affirming from the last AI response.
        """
        if not previous_messages:
            return InterceptResult(
                should_intercept=True,
                reason="vague_affirmation_no_context",
                clarification_prompt="I'd like to help! Could you tell me what you'd like me to do?",
            )

        # Get last AI response
        last_response = ""
        for msg in reversed(previous_messages):
            if msg.get("response"):
                last_response = msg["response"]
                break

        if not last_response:
            return None

        # Look for offers/questions in the last response
        offers = self._extract_offers(last_response)

        if len(offers) == 0:
            # No clear offers, can't determine what they're affirming
            return InterceptResult(
                should_intercept=True,
                reason="vague_affirmation_no_offers",
                clarification_prompt="I want to make sure I understand - what would you like me to elaborate on?",
            )

        if len(offers) == 1:
            # Single clear offer - we can infer, don't intercept
            return None

        # Multiple offers - need clarification
        options_text = "\n".join(f"- {offer}" for offer in offers[:4])
        return InterceptResult(
            should_intercept=True,
            reason="vague_affirmation_multiple_offers",
            clarification_prompt=f"I offered a few things. Which would you like?\n{options_text}",
            context_from_history=last_response[:200],
        )

    def _handle_vague_reference(
        self,
        user_input: str,
        previous_messages: List[Dict[str, Any]]
    ) -> Optional[InterceptResult]:
        """
        Handle vague references like "more", "tell me more about that".
        """
        if not previous_messages:
            return InterceptResult(
                should_intercept=True,
                reason="vague_reference_no_context",
                clarification_prompt="Could you clarify what you'd like me to expand on?",
            )

        # Get last exchange
        last_prompt = ""
        last_response = ""
        for msg in reversed(previous_messages):
            if msg.get("response"):
                last_response = msg["response"]
                last_prompt = msg.get("prompt", "")
                break

        if not last_response:
            return None

        # Extract topics from last response
        topics = self._extract_topics(last_response)

        if len(topics) <= 2:
            return None  # LLM can infer with 0-2 topics

        # 3+ distinct topics - "more" is genuinely ambiguous
        topics_text = "\n".join(f"- {topic}" for topic in topics[:5])
        return InterceptResult(
            should_intercept=True,
            reason="vague_reference_multiple_topics",
            clarification_prompt=f"I covered several topics. Which would you like more detail on?\n{topics_text}",
        )

    def _extract_offers(self, text: str) -> List[str]:
        """Extract offers/questions from AI response."""
        offers = []

        # Look for "Would you like..." patterns
        patterns = [
            r"would you like\s+(?:me to\s+)?(.+?)\?",
            r"do you want\s+(?:me to\s+)?(.+?)\?",
            r"shall i\s+(.+?)\?",
            r"i can\s+(.+?)(?:\.|$)",
            r"if you(?:'d)? like,?\s+i can\s+(.+?)(?:\.|$)",
            r"let me know if you(?:'d)? like\s+(.+?)(?:\!|\?|$)",
            r"feel free to ask\s+(?:about\s+)?(.+?)(?:\.|$)",
            r"happy to\s+(?:provide|explain|elaborate|expand)\s+(.+?)(?:\.|$)",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text.lower()):
                offer = match.group(1).strip()
                if offer and len(offer) > 5:
                    offers.append(offer[:100])

        # Also check for numbered options
        numbered = re.findall(r'\d+[.)]\s+(.+?)(?:\n|$)', text)
        if len(numbered) >= 2:
            offers.extend([n[:100] for n in numbered[:5]])

        # Check for list items (Name – Description or **Name** – Description)
        list_items = re.findall(r'(?:^|\n)\s*(?:\*\*)?([A-Z][a-zA-Z\s]{2,30})(?:\*\*)?\s*[–\-—]\s*', text)
        if len(list_items) >= 3:
            # If we have a list AND an offer mentioning "any", use list items as options
            text_lower = text.lower()
            if 'any' in text_lower and ('specific' in text_lower or 'particular' in text_lower):
                offers.extend([item.strip() for item in list_items[:7]])

        return list(set(offers))[:7]

    def _extract_topics(self, text: str) -> List[str]:
        """Extract main topics from AI response."""
        topics = []

        # Extract headers (lines ending with :)
        headers = re.findall(r'^([A-Z][^:\n]{5,50}):', text, re.MULTILINE)
        topics.extend(headers)

        # Extract numbered items
        numbered = re.findall(r'\d+[.)]\s+([^–\-\n]{5,80})', text)
        topics.extend(numbered[:10])

        # Extract bold items (markdown)
        bold = re.findall(r'\*\*([^*]{3,50})\*\*', text)
        topics.extend(bold)

        # Deduplicate and clean
        cleaned = []
        seen = set()
        for topic in topics:
            topic = topic.strip().rstrip(':.,')
            topic_lower = topic.lower()
            if topic_lower not in seen and len(topic) > 3:
                seen.add(topic_lower)
                cleaned.append(topic)

        return cleaned[:10]

    # =========================================================================
    # Response Validation (After LLM)
    # =========================================================================

    def validate_response(
        self,
        response: str,
        previous_messages: List[Dict[str, Any]],
        similarity_threshold: float = 0.75
    ) -> ValidationResult:
        """
        Validate AI response for duplicates and contradictions.

        Args:
            response: The AI's response text
            previous_messages: Previous messages in session
            similarity_threshold: Max similarity to previous responses (0.75 = 75%)

        Returns:
            ValidationResult with issues found
        """
        result = ValidationResult()

        if not response or not previous_messages:
            return result

        # Check for duplicate/too-similar responses
        for msg in previous_messages[-5:]:  # Check last 5 responses
            prev_response = msg.get("response", "")
            if not prev_response:
                continue

            similarity = self._compute_similarity(response, prev_response)
            if similarity > similarity_threshold:
                result.is_valid = False
                result.is_duplicate = True
                result.similarity_score = similarity
                result.issues.append(
                    f"Response is {similarity*100:.0f}% similar to a previous response"
                )
                break

        # Check for fact contradictions
        contradictions = self._check_fact_consistency(response, previous_messages)
        if contradictions:
            result.is_valid = False
            result.contradictions = contradictions
            for c in contradictions:
                result.issues.append(
                    f"Contradiction: '{c['entity']}' was '{c['old_value']}', now '{c['new_value']}'"
                )

        return result

    def _compute_similarity(self, text1: str, text2: str) -> float:
        """Compute similarity between two texts."""
        # Normalize texts
        text1 = self._normalize_for_comparison(text1)
        text2 = self._normalize_for_comparison(text2)

        if not text1 or not text2:
            return 0.0

        # Use SequenceMatcher for similarity
        return SequenceMatcher(None, text1, text2).ratio()

    def _normalize_for_comparison(self, text: str) -> str:
        """Normalize text for comparison."""
        # Lowercase
        text = text.lower()
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove punctuation
        text = re.sub(r'[^\w\s]', '', text)
        # Truncate to first 500 chars for efficiency
        return text[:500].strip()

    def _check_fact_consistency(
        self,
        response: str,
        previous_messages: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """
        Check for fact contradictions between response and previous messages.

        Looks for patterns like "X is Y" and checks if X was previously
        said to be something different.
        """
        contradictions = []

        # Extract facts from current response
        current_facts = self._extract_facts(response)

        # Extract facts from previous responses
        previous_facts = {}
        for msg in previous_messages[-10:]:
            prev_response = msg.get("response", "")
            if prev_response:
                facts = self._extract_facts(prev_response)
                previous_facts.update(facts)

        # Check for contradictions
        for entity, new_value in current_facts.items():
            if entity in previous_facts:
                old_value = previous_facts[entity]
                # Check if values are different (allowing for minor variations)
                if not self._values_match(old_value, new_value):
                    contradictions.append({
                        "entity": entity,
                        "old_value": old_value,
                        "new_value": new_value,
                    })

        return contradictions

    def _extract_facts(self, text: str) -> Dict[str, str]:
        """
        Extract entity-value facts from text.

        Patterns:
            - "X is located in Y"
            - "X – Y" (list items)
            - "X is Y"
        """
        facts = {}

        # Pattern: "Entity – Location/Description"
        # e.g., "Thyatira – Modern-day Akhisar"
        pattern1 = re.findall(
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*[–\-]\s*([^.\n,]{5,100})',
            text
        )
        for entity, value in pattern1:
            entity_key = entity.lower().strip()
            facts[entity_key] = value.strip()

        # Pattern: "Entity is located in/at/near Location"
        pattern2 = re.findall(
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:is\s+)?(?:located\s+)?(?:in|at|near)\s+([^.\n,]{3,100})',
            text,
            re.IGNORECASE
        )
        for entity, value in pattern2:
            entity_key = entity.lower().strip()
            if entity_key not in facts:
                facts[entity_key] = value.strip()

        return facts

    def _values_match(self, value1: str, value2: str) -> bool:
        """Check if two fact values match (allowing minor variations)."""
        v1 = value1.lower().strip()
        v2 = value2.lower().strip()

        # Exact match
        if v1 == v2:
            return True

        # One contains the other
        if v1 in v2 or v2 in v1:
            return True

        # High similarity
        similarity = SequenceMatcher(None, v1, v2).ratio()
        if similarity > 0.8:
            return True

        return False


# =============================================================================
# Convenience Functions
# =============================================================================

_guard_cache: Dict[str, ResponseGuard] = {}


def get_response_guard(user_id: str, session_id: str) -> ResponseGuard:
    """Get or create a response guard for a session."""
    cache_key = f"{user_id}:{session_id}"
    if cache_key not in _guard_cache:
        _guard_cache[cache_key] = ResponseGuard(user_id, session_id)
    return _guard_cache[cache_key]


def check_input_ambiguity(
    user_id: str,
    session_id: str,
    user_input: str,
    previous_messages: List[Dict[str, Any]]
) -> Optional[InterceptResult]:
    """
    Check if user input is too ambiguous and should be intercepted.

    Returns InterceptResult with clarification prompt if should intercept.
    """
    guard = get_response_guard(user_id, session_id)
    return guard.check_input(user_input, previous_messages)


def validate_ai_response(
    user_id: str,
    session_id: str,
    response: str,
    previous_messages: List[Dict[str, Any]]
) -> ValidationResult:
    """
    Validate AI response for duplicates and contradictions.
    """
    guard = get_response_guard(user_id, session_id)
    return guard.validate_response(response, previous_messages)

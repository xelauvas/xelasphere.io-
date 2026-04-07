"""
Smart Guard - Context-Aware Response Validation
================================================
Uses conversation context + embeddings instead of LLM for ambiguity detection.

Architecture:
    1. Action tagging - tracks what AI just did (joke, list, code, etc.)
    2. Referent stack - tracks artifacts that can be referenced
    3. Continuation detection - detects "other one", "again", etc. via structure
    4. Missing slot detection - only intercepts when something is genuinely missing

Features:
    - Context-based continuation detection (no LLM needed)
    - Embedding-based duplicate detection - semantic similarity
    - Only intercepts when a required slot is missing

Usage:
    from app.smart_guard import SmartGuard

    guard = SmartGuard(user_id, session_id)

    # After AI responds - tag the response
    guard.tag_response(ai_response, user_prompt)

    # Before next LLM call
    intercept = guard.check_input(user_input, previous_messages)
    if intercept.should_intercept:
        return intercept.clarification_prompt

    # After LLM call
    validation = guard.validate_response(response, previous_messages)
"""

import json
import logging
import re
import requests
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Import conversation context for continuation detection
try:
    from .conversation_context import (
        ConversationContext,
        get_conversation_context,
        is_continuation_input,
        needs_slot_clarification,
        detect_action_type,
        extract_referents
    )
    HAS_CONTEXT_MODULE = True
except ImportError:
    HAS_CONTEXT_MODULE = False
    logger.warning("conversation_context module not available, using fallback")


# =============================================================================
# LLM Prompts
# =============================================================================

AMBIGUITY_CHECK_PROMPT = """Analyze if the user's input needs clarification based on conversation context.

CONVERSATION CONTEXT:
{conversation_context}

CURRENT USER INPUT:
{user_input}

Return ONLY valid JSON:
{{
  "is_ambiguous": true or false,
  "reason": "vague_affirmation" | "vague_reference" | "clear",
  "options_from_ai": ["option1", "option2"] or [],
  "clarification_prompt": "question to ask" or null
}}

ONLY mark as ambiguous if:
- Input is JUST "yes", "sure", "ok", "go ahead", "please" (1-2 words, no topic)
- Input is JUST "more" or "elaborate" (1 word, no topic specified)
- Input has NO connection to the conversation context above

NOT ambiguous (mark as "clear"):
- "other one", "another one", "next" - clearly refers to previous request topic (e.g., user asked for joke, got joke, says "other one" = wants another joke)
- "more about X" (has a topic)
- "tell me about the churches" (specific subject)
- Any input that logically continues the conversation shown above
- Any question with a clear subject
- Short inputs that clearly reference what was just discussed

When in doubt, return {{"is_ambiguous": false, "reason": "clear"}}"""


DUPLICATE_CHECK_PROMPT = """Compare these two AI responses for redundancy.

PREVIOUS RESPONSE:
{prev_response}

NEW RESPONSE:
{new_response}

Return ONLY valid JSON:
{{
  "is_duplicate": true or false,
  "similarity_percent": 0-100,
  "reason": "same_content" | "same_structure" | "new_info" | "different_angle"
}}

Rules:
- Same facts/list repeated = duplicate (even if worded differently)
- Same structure with minor changes = duplicate
- New examples, deeper detail, different aspect = NOT duplicate"""


FACT_CHECK_PROMPT = """Check for factual contradictions between responses.

PREVIOUS FACTS STATED:
{previous_facts}

NEW RESPONSE:
{new_response}

Return ONLY valid JSON:
{{
  "has_contradiction": true or false,
  "contradictions": [
    {{"entity": "X", "old_value": "Y", "new_value": "Z"}}
  ]
}}

Only flag CLEAR contradictions (e.g., different locations, dates, numbers)."""


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class InterceptResult:
    """Result when input should be intercepted."""
    should_intercept: bool = False
    reason: str = ""
    clarification_prompt: str = ""
    options: List[str] = field(default_factory=list)
    confidence: str = "low"  # low, medium, high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_intercept": self.should_intercept,
            "reason": self.reason,
            "clarification_prompt": self.clarification_prompt,
            "options": self.options,
        }


@dataclass
class ValidationResult:
    """Result of response validation."""
    is_valid: bool = True
    issues: List[str] = field(default_factory=list)
    is_duplicate: bool = False
    similarity_score: float = 0.0
    contradictions: List[Dict[str, str]] = field(default_factory=list)
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "issues": self.issues,
            "is_duplicate": self.is_duplicate,
            "similarity_score": self.similarity_score,
            "contradictions": self.contradictions,
        }


# =============================================================================
# Smart Guard
# =============================================================================

class SmartGuard:
    """
    Context-aware conversation guard.

    Uses conversation context (action tags + referent stack) for continuation
    detection instead of LLM calls. Only intercepts when a required slot is
    genuinely missing.

    Architecture:
        - tag_response(): Call after AI responds to track action type
        - check_input(): Call before AI to detect if interception needed
        - validate_response(): Call after AI to check for duplicates
    """

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id
        self._api_key = None
        self._api_url = None
        self._model = "llama-3.1-8b-instant"
        self._timeout = 3  # seconds
        self._load_config()

        # Initialize conversation context
        if HAS_CONTEXT_MODULE:
            self._context = get_conversation_context(session_id)
        else:
            self._context = None

    def _load_config(self):
        """Load API configuration."""
        try:
            from .config import Config
            self._api_key = Config.GROQ_API_KEY
            self._api_url = Config.API_URL
            logger.debug(f"SmartGuard config loaded: API_KEY={'set' if self._api_key else 'NOT SET'}")
        except Exception as e:
            logger.warning(f"SmartGuard failed to load config: {e}")

    def _call_llm(self, prompt: str, max_tokens: int = 200) -> Optional[str]:
        """Make fast LLM call with timeout."""
        if not self._api_key or not self._api_url:
            return None

        try:
            response = requests.post(
                self._api_url,
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "temperature": 0.1
                },
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json"
                },
                timeout=self._timeout
            )

            if response.status_code != 200:
                logger.debug(f"LLM call failed: {response.status_code}")
                return None

            content = response.json()["choices"][0]["message"]["content"].strip()
            return content

        except requests.Timeout:
            logger.debug("LLM call timed out")
            return None
        except Exception as e:
            logger.debug(f"LLM call error: {e}")
            return None

    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """Extract JSON from LLM response."""
        if not response:
            return None

        try:
            # Try direct parse
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in response
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass

        return None

    # =========================================================================
    # Response Tagging (After LLM response)
    # =========================================================================

    def tag_response(self, response: str, user_prompt: str = ""):
        """
        Tag the AI's response to track action type and referents.
        Call this after AI responds to enable continuation detection.

        Args:
            response: The AI's response text
            user_prompt: The user's original prompt
        """
        if self._context and HAS_CONTEXT_MODULE:
            self._context.tag_response(response, user_prompt)
            logger.debug(f"SmartGuard: Tagged response as {self._context.get_last_action_type()}")

    # =========================================================================
    # Input Checking (Before LLM)
    # =========================================================================

    def check_input(
        self,
        user_input: str,
        previous_messages: List[Dict[str, Any]]
    ) -> InterceptResult:
        """
        Check if user input needs interception.

        NEW APPROACH - Only intercept when:
        1. Input requires a slot/referent that's missing
        2. Input is truly vague with no context to continue from

        DOES NOT intercept:
        - Continuations like "other one", "again", "next" (context exists)
        - Short inputs that clearly reference previous conversation
        """
        user_input = user_input.strip()

        if not user_input:
            return InterceptResult()

        # =================================================================
        # STEP 1: Context-based continuation detection (fast, no LLM)
        # =================================================================
        if self._context and HAS_CONTEXT_MODULE:
            # Check if this is a continuation - if so, let it flow through
            if self._context.is_continuation(user_input):
                logger.debug(f"SmartGuard: '{user_input}' is continuation of {self._context.get_last_action_type()}")
                return InterceptResult()  # Don't intercept - main LLM will understand

            # Check if a required slot is missing.
            # But DON'T intercept if the LLM can fill in sensible defaults.
            # Only intercept for truly critical missing info (e.g., recipient
            # for a send action, target for a delete action).
            missing_slot = self._context.get_missing_slot(user_input)
            if missing_slot:
                # Let the LLM handle it — it can use memory, context, or
                # sensible defaults. Only intercept for irreversible actions
                # where guessing wrong would be harmful.
                last_action = self._context.get_last_action_type() if self._context else ""
                irreversible_actions = {"send", "delete", "publish", "submit", "transfer"}
                if last_action in irreversible_actions:
                    logger.info(f"SmartGuard: Missing slot '{missing_slot}' for irreversible action '{last_action}'")
                    return InterceptResult(
                        should_intercept=True,
                        reason="missing_slot_irreversible",
                        clarification_prompt=f"Before I {last_action}, I need to confirm: what {missing_slot}?",
                        confidence="high"
                    )
                else:
                    logger.debug(f"SmartGuard: Missing slot '{missing_slot}' but action is reversible, letting LLM handle")

        # =================================================================
        # STEP 2: Quick structural checks (no LLM needed)
        # =================================================================

        # If input has 4+ words, it's specific enough - don't intercept
        word_count = len(user_input.split())
        if word_count >= 4:
            logger.debug(f"SmartGuard: Input has {word_count} words, not intercepting")
            return InterceptResult()

        # If input contains question words + something, it's clear
        input_lower = user_input.lower()
        if any(q in input_lower for q in ['what ', 'how ', 'why ', 'where ', 'when ', 'which ']):
            return InterceptResult()

        # =================================================================
        # STEP 3: Check for truly vague inputs (only when no context)
        # =================================================================

        # If we have previous messages, build context for fallback checks
        if not previous_messages:
            return InterceptResult()

        # Get last response for fallback regex check
        last_response = ""
        for msg in reversed(previous_messages[-3:]):
            if isinstance(msg, dict):
                response_text = msg.get("response") or ""
                if not response_text:
                    sender = msg.get('sender', '').lower()
                    if sender not in ('user', 'human'):
                        response_text = msg.get("content") or ""
                if response_text:
                    last_response = response_text
                    break

        if not last_response:
            return InterceptResult()

        # Regex fallback - only for TRULY vague inputs like bare "yes", "ok"
        regex_result = self._regex_ambiguity_check(user_input, last_response)
        if regex_result.should_intercept:
            logger.info(f"SmartGuard regex intercept: {regex_result.reason}")
            return regex_result

        logger.debug("SmartGuard: No interception needed")
        return InterceptResult()

    def _llm_ambiguity_check(
        self,
        user_input: str,
        last_response: str,
        recent_context: list = None
    ) -> Optional[InterceptResult]:
        """Use LLM to detect ambiguity."""
        # Format recent context as conversation history
        context_str = ""
        if recent_context:
            for entry in recent_context:
                if entry.get("user"):
                    context_str += f"User: {entry['user']}\n"
                if entry.get("ai"):
                    context_str += f"AI: {entry['ai'][:150]}...\n"

        prompt = AMBIGUITY_CHECK_PROMPT.format(
            conversation_context=context_str if context_str else "(no prior context)",
            user_input=user_input
        )

        response = self._call_llm(prompt)
        parsed = self._parse_json_response(response)

        if not parsed:
            return None

        if parsed.get("is_ambiguous"):
            options = parsed.get("options_from_ai", [])
            clarification = parsed.get("clarification_prompt", "")

            # Build clarification with options if available
            if options and len(options) > 1:
                options_text = "\n".join(f"- {opt}" for opt in options[:6])
                if not clarification:
                    clarification = f"I offered a few options. Which would you like?\n{options_text}"
                elif options_text not in clarification:
                    clarification = f"{clarification}\n{options_text}"

            return InterceptResult(
                should_intercept=True,
                reason=parsed.get("reason", "ambiguous"),
                clarification_prompt=clarification or "Could you be more specific about what you'd like?",
                options=options,
                confidence="high"
            )

        return InterceptResult(should_intercept=False, confidence="high")

    def _regex_ambiguity_check(
        self,
        user_input: str,
        last_response: str
    ) -> InterceptResult:
        """Simple regex fallback for obvious cases."""
        input_lower = user_input.lower().strip()

        # ACTION CONFIRMATIONS — these mean "do it", never intercept them.
        # The user is telling us to proceed, not asking a vague question.
        action_confirmations = [
            r"^yes\s*(?:please)?[.!]?$",
            r"^yeah?[.!]?$",
            r"^sure[.!]?$",
            r"^ok(?:ay)?[.!]?$",
            r"^go\s*(?:ahead|on)[.!]?$",
            r"^do\s*it[.!]?$",
            r"^please[.!]?$",
            r"^continue[.!]?$",
            r"^proceed[.!]?$",
            r"^definitely[.!]?$",
            r"^absolutely[.!]?$",
            r"^just\s*do\s*it[.!]?$",
            r"^skip[.!]?$",
        ]

        for pattern in action_confirmations:
            if re.match(pattern, input_lower):
                # User wants us to ACT. Never intercept action confirmations.
                return InterceptResult(should_intercept=False, confidence="high")

        # Only intercept truly vague single-word inputs that could mean
        # multiple things AND the last response had a list with options.
        vague_expansion_patterns = [
            r"^more[.!]?$",
            r"^elaborate[.!]?$",
        ]

        is_vague_expansion = False
        for pattern in vague_expansion_patterns:
            if re.match(pattern, input_lower):
                is_vague_expansion = True
                break

        if is_vague_expansion:
            # Only intercept if the last response had 3+ distinct items
            # where "more" is genuinely ambiguous about which item.
            items = self._extract_list_items(last_response)

            if len(items) >= 3:
                options_text = "\n".join(f"- {item}" for item in items[:6])
                return InterceptResult(
                    should_intercept=True,
                    reason="vague_expansion_multiple_options",
                    clarification_prompt=f"Which would you like me to expand on?\n{options_text}",
                    options=items[:6],
                    confidence="medium"
                )
            # If fewer than 3 items, the intent is clear enough — don't intercept.

        return InterceptResult(should_intercept=False, confidence="low")

    def _has_multiple_topics(self, text: str) -> bool:
        """Check if text discusses multiple distinct topics."""
        # Count headers, bold items, or topic indicators
        headers = len(re.findall(r'^[A-Z][^.!?\n]{5,50}:', text, re.MULTILINE))
        bold_items = len(re.findall(r'\*\*[^*]+\*\*', text))
        dashes = len(re.findall(r'^\s*[-–—]\s+[A-Z]', text, re.MULTILINE))
        numbered = len(re.findall(r'^\s*\d+[.)]\s+', text, re.MULTILINE))

        total = headers + bold_items + dashes + numbered
        return total >= 2

    def _extract_list_items(self, text: str) -> List[str]:
        """Extract list items from text."""
        items = []

        # Numbered items: "1. Item" or "1) Item"
        numbered = re.findall(r'^\s*\d+[.)]\s+(.+?)$', text, re.MULTILINE)
        items.extend([n.split('–')[0].split('-')[0].strip()[:40] for n in numbered])

        # Bullet items
        bullets = re.findall(r'^\s*[-•*]\s+(.+?)$', text, re.MULTILINE)
        items.extend([b.split('–')[0].split('-')[0].strip()[:40] for b in bullets])

        # Bold items with description: "**Name** - Description" or "** Name **"
        bold = re.findall(r'\*\*\s*([^*]+?)\s*\*\*', text)
        items.extend([b.strip()[:40] for b in bold if len(b.strip()) > 2])

        # Capitalized items with dash: "Name – Description" (common in church lists)
        # Match: "Ephesus – Known for..." or "Philadelphia – Praised for..."
        caps = re.findall(r'(?:^|\n)\s*([A-Z][a-zA-Z]{2,20})\s*[-–—]\s*', text)
        items.extend(caps)

        # Also try to find capitalized words before descriptions
        # Pattern: Word followed by dash and description
        name_desc = re.findall(r'\b([A-Z][a-z]{3,15})\s*[-–—]\s*[A-Z][a-z]', text)
        items.extend(name_desc)

        # Deduplicate
        seen = set()
        unique = []
        for item in items:
            item = item.strip()[:40]
            if item.lower() not in seen and len(item) > 2:
                seen.add(item.lower())
                unique.append(item)
        return unique[:10]

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
        Validate AI response using embeddings + LLM.
        """
        result = ValidationResult()

        if not response or not previous_messages:
            return result

        # Get previous responses
        prev_responses = []
        for msg in previous_messages[-5:]:
            prev_resp = msg.get("response", "")
            if prev_resp:
                prev_responses.append(prev_resp)

        if not prev_responses:
            return result

        # Check for duplicates using embeddings
        duplicate_result = self._check_duplicate_embedding(response, prev_responses)
        if duplicate_result:
            result.is_duplicate = duplicate_result["is_duplicate"]
            result.similarity_score = duplicate_result.get("similarity", 0)
            if result.is_duplicate:
                result.is_valid = False
                result.issues.append(
                    f"Response is {result.similarity_score:.0%} similar to previous"
                )
                result.suggestion = "provide_new_info"

        # Check for contradictions (optional, more expensive)
        # Only run if we have facts to compare
        # contradiction_result = self._check_contradictions(response, prev_responses)

        return result

    def _check_duplicate_embedding(
        self,
        new_response: str,
        prev_responses: List[str],
        threshold: float = 0.80
    ) -> Optional[Dict]:
        """Check for duplicates using embeddings."""
        try:
            from .hybrid_retrieval import get_embedding, cosine_similarity
        except ImportError:
            # Fallback to text similarity
            return self._check_duplicate_text(new_response, prev_responses, threshold)

        try:
            new_embedding = get_embedding(new_response[:500])

            for prev in prev_responses:
                prev_embedding = get_embedding(prev[:500])
                similarity = cosine_similarity(new_embedding, prev_embedding)

                if similarity > threshold:
                    return {
                        "is_duplicate": True,
                        "similarity": similarity,
                        "reason": "semantic_similarity"
                    }

            return {"is_duplicate": False, "similarity": 0}

        except Exception as e:
            logger.debug(f"Embedding check failed: {e}")
            return self._check_duplicate_text(new_response, prev_responses, threshold)

    def _check_duplicate_text(
        self,
        new_response: str,
        prev_responses: List[str],
        threshold: float = 0.75
    ) -> Dict:
        """Fallback: text-based duplicate check."""
        from difflib import SequenceMatcher

        def normalize(text: str) -> str:
            text = text.lower()
            text = re.sub(r'\s+', ' ', text)
            text = re.sub(r'[^\w\s]', '', text)
            return text[:500].strip()

        new_norm = normalize(new_response)

        for prev in prev_responses:
            prev_norm = normalize(prev)
            similarity = SequenceMatcher(None, new_norm, prev_norm).ratio()

            if similarity > threshold:
                return {
                    "is_duplicate": True,
                    "similarity": similarity,
                    "reason": "text_similarity"
                }

        return {"is_duplicate": False, "similarity": 0}

    def _check_contradictions(
        self,
        new_response: str,
        prev_responses: List[str]
    ) -> Optional[List[Dict]]:
        """Use LLM to check for fact contradictions."""
        # Extract key facts from previous responses
        prev_facts = "\n".join(prev_responses[-3:])[:1000]

        prompt = FACT_CHECK_PROMPT.format(
            previous_facts=prev_facts,
            new_response=new_response[:600]
        )

        response = self._call_llm(prompt, max_tokens=150)
        parsed = self._parse_json_response(response)

        if parsed and parsed.get("has_contradiction"):
            return parsed.get("contradictions", [])

        return None


# =============================================================================
# Convenience Functions
# =============================================================================

_guard_cache: Dict[str, SmartGuard] = {}


def get_smart_guard(user_id: str, session_id: str) -> SmartGuard:
    """Get or create a smart guard for a session."""
    cache_key = f"{user_id}:{session_id}"
    if cache_key not in _guard_cache:
        _guard_cache[cache_key] = SmartGuard(user_id, session_id)
    return _guard_cache[cache_key]


def smart_check_input(
    user_id: str,
    session_id: str,
    user_input: str,
    previous_messages: List[Dict[str, Any]]
) -> InterceptResult:
    """
    Context-aware input check.

    Uses action tagging + referent stack to detect continuations.
    Only intercepts when a required slot is genuinely missing.
    """
    guard = get_smart_guard(user_id, session_id)
    return guard.check_input(user_input, previous_messages)


def smart_tag_response(
    user_id: str,
    session_id: str,
    response: str,
    user_prompt: str = ""
):
    """
    Tag an AI response to enable continuation detection.

    Call this after the AI responds so that follow-up inputs like
    "other one", "again", "next" are understood correctly.

    Args:
        user_id: The user ID
        session_id: The session ID
        response: The AI's response text
        user_prompt: The user's original prompt
    """
    guard = get_smart_guard(user_id, session_id)
    guard.tag_response(response, user_prompt)


def smart_validate_response(
    user_id: str,
    session_id: str,
    response: str,
    previous_messages: List[Dict[str, Any]]
) -> ValidationResult:
    """
    Smart response validation using embeddings.
    """
    guard = get_smart_guard(user_id, session_id)
    return guard.validate_response(response, previous_messages)

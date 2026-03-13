"""
Memory V2 Extractor
====================
Extracts memory candidates from USER text only.

Key principles:
- Never extract from assistant responses (prevents poisoning)
- Look for explicit statements, not inferences
- Generate candidates with confidence scores
- Let judge decide what actually gets stored

Upgrades:
- Opt-out detection ("don't remember this")
- Key normalization for deduplication
- Value canonicalization for consistency
- Project identity with stable IDs
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .schema import MemoryType, create_memory, MemoryItem
from .normalize import (
    normalize_key,
    canonicalize_value,
    contains_opt_out,
    generate_project_id,
    extract_project_identity,
)

logger = logging.getLogger(__name__)


@dataclass
class MemoryCandidate:
    """A potential memory extracted from user text."""
    type: MemoryType
    key: str                    # Normalized canonical key
    value: Any                  # Canonicalized value
    confidence: float
    quote: str
    extraction_method: str      # "pattern" | "llm"
    topics: List[str]
    original_key: str = ""      # Original key before normalization
    original_value: Any = ""    # Original value before canonicalization
    project_id: str = ""        # Stable project ID for project type


class MemoryExtractor:
    """
    Extracts memory candidates from user messages.

    Uses hybrid approach:
    1. Pattern-based extraction (fast, high precision)
    2. LLM extraction (slower, better recall)
    """

    # Pattern-based extraction rules
    # Note: All patterns use re.IGNORECASE
    EXTRACTION_PATTERNS = {
        MemoryType.PROFILE: [
            # Name patterns - handles "my name is X", "i'm called X", "call me X"
            # Uses negative lookahead to prevent capturing common words like "and", "from"
            (r"(?:my name is|i'?m called|call me)\s+([A-Za-z]{2,}(?:\s+(?!and\b|from\b|in\b|but\b|i\b|the\b)[A-Za-z]{2,})?)",
             "name", 0.95),
            # Location patterns - handles "i'm from X", "im from X", "from X" (when preceded by profile info)
            (r"(?:i'?m |i am )?(?:from|based in|living in|in)\s+([A-Za-z\s,]+?)(?:\s+and|\s*$|\.)",
             "location", 0.85),
            # Timezone patterns
            (r"(?:my timezone is|i'?m in|i use)\s+((?:UTC|GMT|EST|PST|CST|MST|[A-Z]{2,4})[+-]?\d*)",
             "timezone", 0.90),
            # Role/Job patterns - handles "i'm a X", "im a X", "i am a X", "my background: X"
            (r"(?:i'?m a|i am a|i work as a?|my job is)\s+([a-zA-Z\s]+(?:developer|engineer|designer|manager|analyst|scientist|writer|teacher|student|programmer))",
             "role", 0.85),
            # Background patterns - handles "my background: X", "my background is X"
            (r"(?:my background(?:\s+is)?[:\s]+)(.+?)(?:\.|$)",
             "background", 0.85),
            # Transitioning/career patterns
            (r"(?:transitioning (?:into|to|from)|moving (?:into|to))\s+([a-zA-Z\s]+?)(?:\.|,|$)",
             "career_transition", 0.80),
            # Language patterns
            (r"(?:i speak|my (?:native |first )?language is)\s+([A-Za-z]+)",
             "language", 0.90),
        ],
        MemoryType.PREFERENCE: [
            # Format preferences
            (r"(?:i (?:prefer|like|want)|please use)\s+([a-zA-Z\s]+(?:format|style|syntax))",
             "format_preference", 0.80),
            # Style preferences
            (r"(?:i (?:prefer|like))\s+(concise|detailed|brief|verbose|formal|casual)\s+(?:responses?|answers?|explanations?)",
             "response_style", 0.85),
            # Write in X / Use X patterns
            (r"(?:write in|use|respond in)\s+(UK English|US English|British English|American English|[A-Za-z]+\s+English)",
             "writing_language", 0.90),
            # Keep responses X patterns
            (r"(?:keep (?:responses?|replies?|answers?))\s+(short|brief|concise|detailed|long|verbose)",
             "response_length", 0.85),
            # Bullet point / format preferences
            (r"(?:(?:use|in|with)\s+)?(bullet\s*points?|numbered\s+lists?|markdown|plain\s+text)",
             "response_format", 0.80),
            # Code preferences
            (r"(?:i (?:prefer|use|like))\s+([A-Za-z#+]+)\s+(?:for|when|over)",
             "language_preference", 0.80),
            # General preferences
            (r"(?:always|please always)\s+(.+?)(?:\.|$)",
             "preference", 0.75),
        ],
        MemoryType.PROJECT: [
            # Working on patterns
            (r"(?:i(?:'m| am) (?:working on|building|developing|creating))\s+(?:a\s+)?([a-zA-Z0-9\s-]+(?:app|project|website|api|service|tool|platform))",
             "current_project", 0.85),
            # Tech stack patterns
            (r"(?:i(?:'m| am)? using|my (?:stack|tech|framework) is)\s+([A-Za-z0-9,\s]+)",
             "tech_stack", 0.80),
            # Codebase patterns
            (r"(?:my (?:codebase|project|app) (?:uses|is in|is written in))\s+([A-Za-z0-9,\s]+)",
             "project_tech", 0.80),
        ],
        MemoryType.CONSTRAINT: [
            # Don't/Never patterns - extract what to avoid, key becomes no_[thing]
            # These are processed specially to create dynamic keys
            (r"(?:don't|do not|never|avoid)\s+(?:use\s+)?([a-zA-Z0-9!@#$%^&*_+-]+)(?:\s+in\s+|\s+for\s+|\s+with\s+)?([a-zA-Z]*)",
             "no_{0}", 0.90),  # {0} = captured thing, value = context
            # Allergy/restriction patterns
            (r"(?:i(?:'m| am) allergic to|i can't (?:eat|have|use))\s+([a-zA-Z\s]+)",
             "allergy", 0.95),
            # Must not patterns
            (r"(?:must not|should never|prohibited)\s+(.+?)(?:\.|$)",
             "no_{0}", 0.90),
        ],
    }

    # Topics to extract from context
    TOPIC_KEYWORDS = {
        'programming': ['code', 'programming', 'developer', 'software', 'api', 'function'],
        'work': ['work', 'job', 'office', 'meeting', 'project', 'deadline'],
        'personal': ['home', 'family', 'personal', 'hobby', 'weekend'],
        'health': ['health', 'exercise', 'diet', 'sleep', 'medical'],
        'finance': ['money', 'budget', 'invest', 'savings', 'expense'],
        'learning': ['learn', 'study', 'course', 'tutorial', 'practice'],
    }

    def __init__(self, use_llm: bool = True, llm_client=None):
        """
        Initialize extractor.

        Args:
            use_llm: Whether to use LLM for extraction
            llm_client: Optional LLM client for extraction
        """
        self.use_llm = use_llm
        self.llm_client = llm_client

    def extract(self, user_message: str, turn_id: str = "") -> List[MemoryCandidate]:
        """
        Extract memory candidates from a user message.

        Args:
            user_message: The user's message text
            turn_id: Conversation turn ID for attribution

        Returns:
            List of MemoryCandidate
        """
        logger.info(f"🔍 MemoryExtractor.extract() called with message: '{user_message}'")

        # UPGRADE: Check for opt-out phrases first
        if contains_opt_out(user_message):
            logger.info("User requested no memory storage for this message")
            return []

        candidates = []

        # Check if this is an explicit memory request (boost confidence later)
        explicit_parser = ExplicitMemoryParser()
        is_explicit = explicit_parser.is_explicit_memory(user_message)
        if is_explicit:
            logger.info("📌 Explicit memory request detected — will boost confidence")

        # SMART EXTRACTION: Use LLM first (language-agnostic, handles any phrasing)
        if self.use_llm and self.llm_client:
            llm_candidates = self._extract_llm(user_message)
            candidates.extend(llm_candidates)
            logger.info(f"🧠 LLM extraction found {len(llm_candidates)} candidates")

        # FALLBACK: Pattern-based extraction only if LLM found nothing
        if not candidates:
            logger.info("🔍 LLM found nothing, falling back to patterns")
            pattern_candidates = self._extract_patterns(user_message)
            candidates.extend(pattern_candidates)

        # Deduplicate by normalized key
        candidates = self._deduplicate(candidates)

        # Boost confidence for explicit memory requests
        if is_explicit:
            for candidate in candidates:
                candidate.confidence = min(1.0, candidate.confidence + 0.10)

        # Extract topics
        for candidate in candidates:
            candidate.topics = self._extract_topics(user_message)

        logger.debug(f"Extracted {len(candidates)} memory candidates from message")
        return candidates

    def _extract_patterns(self, text: str) -> List[MemoryCandidate]:
        """Extract candidates using regex patterns with normalization."""
        candidates = []
        logger.info(f"🔍 Pattern extraction on text: '{text}'")

        for memory_type, patterns in self.EXTRACTION_PATTERNS.items():
            for pattern, key_template, confidence in patterns:
                matches = list(re.finditer(pattern, text, re.IGNORECASE))
                if matches:
                    logger.info(f"🔍 Pattern '{key_template}' matched: {[m.group(0) for m in matches]}")
                for match in matches:
                    value = match.group(1).strip()
                    if len(value) < 2 or len(value) > 200:
                        continue

                    # Handle dynamic keys like "no_{0}" where {0} is the captured thing
                    if "{0}" in key_template:
                        # Extract the thing to avoid, clean it for key use
                        thing = value.lower().replace(" ", "_").replace("!", "").replace("@", "")
                        key = key_template.replace("{0}", thing)
                        # Value becomes the context (group 2 if exists, or generic)
                        try:
                            context = match.group(2).strip() if match.lastindex >= 2 and match.group(2) else "general"
                        except:
                            context = "general"
                        value = context if context else value
                    else:
                        key = key_template

                    # UPGRADE: Normalize key for deduplication
                    canonical_key, original_key = normalize_key(key)

                    # UPGRADE: Canonicalize value for consistency
                    canonical_value = canonicalize_value(canonical_key, value)

                    # UPGRADE: Generate project ID for project type
                    project_id = ""
                    if memory_type == MemoryType.PROJECT:
                        project_info = extract_project_identity(value)
                        project_id = generate_project_id(
                            project_info["title"],
                            project_info["repo"],
                            project_info["stack"]
                        )

                    candidates.append(MemoryCandidate(
                        type=memory_type,
                        key=canonical_key,
                        value=canonical_value,
                        confidence=confidence,
                        quote=match.group(0),
                        extraction_method="pattern",
                        topics=[],
                        original_key=original_key,
                        original_value=value,
                        project_id=project_id
                    ))

        return candidates

    def _extract_llm(self, text: str) -> List[MemoryCandidate]:
        """
        Extract candidates using LLM.

        This is more flexible but slower than pattern matching.
        """
        if not self.llm_client:
            return []

        try:
            prompt = f"""Extract any personal information, preferences, or facts the user is stating about themselves from this message.

User message: "{text}"

For each fact found, provide:
- type: one of [profile, preference, project, constraint]
- key: a short label (e.g., "name", "timezone", "coding_style")
- value: the extracted information
- confidence: 0.0 to 1.0 (how certain are you?)
- quote: the exact text this came from

Return as JSON array. If nothing found, return [].
Only extract EXPLICIT statements, not inferences."""

            # Call LLM (implementation depends on client)
            response = self.llm_client.extract_memory(prompt)

            candidates = []
            for item in response:
                try:
                    candidates.append(MemoryCandidate(
                        type=MemoryType(item.get("type", "profile")),
                        key=item.get("key", "unknown"),
                        value=item.get("value", ""),
                        confidence=float(item.get("confidence", 0.6)),
                        quote=item.get("quote", ""),
                        extraction_method="llm",
                        topics=[]
                    ))
                except (ValueError, KeyError) as e:
                    logger.debug(f"Invalid LLM extraction item: {e}")

            return candidates

        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            return []

    def _extract_topics(self, text: str) -> List[str]:
        """Extract topic tags from text."""
        text_lower = text.lower()
        topics = []

        for topic, keywords in self.TOPIC_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    if topic not in topics:
                        topics.append(topic)
                    break

        return topics

    def _deduplicate(self, candidates: List[MemoryCandidate]) -> List[MemoryCandidate]:
        """Remove duplicate candidates, keeping highest confidence."""
        seen = {}
        for candidate in candidates:
            key = f"{candidate.type.value}:{candidate.key}"
            if key not in seen or candidate.confidence > seen[key].confidence:
                seen[key] = candidate
        return list(seen.values())

    def candidates_to_memories(
        self,
        candidates: List[MemoryCandidate],
        user_id: str,
        provisional: bool = True
    ) -> List[MemoryItem]:
        """
        Convert candidates to MemoryItem objects.

        Args:
            candidates: Extracted candidates
            user_id: User's ID
            provisional: If True, mark as provisional (needs confirmation)

        Returns:
            List of MemoryItem ready for storage
        """
        memories = []

        for candidate in candidates:
            memory = create_memory(
                user_id=user_id,
                memory_type=candidate.type,
                key=candidate.key,
                value=candidate.value,
                confidence=candidate.confidence,
                quote=candidate.quote,
                topics=candidate.topics,
                provisional=provisional
            )
            memories.append(memory)

        return memories


class ExplicitMemoryParser:
    """
    Parses explicit memory commands from user.

    Handles commands like:
    - "Remember that I prefer Python"
    - "Note: my timezone is PST"
    - "Keep in mind I'm allergic to nuts"
    """

    EXPLICIT_PATTERNS = [
        r"^(?:from now on\s+)?(?:remember|note|keep in mind|don't forget)\s+(?:these\s+)?(?:preferences?\s+)?(?:about\s+me\s*)?[:\s]+(.+)$",
        r"^(?:remember|note|keep in mind|don't forget)[:\s]+(.+)$",
        r"^(?:save|store)\s+(?:that\s+)?(.+)$",
        r"^(?:fyi|btw|for your (?:information|reference))[:\s]+(.+)$",
        r"^from now on[,:\s]+(.+)$",
    ]

    def parse(self, text: str) -> Optional[str]:
        """
        Check if message is an explicit memory command.

        Returns:
            The content to remember, or None if not a memory command
        """
        text_stripped = text.strip()

        for pattern in self.EXPLICIT_PATTERNS:
            match = re.match(pattern, text_stripped, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        return None

    def is_explicit_memory(self, text: str) -> bool:
        """Check if text is an explicit memory command."""
        return self.parse(text) is not None

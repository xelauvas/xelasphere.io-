"""
Memory V2 LLM Extractor
========================
Smart, language-agnostic memory extraction using LLM.

Uses the shared metadata model stack (Gemini primary, Groq 70B fallback)
with JSON mode for schema-compliant output. Replaces the previous
qwen3-32b direct-Groq + Cerebras fallback wiring.

No hard-coded patterns - the LLM decides what's worth remembering.
"""

import json
import logging
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from ..utilities import send_metadata_prompt

logger = logging.getLogger(__name__)

# System prompt for memory extraction - keep it concise for better results
EXTRACTION_SYSTEM_PROMPT = """You are a memory extraction assistant. Extract ONLY durable personal facts about the USER. Be extremely conservative — when in doubt, extract NOTHING.

Categories:
- profile: name, location, job/role, timezone, language
- preference: coding style, tools, UI preferences, communication style (POSITIVE choices)
- project: what they're building, tech stack
- constraint: things to AVOID or NEVER do

Return JSON: {"memories": [{"type": "...", "key": "...", "value": "...", "confidence": 0.9, "quote": "..."}]}
If nothing to remember: {"memories": []}

HARD REJECTION RULES (return empty memories if ANY apply):

1. TASK INSTRUCTIONS ARE NOT PREFERENCES. If the user is giving a one-shot instruction for THIS response ("Show X", "Make Y", "Write Z", "Give me A", "Explain B", "Build C", "Fix D"), extract NOTHING. An instruction without a durability signal is a task, not a preference.

2. DURABILITY SIGNAL REQUIRED for preference/constraint. Only extract a preference or constraint when the user explicitly marks it as persistent. Required signals:
   - "I prefer", "I like", "I hate", "I always", "I never", "I usually"
   - "from now on", "going forward", "always", "never"
   - "remember", "keep in mind", "note that", "don't forget"
   - "my preferred", "my style is", "I want you to [always/keep]"
   WITHOUT one of these signals, a one-line directive is a task, NOT a preference.

3. TEST / DEMO / EXAMPLE CONTENT. If the message looks like a test prompt, demo request, or example ("show real checkboxes", "display this", "render that as", "format X as Y"), extract NOTHING. These are testing the UI, not user preferences.

4. QUESTIONS. "what should I...", "how do I...", "can you..." → NOTHING.

5. NEGATIONS. "not X", "I'm not X" → extract only the positive claim if paired ("I am X not Y" → X only). A lone negation is NOTHING.

6. OPT-OUT. "dont remember", "off the record", "forget this", "between us", "private" → NOTHING.

7. CODE / TECHNICAL EXPLANATIONS. If the message is explaining code, debugging, or describing how something works → NOTHING. The user asking about --verbose is not a preference for --verbose.

CONSTRAINT KEY FORMAT: Use "no_[thing]" as key, context as value. Unique keys per constraint.
   - "never use var" → key=no_var, value=javascript
   - "avoid any" → key=no_any, value=typescript

WRITING/RESPONSE preferences (REQUIRE durability signal):
   - "always write in UK English" → preference: writing_language=UK English
   - "from now on keep responses short" → preference: response_length=short
   WITHOUT "always"/"from now on", treat as a one-off task, NOT a preference.

BACKGROUND/BIO: "My background: X", "I'm transitioning into Y" → profile entries (these are statements about the user, OK).

Examples — EXTRACT:
- "im alex from berlin" → profile: name=Alex, location=Berlin
- "im a backend developer" → profile: role=backend developer
- "je m'appelle marie" → profile: name=Marie
- "i always use tabs" → preference: indentation=tabs
- "never use var in javascript" → constraint: no_var=javascript
- "i prefer typescript over javascript" → preference: language_preference=TypeScript
- "from now on write in UK English" → preference: writing_language=UK English
- "my background: hotel GM transitioning into software" → profile: background=hotel GM transitioning into software
- "im building a saas with flask" → project: current_project=SaaS with Flask
- "i am xela not ada" → profile: name=Xela

Examples — EXTRACT NOTHING (task instructions, tests, questions):
- "Show real checkboxes" → NOTHING (one-off task)
- "Show --verbose and --dry-run as literal dashes, not em-dashes" → NOTHING (UI test instruction, not a preference)
- "Give me a todo list with 3 unchecked items" → NOTHING (task)
- "Explain the --verbose flag" → NOTHING (question/task)
- "Write me a React app" → NOTHING (task)
- "Fix this bug" → NOTHING (task)
- "Make it blue" → NOTHING (one-off task)
- "Use bullet points" → NOTHING (one-off; no durability signal)
- "Avoid inline styles" → NOTHING (one-off; no durability signal — BUT "I always avoid inline styles" → constraint)
- "what's the weather" → NOTHING (question)
- "can you recommend a framework?" → NOTHING (question)
- "i'm not a designer" → NOTHING (lone negation)
- "dont remember this but my salary is 100k" → NOTHING (opt-out)
- "help me debug this" → NOTHING (task)"""


@dataclass
class ExtractedMemory:
    """A memory extracted by the LLM."""
    type: str
    key: str
    value: str
    confidence: float
    quote: str


class LLMMemoryExtractor:
    """
    Intelligent memory extraction using LLM.

    Replaces hard-coded regex patterns with smart extraction
    that works in any language and handles diverse phrasings.
    """

    def __init__(self, api_key: Optional[str] = None, cerebras_api_key: Optional[str] = None):
        """
        Initialize the LLM extractor.

        api_key / cerebras_api_key are accepted for backward compatibility
        but no longer used — extraction now routes through the shared
        metadata model stack (Config.METADATA_MODEL_PRIMARY / _FALLBACK),
        which resolves its own credentials from Config.
        """
        # Preserved on the instance only so adapters inspecting attrs don't
        # break; the values are not read during extraction anymore.
        self.api_key = api_key or os.environ.get('GROQ_API_KEY')
        self.cerebras_api_key = cerebras_api_key or os.environ.get('CEREBRAS_API_KEY')

    def extract(self, user_message: str) -> List[ExtractedMemory]:
        """
        Extract memorable facts from a user message.

        Args:
            user_message: The user's message text

        Returns:
            List of ExtractedMemory objects
        """
        if not user_message or len(user_message.strip()) < 3:
            return []

        # Skip very long messages (likely code or documents)
        if len(user_message) > 2000:
            logger.debug("LLM extraction skipped - message too long")
            return []

        try:
            response = self._call_llm(user_message)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"LLM memory extraction failed: {e}")
            return []

    def _call_llm(self, user_message: str) -> str:
        """
        Extract via the metadata model stack. Gemini primary returns clean
        JSON under json_mode; Groq 70B fallback likewise. Helper handles
        all provider errors transparently — we just care about the string.
        """
        result = send_metadata_prompt(
            f"Extract facts from: {user_message}",
            system=EXTRACTION_SYSTEM_PROMPT,
            max_tokens=500,
            temperature=0.1,
            json_mode=True,
            timeout=15,
        )
        if not result:
            logger.warning("Memory extraction: both metadata providers failed")
            return '{"memories": []}'
        return result

    def _parse_response(self, response_text: str) -> List[ExtractedMemory]:
        """Parse LLM response into ExtractedMemory objects."""
        try:
            # Strip thinking tags (qwen3 uses <think>...</think>)
            import re
            cleaned = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL).strip()

            # Extract JSON from response (may be wrapped in markdown code blocks)
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', cleaned)
            if json_match:
                cleaned = json_match.group(1).strip()

            # Find JSON object or array in response
            if not cleaned or cleaned[0] not in ('{', '['):
                json_start = re.search(r'[\[{]', cleaned)
                if json_start:
                    cleaned = cleaned[json_start.start():]
                else:
                    logger.debug("No JSON found in LLM response")
                    return []

            data = json.loads(cleaned)

            # Handle both array and object with array field
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                # Try common field names
                items = data.get("memories") or data.get("facts") or data.get("extractions") or data.get("items") or []
            else:
                return []

            memories = []
            for item in items:
                if not isinstance(item, dict):
                    continue

                mem_type = item.get("type", "profile")
                if mem_type not in ["profile", "preference", "project", "constraint"]:
                    mem_type = "profile"

                memories.append(ExtractedMemory(
                    type=mem_type,
                    key=item.get("key", "unknown"),
                    value=item.get("value", ""),
                    confidence=float(item.get("confidence", 0.7)),
                    quote=item.get("quote", "")
                ))

            logger.info(f"🧠 LLM extracted {len(memories)} memories: {[(m.key, m.value) for m in memories]}")
            return memories

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse LLM extraction response: {e} | "
                f"raw[:400]={response_text[:400]!r}"
            )
            return []
        except Exception as e:
            logger.error(f"Error parsing extraction response: {e}")
            return []


# Adapter to work with existing MemoryExtractor interface
class LLMExtractionClient:
    """
    Client adapter for use with MemoryExtractor.

    Implements the extract_memory(prompt) interface expected
    by the existing MemoryExtractor._extract_llm method.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.extractor = LLMMemoryExtractor(api_key)

    def extract_memory(self, prompt: str) -> List[Dict[str, Any]]:
        """
        Extract memories from prompt.

        This method is called by MemoryExtractor._extract_llm.

        Args:
            prompt: The extraction prompt (we ignore this and use our own)

        Returns:
            List of dicts with type, key, value, confidence, quote
        """
        # Extract the user message from the prompt
        # The prompt format is: 'User message: "{text}"'
        import re
        match = re.search(r'User message: "(.+)"', prompt, re.DOTALL)
        if match:
            user_message = match.group(1)
        else:
            user_message = prompt

        memories = self.extractor.extract(user_message)

        return [
            {
                "type": m.type,
                "key": m.key,
                "value": m.value,
                "confidence": m.confidence,
                "quote": m.quote
            }
            for m in memories
        ]

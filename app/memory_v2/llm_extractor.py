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
EXTRACTION_SYSTEM_PROMPT = """You are a memory extraction assistant. Extract personal facts from user messages.

Categories:
- profile: name, location, job/role, timezone, language
- preference: coding style, tools, UI preferences, communication style (POSITIVE choices)
- project: what they're building, tech stack
- constraint: things to AVOID or NEVER do

Return JSON: {"memories": [{"type": "...", "key": "...", "value": "...", "confidence": 0.9, "quote": "..."}]}
If nothing to remember: {"memories": []}

Rules:
1. Only extract EXPLICIT statements, not assumptions
2. Works in ANY language
3. QUESTIONS are NOT facts - skip ALL questions ("what should I...", "how do I...", "can you...")
4. NEGATIONS are NOT facts - if user says "not X", "I'm not X", "not ada price", do NOT extract X as a fact. Only extract what the user IS, not what they deny being
5. CORRECTIONS: "I am X not Y" or "my name is X not Y" → extract ONLY X. The negated part (Y) is what they are rejecting
4. OPT-OUT: If user says "dont remember", "off the record", "forget this", "between us", "private" - return EMPTY {"memories": []}
5. CONSTRAINT KEY FORMAT: Use "no_[thing]" as key, context as value. Each constraint needs a UNIQUE key!
   - "never use var" → key=no_var, value=javascript
   - "avoid any" → key=no_any, value=typescript
   - Different things = different keys (so they don't overwrite each other)

6. EXPLICIT MEMORY REQUESTS: When user says "remember", "from now on", "keep in mind", "note that" — extract ALL facts listed, even if multi-line
7. WRITING/RESPONSE preferences: "Write in UK English" → preference: writing_language=UK English; "Keep responses short" → preference: response_length=short; "Use bullet points" → preference: response_format=bullet points
8. BACKGROUND/BIO: "My background: X" → profile: background=X; "transitioning into Y" → profile: career_transition=Y

Examples:
- "im alex from berlin" → profile: name=Alex, location=Berlin
- "im a backend developer" → profile: role=backend developer
- "je m'appelle marie" → profile: name=Marie (French)
- "never use tabs" → constraint: no_tabs=use spaces instead
- "never use var in javascript" → constraint: no_var=javascript
- "avoid using any in typescript" → constraint: no_any=typescript
- "dont use jQuery" → constraint: no_jquery=use vanilla JS or modern frameworks
- "dont use !important in css" → constraint: no_important=css
- "avoid inline styles" → constraint: no_inline_styles=use CSS classes
- "im building a saas with flask" → project: current_project=SaaS with Flask
- "i prefer typescript over javascript" → preference: language_preference=TypeScript
- "i work as a data scientist at google" → profile: role=data scientist, company=Google
- "from now on remember: write in UK English, keep responses short" → preference: writing_language=UK English, response_length=short
- "my background: hotel GM transitioning into software" → profile: background=hotel GM transitioning into software
- "dont remember this but my salary is 100k" → nothing (opt-out)
- "off the record, i hate my job" → nothing (opt-out)
- "what's the weather" → nothing (question)
- "help me debug this" → nothing (question)
- "what tools should I use for my project?" → nothing (question, not a fact)
- "can you recommend a framework?" → nothing (question)
- "i am xela not ada" → profile: name=Xela (NOT Ada — "not ada" is a negation)
- "my name is alex not alice" → profile: name=Alex (NOT Alice)
- "i'm not a designer" → nothing (negation, not an affirmation)
- "that's not my timezone" → nothing (negation)"""


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

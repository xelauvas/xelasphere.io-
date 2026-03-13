"""
Reflection Module - Lightweight Post-Response Analysis
=======================================================
After each response, the system reflects on:
    1. Did I answer the user's actual question?
    2. What are the next steps?
    3. What should be remembered?

Only stores artifacts, NOT the reflection text itself:
    - next_steps: checklist for follow-through
    - memory_candidates: items for memory gate

Usage:
    from app.reflection import reflect_on_response

    result = reflect_on_response(user_prompt, assistant_response)
    if result.next_steps:
        store_next_steps(session_id, result.next_steps)
    if result.memory_candidates:
        feed_to_memory_gate(result.memory_candidates)
"""

import json
import logging
import re
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from .config import Config

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ReflectionResult:
    """Result of reflection analysis."""
    answered_question: bool = True
    next_steps: List[str] = field(default_factory=list)
    memory_candidates: List[Dict[str, str]] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)  # warnings like "user asked X, I talked about Y"

    def has_actionable_items(self) -> bool:
        return bool(self.next_steps or self.memory_candidates or self.flags)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answered": self.answered_question,
            "next_steps": self.next_steps,
            "memory_candidates": self.memory_candidates,
            "flags": self.flags
        }


# =============================================================================
# Reflection Prompt
# =============================================================================

REFLECTION_PROMPT = """Analyze this conversation turn. Be extremely brief.

USER ASKED: {user_prompt}

ASSISTANT REPLIED: {assistant_response}

Answer in JSON only:
{{
  "answered": true/false,
  "flags": ["issue1"] or [],
  "next_steps": ["step1", "step2"] or [],
  "memory": [
    {{"type": "preference|project|profile|do_not_assume", "text": "concise fact"}}
  ] or []
}}

RULES:
- "answered": false only if response completely missed the question
- "flags": brief note if response drifted or missed a constraint
- "next_steps": only if user's task is multi-step and incomplete
- "memory": only STABLE facts about USER (not task progress)
  - preference: communication style, format preferences
  - project: what they're building, tech stack
  - profile: name, role, expertise
  - do_not_assume: things NOT to guess

If nothing notable, return: {{"answered": true, "flags": [], "next_steps": [], "memory": []}}"""


# =============================================================================
# Core Logic
# =============================================================================

def _call_reflection_llm(prompt: str) -> Optional[str]:
    """Call LLM for reflection analysis."""
    try:
        api_key = Config.GROQ_API_KEY
        if not api_key:
            return None

        response = requests.post(
            Config.API_URL,
            json={
                "messages": [{"role": "user", "content": prompt}],
                "model": "llama-3.1-8b-instant",
                "max_tokens": 300,
                "temperature": 0.1
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=4
        )

        if response.status_code != 200:
            return None

        return response.json()["choices"][0]["message"]["content"].strip()

    except Exception as e:
        logger.debug(f"Reflection LLM call failed: {e}")
        return None


def _parse_reflection(response: str) -> ReflectionResult:
    """Parse LLM response into ReflectionResult."""
    result = ReflectionResult()

    try:
        # Clean up response
        response = re.sub(r"```json\s*", "", response)
        response = re.sub(r"```\s*", "", response)

        # Find JSON
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if not match:
            return result

        data = json.loads(match.group())

        result.answered_question = data.get("answered", True)
        result.flags = data.get("flags", [])[:3]  # Cap at 3
        result.next_steps = data.get("next_steps", [])[:5]  # Cap at 5

        # Parse memory candidates
        for mem in data.get("memory", [])[:3]:  # Cap at 3
            if isinstance(mem, dict) and mem.get("text"):
                result.memory_candidates.append({
                    "type": mem.get("type", "profile"),
                    "text": mem.get("text", "")[:200]  # Cap length
                })

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug(f"Failed to parse reflection: {e}")

    return result


def _should_reflect(user_prompt: str, assistant_response: str) -> bool:
    """Determine if reflection is worthwhile for this turn."""
    # Skip very short exchanges
    if len(user_prompt) < 15 or len(assistant_response) < 50:
        return False

    # Skip obvious chit-chat
    chit_chat = ["hello", "hi ", "thanks", "thank you", "bye", "ok", "okay", "sure", "got it"]
    prompt_lower = user_prompt.lower().strip()
    if any(prompt_lower.startswith(p) for p in chit_chat):
        return False

    # Skip if response is just acknowledgment
    response_lower = assistant_response.lower()[:100]
    if any(ack in response_lower for ack in ["you're welcome", "no problem", "happy to help"]):
        return False

    return True


# =============================================================================
# Main Interface
# =============================================================================

def reflect_on_response(
    user_prompt: str,
    assistant_response: str,
    skip_threshold: bool = True
) -> ReflectionResult:
    """
    Perform lightweight reflection on a conversation turn.

    Analyzes:
        1. Did the response answer the actual question?
        2. Are there incomplete next steps?
        3. Are there facts worth remembering?

    Args:
        user_prompt: What the user asked
        assistant_response: What the assistant replied
        skip_threshold: Whether to skip trivial exchanges (default True)

    Returns:
        ReflectionResult with next_steps and memory_candidates
    """
    # Quick filter
    if skip_threshold and not _should_reflect(user_prompt, assistant_response):
        return ReflectionResult()

    # Build prompt
    prompt = REFLECTION_PROMPT.format(
        user_prompt=user_prompt[:800],
        assistant_response=assistant_response[:1500]
    )

    # Call LLM
    response = _call_reflection_llm(prompt)
    if not response:
        return ReflectionResult()

    # Parse result
    return _parse_reflection(response)


def get_next_steps_for_session(reflection: ReflectionResult) -> Optional[List[str]]:
    """
    Extract next steps to store in session (if any).
    Returns None if no steps worth storing.
    """
    if not reflection.next_steps:
        return None

    # Filter out generic steps
    meaningful = []
    generic = ["let me know", "feel free", "happy to help", "if you need"]

    for step in reflection.next_steps:
        step_lower = step.lower()
        if not any(g in step_lower for g in generic):
            meaningful.append(step)

    return meaningful if meaningful else None


def get_memory_candidates(reflection: ReflectionResult) -> List[Dict[str, str]]:
    """
    Extract memory candidates for the memory gate.
    Returns list of {type, text} dicts.
    """
    return reflection.memory_candidates

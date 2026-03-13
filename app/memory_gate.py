"""
Memory Gate Module (DEPRECATED)
===============================

!!! DEPRECATED !!!
This module has been superseded by Memory V2.
Use app.memory_v2.MemoryOrchestrator instead.

Migration guide:
    # Old way (DEPRECATED)
    from app.memory_gate import process_memory_gate
    result = process_memory_gate(user_id, prompt, response, memory, save=True)

    # New way (Memory V2)
    from app.memory_v2 import MemoryOrchestrator, SessionContext, ConversationState
    orchestrator = MemoryOrchestrator(user_id, session_ctx)
    context = orchestrator.get_context(user_message, conv_state)
    result = orchestrator.post_turn(user_message, assistant_message, conv_state, context.eval_metadata)

This module is kept for backward compatibility but will be removed in a future release.
!!! DEPRECATED !!!

Original description:
---------------------
Filters and manages long-term memory storage to keep it clean and useful.

Only stores items that are:
- Stable (won't change tomorrow)
- Reusable (will help in future sessions)
- User-specific (preference/profile/goals)
- Non-sensitive (no passwords, tokens, private data)
"""
import warnings
warnings.warn(
    "memory_gate is deprecated and will be removed in a future release. "
    "Use app.memory_v2.MemoryOrchestrator instead.",
    DeprecationWarning,
    stacklevel=2
)

import json
import logging
import re
import requests
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .config import Config, db

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Types of memories that can be stored (maps to buckets)."""
    PREFERENCES = "preferences"    # tone, format, language, style
    PROJECTS = "projects"          # ongoing work, stack, constraints
    PROFILE = "profile"            # name, role, timezone, expertise
    DO_NOT_ASSUME = "do_not_assume"  # things model should NOT guess

    # Legacy mappings (for backward compatibility)
    PREFERENCE = "preferences"
    PROJECT = "projects"
    FACT = "profile"
    SKILL = "profile"
    CONTEXT = "profile"


@dataclass
class MemoryItem:
    """A single memory item to potentially store."""
    type: MemoryType
    text: str
    confidence: float  # 0.0 to 1.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "text": self.text,
            "confidence": self.confidence,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        return cls(
            type=MemoryType(data.get("type", "fact")),
            text=data.get("text", ""),
            confidence=float(data.get("confidence", 0.5)),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat())
        )


@dataclass
class MemoryEvaluation:
    """Result of memory evaluation."""
    should_store: bool
    items: List[MemoryItem] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "store": self.should_store,
            "items": [item.to_dict() for item in self.items],
            "reasoning": self.reasoning
        }


# Prompt template for memory extraction (uses buckets)
MEMORY_GATE_PROMPT = """You are a memory filter for a personal AI assistant. Analyze the conversation and decide what (if anything) should be saved to the user's long-term memory.

ONLY extract information that is:
1. STABLE - Won't change tomorrow (not "I'm tired" or "debugging a bug")
2. REUSABLE - Will help personalize future conversations
3. USER-SPECIFIC - About the user's identity, preferences, or goals
4. NON-SENSITIVE - No passwords, API keys, private medical/financial data

MEMORY BUCKETS:
- preferences: Communication style, output format, tone, language (e.g., "prefers short answers", "wants TypeScript")
- projects: Ongoing work, tech stack, goals, constraints (e.g., "building e-commerce app with Next.js")
- profile: Stable facts: name, role, timezone, expertise (e.g., "backend developer in Berlin")
- do_not_assume: Things the model should NOT guess (e.g., "not a native English speaker")

EXISTING MEMORY (avoid duplicates):
{existing_memory}

CONVERSATION:
User: {user_prompt}
Assistant: {assistant_response}

Respond with ONLY valid JSON (no markdown, no explanation):
{{
  "store": true/false,
  "items": [
    {{"type": "preferences|projects|profile|do_not_assume", "text": "concise memory text", "confidence": 0.0-1.0}}
  ],
  "reasoning": "brief explanation"
}}

If nothing worth storing, return: {{"store": false, "items": [], "reasoning": "..."}}"""


class MemoryGate:
    """
    Evaluates conversation content and manages long-term memory storage.

    Example:
        gate = MemoryGate()
        result = gate.evaluate("I prefer Python over JavaScript", "Got it!", "")
        # result.should_store = True
        # result.items = [MemoryItem(type="preference", text="Prefers Python over JavaScript", confidence=0.9)]
    """

    def __init__(
        self,
        model: str = "llama-3.1-8b-instant",  # Fast, cheap model for filtering
        min_confidence: float = 0.7,
        max_items_per_eval: int = 3,
        max_memory_items: int = 50
    ):
        """
        Initialize the memory gate.

        Args:
            model: Model to use for evaluation (smaller model is fine)
            min_confidence: Minimum confidence threshold to store (0.0-1.0)
            max_items_per_eval: Max items to extract per conversation turn
            max_memory_items: Max total items to keep in long-term memory
        """
        self.model = model
        self.min_confidence = min_confidence
        self.max_items_per_eval = max_items_per_eval
        self.max_memory_items = max_memory_items

    def evaluate(
        self,
        user_prompt: str,
        assistant_response: str,
        existing_memory: str = ""
    ) -> MemoryEvaluation:
        """
        Evaluate if conversation content should be stored in long-term memory.

        Args:
            user_prompt: The user's message
            assistant_response: The assistant's response
            existing_memory: Current long-term memory (to avoid duplicates)

        Returns:
            MemoryEvaluation with should_store flag and items list
        """
        # Quick pre-filter: skip very short or obviously transient messages
        if self._should_skip(user_prompt, assistant_response):
            return MemoryEvaluation(
                should_store=False,
                reasoning="Message too short or transient"
            )

        # Build the evaluation prompt
        prompt = MEMORY_GATE_PROMPT.format(
            existing_memory=existing_memory[:1000] if existing_memory else "(empty)",
            user_prompt=user_prompt[:1500],
            assistant_response=assistant_response[:1500]
        )

        try:
            # Call the AI to evaluate
            response = self._call_model(prompt)
            evaluation = self._parse_response(response)

            # Filter by confidence threshold
            evaluation.items = [
                item for item in evaluation.items
                if item.confidence >= self.min_confidence
            ][:self.max_items_per_eval]

            evaluation.should_store = len(evaluation.items) > 0

            return evaluation

        except Exception as e:
            logger.warning(f"Memory gate evaluation failed: {e}")
            return MemoryEvaluation(
                should_store=False,
                reasoning=f"Evaluation error: {str(e)}"
            )

    def _should_skip(self, user_prompt: str, assistant_response: str) -> bool:
        """Quick pre-filter to skip obviously non-memorable content."""
        # Skip very short messages
        if len(user_prompt) < 15:
            return True

        # Skip common transient patterns
        transient_patterns = [
            r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|bye)[\s!.?]*$",
            r"^what('s| is) the (time|date|weather)",
            r"^(help|test|testing|debug)",
            r"^(lol|haha|hmm|umm|uh)",
        ]

        prompt_lower = user_prompt.lower().strip()
        for pattern in transient_patterns:
            if re.match(pattern, prompt_lower, re.IGNORECASE):
                return True

        return False

    def _call_model(self, prompt: str) -> str:
        """Call the AI model to evaluate memory."""
        # Use Groq API (fast and cheap)
        api_key = Config.GROQ_API_KEY
        if not api_key:
            raise ValueError("GROQ_API_KEY not configured")

        url = Config.API_URL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "messages": [{"role": "user", "content": prompt}],
            "model": self.model,
            "max_tokens": 500,
            "temperature": 0.3  # Low temperature for consistent extraction
        }

        response = requests.post(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()

        result = response.json()
        return result["choices"][0]["message"]["content"].strip()

    def _parse_response(self, response: str) -> MemoryEvaluation:
        """Parse the AI response into MemoryEvaluation."""
        # Clean up response (remove markdown code blocks if present)
        response = re.sub(r"```json\s*", "", response)
        response = re.sub(r"```\s*", "", response)
        response = response.strip()

        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Try to find JSON in the response
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                return MemoryEvaluation(
                    should_store=False,
                    reasoning="Failed to parse response"
                )

        items = []
        for item_data in data.get("items", []):
            try:
                items.append(MemoryItem(
                    type=MemoryType(item_data.get("type", "fact")),
                    text=item_data.get("text", ""),
                    confidence=float(item_data.get("confidence", 0.5))
                ))
            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping invalid memory item: {e}")
                continue

        return MemoryEvaluation(
            should_store=data.get("store", False),
            items=items,
            reasoning=data.get("reasoning", "")
        )

    def save_memories(
        self,
        user_id: str,
        items: List[MemoryItem],
        existing_memory: Optional[str] = None
    ) -> bool:
        """
        Save memory items to structured buckets in Firestore.

        Args:
            user_id: The user's ID
            items: List of MemoryItem to save
            existing_memory: Ignored (kept for backward compatibility)

        Returns:
            True if save succeeded, False otherwise
        """
        if not items:
            return True

        try:
            from .memory_buckets import save_to_bucket

            saved = 0
            for item in items:
                # Map type to bucket name
                bucket_name = item.type.value
                if bucket_name in ["preference", "fact", "skill", "context"]:
                    # Legacy type mapping
                    bucket_map = {
                        "preference": "preferences",
                        "fact": "profile",
                        "skill": "profile",
                        "context": "profile"
                    }
                    bucket_name = bucket_map.get(bucket_name, "profile")

                if save_to_bucket(user_id, bucket_name, item.text, item.confidence):
                    saved += 1

            if saved > 0:
                logger.info(f"Saved {saved} memories for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save memories for user {user_id}: {e}")
            return False

    def get_memories(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all memory items for a user as structured data.

        Args:
            user_id: The user's ID

        Returns:
            List of memory items as dictionaries
        """
        try:
            from .memory_buckets import MemoryBucketManager
            manager = MemoryBucketManager(user_id)
            return manager.get_flat_list()

        except Exception as e:
            logger.error(f"Failed to get memories for user {user_id}: {e}")
            return []

    def clear_memories(self, user_id: str) -> bool:
        """
        Clear all long-term memories for a user.

        Args:
            user_id: The user's ID

        Returns:
            True if cleared successfully
        """
        try:
            from .memory_buckets import MemoryBucketManager
            manager = MemoryBucketManager(user_id)
            return manager.clear_all()

        except Exception as e:
            logger.error(f"Failed to clear memories for user {user_id}: {e}")
            return False

    def remove_memory(self, user_id: str, memory_text: str) -> bool:
        """
        Remove a specific memory item.

        Args:
            user_id: The user's ID
            memory_text: The text of the memory to remove

        Returns:
            True if removed successfully
        """
        try:
            from .memory_buckets import MemoryBucketManager
            manager = MemoryBucketManager(user_id)
            return manager.remove(memory_text)

        except Exception as e:
            logger.error(f"Failed to remove memory for user {user_id}: {e}")
            return False


# Convenience function for easy integration
def process_memory_gate(
    user_id: str,
    user_prompt: str,
    assistant_response: str,
    existing_memory: str = "",
    save: bool = True
) -> MemoryEvaluation:
    """
    Process a conversation turn through the memory gate.

    This is the main entry point for integrating with the chat flow.

    Args:
        user_id: The user's ID
        user_prompt: The user's message
        assistant_response: The assistant's response
        existing_memory: Current long-term memory
        save: Whether to save extracted memories immediately

    Returns:
        MemoryEvaluation with results

    Example:
        result = process_memory_gate(
            user_id="user123",
            user_prompt="I'm a Python developer working on ML projects",
            assistant_response="Great! I'll keep that in mind...",
            existing_memory="[fact] User's name is John"
        )
        # result.items might contain:
        # [MemoryItem(type="fact", text="User is a Python developer", confidence=0.9),
        #  MemoryItem(type="project", text="Working on ML projects", confidence=0.85)]
    """
    gate = MemoryGate()
    result = gate.evaluate(user_prompt, assistant_response, existing_memory)

    if save and result.should_store:
        gate.save_memories(user_id, result.items, existing_memory)

    return result

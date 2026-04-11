"""
Memory V2 LLM-based Smart Retrieval
===================================
Intelligent memory retrieval using LLM for semantic relevance.

Fallback chain:
  1. GPT-4.1 Nano (OpenAI) — fast, cheap, best quality
  2. Groq llama-3.1-8b — free backup
  3. Skip — chat works without memory scoring
"""

import json
import logging
import os
import requests
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .schema import MemoryItem, MemoryType

logger = logging.getLogger(__name__)

# Provider configs
PROVIDERS = [
    {
        "name": "openai",
        "model": "gpt-4.1-nano",
        "url": "https://api.openai.com/v1/chat/completions",
        "env_key": "OPENAI_API_KEY",
        "timeout": 8,
    },
    {
        "name": "groq",
        "model": "llama-3.1-8b-instant",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env_key": "GROQ_API_KEY",
        "timeout": 10,
    },
]

# System prompt for memory relevance scoring
RETRIEVAL_SYSTEM_PROMPT = """You are a memory relevance assistant. Given a user query and a list of stored memories, decide which memories are relevant.

For each memory, score relevance 0-10:
- 10: Directly answers or is essential to the query
- 7-9: Highly relevant, provides important context
- 4-6: Somewhat relevant, might be useful
- 1-3: Marginally relevant
- 0: Not relevant at all

Return JSON: {"relevant": [{"id": "...", "score": N, "reason": "..."}]}
Only include memories with score >= 4.

Rules:
1. Semantic matching - "recommend a framework" matches "tech_stack=Django" even without keyword
2. Constraints ALWAYS relevant if they could apply (score minimum 7)
3. Profile relevant for personalization questions
4. Projects relevant for coding/building questions
5. Preferences relevant for style/format questions
6. Works in ANY language - understand the meaning, not just keywords

Examples:
Query: "what framework should I use?"
Memories: [{id: "m1", key: "tech_stack", value: "Django, React"}]
→ {"relevant": [{"id": "m1", "score": 9, "reason": "tech stack directly relevant to framework question"}]}

Query: "write me a function"
Memories: [{id: "m1", key: "code_style", value: "prefer explicit types"}, {id: "m2", key: "name", value: "Alex"}]
→ {"relevant": [{"id": "m1", "score": 8, "reason": "code style applies to writing code"}]}

Query: "bonjour, comment ça va?"
Memories: [{id: "m1", key: "name", value: "Marie"}, {id: "m2", key: "timezone", value: "Europe/Paris"}]
→ {"relevant": [{"id": "m1", "score": 6, "reason": "greeting - name can be used"}]}

Query: "help me debug this error"
Memories: [{id: "m1", key: "no_jquery", value: "use vanilla JS"}, {id: "m2", key: "favorite_color", value: "blue"}]
→ {"relevant": [{"id": "m1", "score": 7, "reason": "constraint may apply to debugging"}]}

Query: "what's the weather?"
Memories: [{id: "m1", key: "location", value: "Berlin"}, {id: "m2", key: "role", value: "developer"}]
→ {"relevant": [{"id": "m1", "score": 10, "reason": "location needed for weather"}]}
"""


@dataclass
class RelevanceScore:
    """Relevance score from LLM."""
    memory_id: str
    score: float  # 0-10
    reason: str


class LLMMemoryRetriever:
    """
    Intelligent memory retrieval using LLM with automatic fallback.

    Tries GPT-4.1 Nano first (fast, cheap, smart), falls back to
    Groq, then silently skips if both fail.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the LLM retriever.

        Args:
            api_key: Legacy param (ignored — keys loaded from env per provider).
        """
        self._provider_keys = {}
        for p in PROVIDERS:
            key = os.environ.get(p["env_key"])
            if key:
                self._provider_keys[p["name"]] = key

        if not self._provider_keys:
            logger.warning("No API keys configured for smart retrieval")

    def score_relevance(
        self,
        query: str,
        memories: List[MemoryItem]
    ) -> List[RelevanceScore]:
        """
        Score relevance of memories to the query using LLM.

        Args:
            query: The user's current message/query
            memories: List of candidate memories to score

        Returns:
            List of RelevanceScore for relevant memories (score >= 4)
        """
        if not self._provider_keys:
            logger.debug("LLM retrieval skipped - no API keys")
            return []

        if not query or not memories:
            return []

        # Skip for very short queries (greetings)
        if len(query.strip()) < 3:
            return []

        # Format memories for LLM
        memories_data = []
        for mem in memories:
            memories_data.append({
                "id": mem.id,
                "type": mem.type.value,
                "key": mem.key,
                "value": str(mem.value)[:200]  # Truncate long values
            })

        if not memories_data:
            return []

        # Try each provider in order
        for provider in PROVIDERS:
            api_key = self._provider_keys.get(provider["name"])
            if not api_key:
                continue

            try:
                response = self._call_llm(query, memories_data, provider, api_key)
                scores = self._parse_response(response)
                if scores is not None:
                    return scores
            except Exception as e:
                logger.warning("Memory retrieval failed on %s: %s", provider["name"], e)
                continue

        # All providers failed — skip silently
        logger.debug("All retrieval providers failed — skipping memory scoring")
        return []

    def _call_llm(self, query: str, memories: List[Dict], provider: Dict, api_key: str) -> str:
        """Call an LLM API for relevance scoring."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        memories_json = json.dumps(memories, indent=2)
        user_prompt = f"""Query: "{query}"

Memories:
{memories_json}

Which memories are relevant to this query? Score each 0-10."""

        payload = {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": RETRIEVAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 800,
            "response_format": {"type": "json_object"}
        }

        response = requests.post(
            provider["url"],
            headers=headers,
            json=payload,
            timeout=provider["timeout"]
        )

        if response.status_code == 429:
            logger.warning("%s rate limited for retrieval scoring", provider["name"])
            raise RuntimeError(f"{provider['name']} rate limited")

        if response.status_code >= 400:
            body = response.text[:200]
            logger.warning("%s returned %d: %s", provider["name"], response.status_code, body)
            raise RuntimeError(f"{provider['name']} returned {response.status_code}")

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _parse_response(self, response_text: str) -> List[RelevanceScore]:
        """Parse LLM response into RelevanceScore objects."""
        try:
            data = json.loads(response_text)

            # Handle various response formats
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("relevant") or data.get("memories") or data.get("results") or []
            else:
                return []

            scores = []
            for item in items:
                if not isinstance(item, dict):
                    continue

                memory_id = item.get("id") or item.get("memory_id")
                if not memory_id:
                    continue

                score = float(item.get("score", 0))

                # Only include if score >= 4 (threshold for "somewhat relevant")
                if score >= 4:
                    scores.append(RelevanceScore(
                        memory_id=memory_id,
                        score=score,
                        reason=item.get("reason", "")
                    ))

            logger.info(f"🧠 LLM retrieval scored {len(scores)} relevant memories")
            return scores

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM retrieval response: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing retrieval response: {e}")
            return []


def get_llm_retriever() -> Optional[LLMMemoryRetriever]:
    """Get a configured LLM retriever instance."""
    retriever = LLMMemoryRetriever()
    if not retriever._provider_keys:
        return None
    return retriever

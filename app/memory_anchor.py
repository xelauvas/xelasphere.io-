"""
Memory Anchoring - Contradiction Detection & Resolution
========================================================
Prevents memory contradictions by:
    1. Detecting conflicts between new and existing memories
    2. Newer info overrides older (with timestamp tracking)
    3. Deprecated items are removed, not just marked

Conflict types:
    - Direct contradiction: "prefers Python" vs "prefers JavaScript"
    - Update/refinement: "developer" → "senior developer"
    - Obsolete: "working on project X" → "finished project X"

Usage:
    from app.memory_anchor import resolve_memory_conflicts

    # Before adding new memory
    resolution = resolve_memory_conflicts(
        new_text="Prefers TypeScript",
        existing_entries=bucket_entries,
        bucket_name="preferences"
    )

    if resolution.conflicts:
        for old_entry in resolution.to_remove:
            bucket.remove(old_entry)
    bucket.add(resolution.final_text)
"""

import json
import logging
import re
import requests
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

from .config import Config

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ConflictResolution:
    """Result of conflict resolution."""
    has_conflict: bool = False
    conflict_type: str = ""  # "contradiction", "update", "obsolete"
    to_remove: List[Dict[str, Any]] = field(default_factory=list)  # Old entries to remove
    final_text: str = ""  # The text to store (may be merged/refined)
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_conflict": self.has_conflict,
            "conflict_type": self.conflict_type,
            "to_remove": self.to_remove,
            "final_text": self.final_text,
            "reasoning": self.reasoning
        }


# =============================================================================
# Conflict Detection Patterns
# =============================================================================

# Common contradiction patterns (for fast heuristic check)
CONTRADICTION_PATTERNS = [
    # Preferences
    (r"prefers?\s+(\w+)", r"prefers?\s+(\w+)"),
    (r"likes?\s+(\w+)", r"(doesn't like|dislikes?|hates?)\s+\1"),
    (r"wants?\s+(\w+)", r"(doesn't want|avoids?)\s+\1"),
    (r"always\s+(\w+)", r"never\s+\1"),

    # Boolean states
    (r"is\s+a\s+(\w+)", r"is\s+not\s+a\s+\1"),
    (r"uses?\s+(\w+)", r"(doesn't use|stopped using|quit)\s+\1"),
    (r"works?\s+(at|on|with)\s+(\w+)", r"(left|quit|stopped).*(at|on|with)\s+\2"),
]

# Keywords that signal updates (not contradictions)
UPDATE_SIGNALS = [
    "now", "currently", "recently", "switched to", "changed to",
    "updated", "moved to", "started", "began", "actually"
]

# Keywords that signal obsolescence
OBSOLETE_SIGNALS = [
    "finished", "completed", "done with", "no longer", "stopped",
    "quit", "left", "ended", "closed"
]


# =============================================================================
# Heuristic Conflict Detection (Fast)
# =============================================================================

def _extract_key_terms(text: str) -> set:
    """Extract key terms for comparison."""
    # Remove common words
    stopwords = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
                 "to", "of", "in", "for", "on", "with", "as", "at", "by", "i", "my"}
    words = set(re.findall(r'\b\w+\b', text.lower()))
    return words - stopwords


def _heuristic_conflict_check(new_text: str, old_text: str) -> Tuple[bool, str]:
    """
    Fast heuristic check for conflicts.
    Returns (is_conflict, conflict_type)
    """
    new_lower = new_text.lower()
    old_lower = old_text.lower()

    # Check for update signals (not a conflict, but an update)
    for signal in UPDATE_SIGNALS:
        if signal in new_lower:
            # Check if they're about the same topic
            new_terms = _extract_key_terms(new_text)
            old_terms = _extract_key_terms(old_text)
            overlap = len(new_terms & old_terms) / max(len(new_terms | old_terms), 1)
            if overlap > 0.3:
                return True, "update"

    # Check for obsolescence signals
    for signal in OBSOLETE_SIGNALS:
        if signal in new_lower:
            new_terms = _extract_key_terms(new_text)
            old_terms = _extract_key_terms(old_text)
            overlap = len(new_terms & old_terms) / max(len(new_terms | old_terms), 1)
            if overlap > 0.3:
                return True, "obsolete"

    # Check for direct contradictions using patterns
    for pattern1, pattern2 in CONTRADICTION_PATTERNS:
        match1 = re.search(pattern1, new_lower)
        match2 = re.search(pattern2, old_lower)
        if match1 and match2:
            return True, "contradiction"
        # Also check reverse
        match1 = re.search(pattern1, old_lower)
        match2 = re.search(pattern2, new_lower)
        if match1 and match2:
            return True, "contradiction"

    # Check for same-topic different-value (e.g., "prefers Python" vs "prefers JavaScript")
    # Pattern: same verb/relation, different object
    pref_pattern = r"(prefers?|likes?|uses?|works?\s+(?:with|on|at))\s+(.+)"
    new_match = re.search(pref_pattern, new_lower)
    old_match = re.search(pref_pattern, old_lower)
    if new_match and old_match:
        if new_match.group(1) == old_match.group(1):  # Same verb
            if new_match.group(2) != old_match.group(2):  # Different object
                return True, "contradiction"

    return False, ""


# =============================================================================
# LLM-Based Conflict Detection (Accurate)
# =============================================================================

CONFLICT_DETECTION_PROMPT = """Compare these two memory items and detect if there's a conflict.

OLD MEMORY: {old_text}
NEW MEMORY: {new_text}

Answer in JSON only:
{{
  "conflict": true/false,
  "type": "none|contradiction|update|obsolete",
  "keep": "new|old|both",
  "reasoning": "brief explanation"
}}

RULES:
- "contradiction": new directly contradicts old (e.g., "likes X" vs "likes Y")
- "update": new refines/updates old (e.g., "developer" → "senior developer")
- "obsolete": new indicates old is no longer true (e.g., "finished project X")
- "none": no conflict, both can coexist
- "keep": which to keep (usually "new" if conflict)

If unsure, prefer "none" (keep both)."""


def _llm_conflict_check(new_text: str, old_text: str) -> Tuple[bool, str, str]:
    """
    LLM-based conflict check for complex cases.
    Returns (is_conflict, conflict_type, keep_which)
    """
    try:
        api_key = Config.GROQ_API_KEY
        if not api_key:
            return False, "", "both"

        prompt = CONFLICT_DETECTION_PROMPT.format(
            old_text=old_text[:200],
            new_text=new_text[:200]
        )

        response = requests.post(
            Config.API_URL,
            json={
                "messages": [{"role": "user", "content": prompt}],
                "model": "llama-3.1-8b-instant",
                "max_tokens": 150,
                "temperature": 0.1
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=3
        )

        if response.status_code != 200:
            return False, "", "both"

        content = response.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON
        content = re.sub(r"```json\s*", "", content)
        content = re.sub(r"```\s*", "", content)
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if not match:
            return False, "", "both"

        data = json.loads(match.group())

        is_conflict = data.get("conflict", False)
        conflict_type = data.get("type", "none")
        keep = data.get("keep", "both")

        return is_conflict, conflict_type, keep

    except Exception as e:
        logger.debug(f"LLM conflict check failed: {e}")
        return False, "", "both"


# =============================================================================
# Main Interface
# =============================================================================

def check_for_conflicts(
    new_text: str,
    existing_entries: List[Dict[str, Any]],
    use_llm: bool = True
) -> List[ConflictResolution]:
    """
    Check new memory against existing entries for conflicts.

    Args:
        new_text: The new memory text to add
        existing_entries: List of existing MemoryEntry dicts
        use_llm: Whether to use LLM for complex conflict detection

    Returns:
        List of ConflictResolution for each detected conflict
    """
    conflicts = []

    for entry in existing_entries:
        old_text = entry.get("text", "")
        if not old_text:
            continue

        # Fast heuristic check first
        is_conflict, conflict_type = _heuristic_conflict_check(new_text, old_text)

        # If heuristic uncertain but texts are similar, use LLM
        if not is_conflict and use_llm:
            # Check if texts might be related (word overlap)
            new_terms = _extract_key_terms(new_text)
            old_terms = _extract_key_terms(old_text)
            overlap = len(new_terms & old_terms) / max(len(new_terms | old_terms), 1)

            if overlap > 0.25:  # Potentially related
                is_conflict, conflict_type, keep = _llm_conflict_check(new_text, old_text)
                if is_conflict and keep == "new":
                    conflicts.append(ConflictResolution(
                        has_conflict=True,
                        conflict_type=conflict_type,
                        to_remove=[entry],
                        final_text=new_text,
                        reasoning=f"LLM detected {conflict_type}: newer overrides older"
                    ))
        elif is_conflict:
            conflicts.append(ConflictResolution(
                has_conflict=True,
                conflict_type=conflict_type,
                to_remove=[entry],
                final_text=new_text,
                reasoning=f"Heuristic detected {conflict_type}: newer overrides older"
            ))

    return conflicts


def resolve_memory_conflicts(
    new_text: str,
    existing_entries: List[Dict[str, Any]],
    bucket_name: str = "",
    use_llm: bool = True
) -> ConflictResolution:
    """
    Resolve conflicts between new memory and existing entries.
    Returns a single resolution with all entries to remove.

    Args:
        new_text: New memory text
        existing_entries: List of existing entries in the bucket
        bucket_name: Name of the bucket (for context)
        use_llm: Whether to use LLM for complex cases

    Returns:
        ConflictResolution with aggregated to_remove list
    """
    if not new_text or not existing_entries:
        return ConflictResolution(final_text=new_text)

    conflicts = check_for_conflicts(new_text, existing_entries, use_llm)

    if not conflicts:
        return ConflictResolution(final_text=new_text)

    # Aggregate all entries to remove
    to_remove = []
    conflict_types = set()

    for conflict in conflicts:
        to_remove.extend(conflict.to_remove)
        if conflict.conflict_type:
            conflict_types.add(conflict.conflict_type)

    return ConflictResolution(
        has_conflict=True,
        conflict_type=", ".join(conflict_types),
        to_remove=to_remove,
        final_text=new_text,
        reasoning=f"Resolved {len(to_remove)} conflict(s): newer memory overrides older"
    )


def get_memory_version(entry: Dict[str, Any]) -> int:
    """
    Get version number for a memory entry.
    Version is derived from update count or defaults to 1.
    """
    return entry.get("version", 1)


def increment_version(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Increment version number for an updated entry."""
    entry["version"] = get_memory_version(entry) + 1
    entry["updated_at"] = datetime.utcnow().isoformat()
    return entry

# app/context/context_builder.py
"""Context Builder — smart prompt assembly for XelaSphere chat.

Replaces the fixed 20-message cap with 4-zone prompt assembly:
  Zone 1: System prompt + instructions (fixed)
  Zone 2: Artifact context — dirty files only (dynamic budget)
  Zone 3: Conversation history — 3 tiers (fills remaining)
  Zone 4: Current user message (fixed)

API constraint: The XelaSphere API layer is string-based. send_prompt(),
get_ai_response(), and stream_deepseek_response() all accept a single
string prompt. ContextBuilder.build() therefore outputs a flat assembled
string via BuiltContext.prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Note: Dense minified JS or generated JSON can hit len/2.5.
# The 10% headroom should absorb this, but if context overruns
# appear in production, check code-density ratio before blaming the model.
_ENGLISH_CHARS_PER_TOKEN = 3.5
_CODE_CHARS_PER_TOKEN = 3.0
_HEADROOM_FACTOR = 0.90  # 10% safety margin

_CODE_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def estimate_tokens(text: str, is_code: bool = False) -> int:
    """Estimate token count using character-based heuristic."""
    if not text:
        return 0
    divisor = _CODE_CHARS_PER_TOKEN if is_code else _ENGLISH_CHARS_PER_TOKEN
    return int(len(text) / divisor)


def _has_code_indicators(text: str) -> bool:
    """Check if text is predominantly code."""
    if not text:
        return False
    return bool(_CODE_FENCE_RE.search(text)) or text.count("\n    ") > 5


def estimate_tokens_auto(text: str) -> int:
    """Estimate tokens, auto-detecting code vs English."""
    return estimate_tokens(text, is_code=_has_code_indicators(text))


def microcompact_message(
    message: Dict[str, Any],
    artifact_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Strip heavyweight content from an older message.

    Replaces code blocks >50 lines with placeholder.
    Preserves prompts unchanged. Returns a shallow copy.
    """
    result = message.copy()
    response = result.get("response", "")
    if not response:
        return result

    def _replace_long_code(match: re.Match) -> str:
        lang = match.group(1) or "code"
        code = match.group(2)
        line_count = code.rstrip("\n").count("\n") + 1
        if line_count <= 50:
            return match.group(0)
        version_hint = f" -- see artifact v{artifact_version}" if artifact_version else ""
        return f"[Code block: {lang}, {line_count} lines{version_hint}]"

    result["response"] = _CODE_FENCE_RE.sub(_replace_long_code, response)
    return result


def extract_turn_metadata(
    turn_number: int,
    user_message: str,
    action_type: str,
    had_error: bool,
    artifact_mutation: Optional[Dict[str, Any]] = None,
    error_detail: Optional[str] = None,
    model: str = "",
) -> Dict[str, Any]:
    """Extract structured metadata from a turn for Tier 2 summaries.

    Called at save time (in save_interaction), not at build time.
    user_intent_summary: strip code blocks first, then take first 80 chars.
    """
    stripped = _CODE_FENCE_RE.sub("", user_message).strip()
    intent = stripped[:80] if stripped else user_message[:80]

    meta: Dict[str, Any] = {
        "turn_number": turn_number,
        "user_intent_summary": intent,
        "action_type": action_type,
        "had_error": had_error,
        "model": model,
    }
    if error_detail:
        meta["error_detail"] = error_detail
    if artifact_mutation:
        meta["artifact_mutation"] = artifact_mutation
    return meta


def build_tier2_summary(metadata_list: List[Dict[str, Any]]) -> str:
    """Build a structured summary from turn metadata for Tier 2 injection."""
    if not metadata_list:
        return ""

    first_turn = metadata_list[0].get("turn_number", 1)
    last_turn = metadata_list[-1].get("turn_number", len(metadata_list))
    lines = [f"## Conversation Summary (turns {first_turn}-{last_turn})"]

    for meta in metadata_list:
        turn = meta.get("turn_number", "?")
        intent = meta.get("user_intent_summary", "")
        action = meta.get("action_type", "chat")
        had_error = meta.get("had_error", False)
        error_detail = meta.get("error_detail", "")
        mutation = meta.get("artifact_mutation")

        parts = [f"- Turn {turn}: {intent}"]

        if action == "create_artifact" and mutation:
            files = mutation.get("files_changed", [])
            to_ver = mutation.get("to_version", "")
            parts.append(f" -> Created artifact ({to_ver}, {len(files)} files)")
        elif action == "patch_artifact" and mutation:
            files = mutation.get("files_changed", [])
            to_ver = mutation.get("to_version", "")
            parts.append(f" -> Patched {', '.join(files)} -> {to_ver}")

        if had_error:
            err = f": {error_detail}" if error_detail else ""
            parts.append(f" [ERROR{err}]")

        lines.append("".join(parts))

    return "\n".join(lines)

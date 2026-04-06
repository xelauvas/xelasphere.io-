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


@dataclass
class BuiltContext:
    """Output of ContextBuilder.build().

    prompt: The flat assembled string to pass to the API.
    messages: Structured message list for future messages-API migration.
    """

    prompt: str
    messages: List[Dict[str, str]]
    artifact_files: List[Dict[str, Any]]
    token_estimate: int
    compression_level: str  # 'none' | 'microcompact' | 'summarized'


class ContextBuilder:
    """Smart prompt assembler with 4-zone budget allocation.

    Outputs a flat prompt string matching the existing API contract.
    """

    TIER1_SIZE = 5

    def __init__(self, token_budget: int = 16384) -> None:
        self.token_budget = token_budget

    def build(
        self,
        system_prompt: str,
        full_history: List[Dict[str, Any]],
        artifact_state: Optional[Any] = None,
        user_message: str = "",
        memory_context: Optional[str] = None,
    ) -> BuiltContext:
        """Assemble the optimal prompt within the token budget."""
        prompt_parts: List[str] = []
        messages: List[Dict[str, str]] = []

        # --- Zone 1: System prompt (fixed cost) ---
        zone1_text = system_prompt
        if memory_context:
            zone1_text += f"\n\nUser memory:\n{memory_context}"
        zone1_tokens = estimate_tokens_auto(zone1_text)
        prompt_parts.append(zone1_text)
        messages.append({"role": "system", "content": zone1_text})

        # --- Zone 4: User message (fixed cost, added last) ---
        zone4_text = f"User: {user_message}\nAssistant:"
        zone4_tokens = estimate_tokens_auto(zone4_text)

        # --- Zone 3 minimum: last TIER1_SIZE turns verbatim ---
        tier1_messages = full_history[-self.TIER1_SIZE:]
        tier1_text_estimate = self._estimate_history_tokens(tier1_messages)

        # --- Zone 2: Artifact context (dynamic budget) ---
        available = int(self.token_budget * _HEADROOM_FACTOR)
        zone2_budget_tokens = max(0, available - zone1_tokens - zone4_tokens - tier1_text_estimate)
        zone2_budget_bytes = int(zone2_budget_tokens * _ENGLISH_CHARS_PER_TOKEN)

        artifact_files: List[Dict[str, Any]] = []
        zone2_text = ""
        if artifact_state is not None:
            artifact_files = artifact_state.get_context_injection(
                max_bytes=zone2_budget_bytes,
                user_message=user_message,
            )
            if artifact_files:
                zone2_text = self._format_artifact_context(artifact_files)
                artifact_state.mark_sent([f["path"] for f in artifact_files])

        zone2_tokens = estimate_tokens_auto(zone2_text) if zone2_text else 0
        if zone2_text:
            prompt_parts.append(zone2_text)
            messages.append({"role": "system", "content": zone2_text})

        # --- Zone 3: Conversation history (fills remaining budget) ---
        remaining_tokens = available - zone1_tokens - zone2_tokens - zone4_tokens
        zone3_parts, zone3_messages, compression_level = self._build_zone3(
            full_history, remaining_tokens, artifact_state
        )
        prompt_parts.extend(zone3_parts)
        messages.extend(zone3_messages)

        # --- Zone 4: User message (always last) ---
        prompt_parts.append(zone4_text)
        messages.append({"role": "user", "content": user_message})

        prompt = "\n".join(prompt_parts)
        total_tokens = estimate_tokens_auto(prompt)

        return BuiltContext(
            prompt=prompt,
            messages=messages,
            artifact_files=artifact_files,
            token_estimate=total_tokens,
            compression_level=compression_level,
        )

    def _build_zone3(
        self,
        full_history: List[Dict[str, Any]],
        budget_tokens: int,
        artifact_state: Optional[Any],
    ) -> tuple:
        """Build Zone 3 conversation history with 3 tiers."""
        if not full_history:
            return [], [], "none"

        tier1_start = max(0, len(full_history) - self.TIER1_SIZE)
        older_messages = full_history[:tier1_start]
        tier1_messages = full_history[tier1_start:]

        artifact_version = None
        if artifact_state is not None:
            artifact_version = getattr(artifact_state, "version_id", None)

        # Tier 1: verbatim with microcompact on older ones in window
        compression_level = "none"
        never_compact_count = 2
        tier1_prompt_parts = []
        tier1_msg_list = []

        for idx, msg in enumerate(tier1_messages):
            is_recent = idx >= len(tier1_messages) - never_compact_count
            if is_recent:
                source_msg = msg
            else:
                source_msg = microcompact_message(msg, artifact_version=artifact_version)
                if source_msg.get("response") != msg.get("response"):
                    compression_level = "microcompact"

            prompt_text = source_msg.get("prompt", "")
            response_text = source_msg.get("response", "")
            if prompt_text:
                user_line = f"User: {prompt_text}"
                tier1_prompt_parts.append(user_line)
                tier1_msg_list.append({"role": "user", "content": prompt_text})
            if response_text:
                asst_line = f"Assistant: {response_text}"
                tier1_prompt_parts.append(asst_line)
                tier1_msg_list.append({"role": "assistant", "content": response_text})

        tier1_tokens = sum(estimate_tokens_auto(p) for p in tier1_prompt_parts)

        # Tier 2: Metadata summary of older messages
        tier2_text = ""
        if older_messages:
            metadata_list = []
            for i, msg in enumerate(older_messages):
                meta = msg.get("turn_metadata")
                if meta:
                    metadata_list.append(meta)
                else:
                    metadata_list.append({
                        "turn_number": i + 1,
                        "user_intent_summary": (msg.get("prompt", "") or "")[:80],
                        "action_type": "chat",
                        "had_error": False,
                        "artifact_mutation": None,
                        "model": msg.get("model_id", ""),
                    })
            tier2_text = build_tier2_summary(metadata_list)
            compression_level = "summarized"

        tier2_tokens = estimate_tokens_auto(tier2_text) if tier2_text else 0

        # Assemble within budget
        total_zone3 = tier1_tokens + tier2_tokens
        result_parts = []
        result_msgs = []

        if total_zone3 <= budget_tokens:
            if tier2_text:
                result_parts.append(tier2_text)
                result_msgs.append({"role": "system", "content": tier2_text})
            result_parts.extend(tier1_prompt_parts)
            result_msgs.extend(tier1_msg_list)
        elif tier1_tokens <= budget_tokens:
            remaining = budget_tokens - tier1_tokens
            if tier2_text and remaining > 0:
                tier2_lines = tier2_text.split("\n")
                truncated = []
                tokens_used = 0
                for line in tier2_lines:
                    line_tokens = estimate_tokens_auto(line)
                    if tokens_used + line_tokens > remaining:
                        break
                    truncated.append(line)
                    tokens_used += line_tokens
                if truncated:
                    trunc_text = "\n".join(truncated)
                    result_parts.append(trunc_text)
                    result_msgs.append({"role": "system", "content": trunc_text})
            result_parts.extend(tier1_prompt_parts)
            result_msgs.extend(tier1_msg_list)
        else:
            tokens_used = 0
            trimmed_parts = []
            trimmed_msgs = []
            # Keep most recent first
            for part, msg in zip(reversed(tier1_prompt_parts), reversed(tier1_msg_list)):
                part_tokens = estimate_tokens_auto(part)
                if tokens_used + part_tokens > budget_tokens:
                    break
                trimmed_parts.append(part)
                trimmed_msgs.append(msg)
                tokens_used += part_tokens
            result_parts = list(reversed(trimmed_parts))
            result_msgs = list(reversed(trimmed_msgs))

        return result_parts, result_msgs, compression_level

    def _estimate_history_tokens(self, messages: List[Dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            if msg.get("prompt"):
                total += estimate_tokens_auto(f"User: {msg['prompt']}")
            if msg.get("response"):
                total += estimate_tokens_auto(f"Assistant: {msg['response']}")
        return total

    def _format_artifact_context(self, files: List[Dict[str, Any]]) -> str:
        parts = ["CURRENT ARTIFACT FILES (for reference when fixing/updating):"]
        for f in files:
            path = f.get("path", "unknown")
            lang = f.get("language", "text")
            content = f.get("content", "")
            parts.append(f"\n{path} ({lang}):")
            parts.append(f"```{lang}")
            parts.append(content)
            parts.append("```")
        return "\n".join(parts)

"""Phase 2: Kimi LLM-powered memory consolidation."""
import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from app.config import Config
from app.memory_v2.dream.report import ConsolidationReport
from app.memory_v2.schema import MemoryItem

logger = logging.getLogger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """You are a memory consolidation assistant. You will be given a structured report of a user's memory bank, including conflicts, low-value entries, and all active memories.

Your job is to analyze the memories and respond with a JSON object containing exactly these four arrays:

{
  "resolve_conflicts": [
    {"keep_id": "<id>", "supersede_ids": ["<id>", ...], "reason": "<why keep this one>"}
  ],
  "prune": [
    {"memory_id": "<id>", "reason": "<why remove>"}
  ],
  "promote": [
    {"memory_id": "<id>", "reason": "<why promote to active>"}
  ],
  "synthesize": [
    {"type": "<memory_type>", "key": "<key>", "value": "<value>", "evidence_ids": ["<id>", ...], "confidence": 0.0}
  ]
}

Rules:
- resolve_conflicts: for each conflict group, pick the most accurate/recent memory to keep and list the rest as supersede_ids.
- prune: recommend memories that are stale, redundant, or no longer useful.
- promote: recommend provisional memories that appear reliable and should become active.
- synthesize: if you see a clear pattern across multiple memories, synthesize a new higher-level memory.
- Keep syntheses minimal (only synthesize when there is clear, strong evidence).
- Respond ONLY with valid JSON. No markdown, no explanation outside the JSON."""

_EMPTY_RESULT: Dict[str, List[Any]] = {
    "resolve_conflicts": [],
    "prune": [],
    "promote": [],
    "synthesize": [],
}


def should_run_phase2(report: ConsolidationReport, pattern_threshold: int = 15) -> bool:
    """Return True if Phase 2 LLM consolidation should run."""
    if report.flagged_conflicts:
        return True
    if report.flagged_low_value:
        return True
    if len(report.all_active) >= pattern_threshold:
        return True
    return False


def _mem_to_dict(mem: MemoryItem) -> Dict[str, Any]:
    """Compact representation of a MemoryItem for prompt inclusion."""
    return {
        "id": mem.id,
        "key": mem.key,
        "value": mem.value,
        "type": mem.type.value if hasattr(mem.type, "value") else str(mem.type),
        "confidence": mem.confidence,
        "status": mem.status.value if hasattr(mem.status, "value") else str(mem.status),
    }


def build_consolidation_prompt(
    flagged_conflicts: List[List[str]],
    flagged_low_value: List[str],
    all_active: List[MemoryItem],
    memories_by_id: Dict[str, MemoryItem],
    token_budget: int = 4000,
) -> str:
    """Build a structured text prompt for Kimi consolidation.

    Sections:
      1. CONFLICTS — groups of conflicting memory IDs with their details
      2. LOW-VALUE — memories flagged as low-value
      3. ALL ACTIVE — full active memory list (capped by token budget)
    """
    sections: List[str] = []

    # Section 1: Conflicts
    if flagged_conflicts:
        lines = ["## CONFLICTS"]
        for group in flagged_conflicts:
            group_mems = [memories_by_id[mid] for mid in group if mid in memories_by_id]
            group_json = [_mem_to_dict(m) for m in group_mems]
            lines.append(json.dumps(group_json, ensure_ascii=False))
        sections.append("\n".join(lines))

    # Section 2: Low-value memories
    if flagged_low_value:
        lines = ["## LOW-VALUE MEMORIES"]
        low_mems = [memories_by_id[mid] for mid in flagged_low_value if mid in memories_by_id]
        for mem in low_mems:
            lines.append(json.dumps(_mem_to_dict(mem), ensure_ascii=False))
        sections.append("\n".join(lines))

    # Section 3: All active memories (budget-capped)
    # Rough heuristic: 1 token ≈ 4 chars; reserve some budget for sections above
    used_chars = sum(len(s) for s in sections)
    remaining_budget_chars = max(0, token_budget * 4 - used_chars - 200)

    active_lines = ["## ALL ACTIVE MEMORIES"]
    for mem in all_active:
        entry = json.dumps(_mem_to_dict(mem), ensure_ascii=False)
        if len("\n".join(active_lines)) + len(entry) + 1 > remaining_budget_chars:
            active_lines.append("... (truncated to fit token budget)")
            break
        active_lines.append(entry)
    sections.append("\n".join(active_lines))

    return "\n\n".join(sections)


def call_kimi(prompt: str) -> Optional[str]:
    """Call the Moonshot/Kimi API with the consolidation prompt.

    Returns the raw response string, or None on any failure.
    """
    try:
        api_key = Config.MOONSHOT_API_KEY
        api_url = Config.MOONSHOT_API_URL

        if not api_key:
            logger.warning("[dream] MOONSHOT_API_KEY not set — skipping Phase 2 LLM call")
            return None

        payload = {
            "model": "kimi-latest",
            "messages": [
                {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response = requests.post(api_url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("[dream] Phase 2: Kimi responded (%d chars)", len(content))
        return content

    except Exception as exc:
        logger.error("[dream] Phase 2: Kimi API call failed — %s", exc)
        return None


def parse_kimi_response(raw: str, max_syntheses: int = 3) -> Dict[str, List[Any]]:
    """Parse the raw Kimi JSON response into a structured dict.

    Handles:
    - Plain JSON strings
    - Markdown code blocks (```json ... ```)
    - Partial responses (missing keys default to [])
    - Malformed JSON (returns empty result)
    - Caps synthesize array at max_syntheses
    """
    if not raw:
        return dict(_EMPTY_RESULT)

    # Strip markdown code block if present
    stripped = raw.strip()
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if code_block:
        stripped = code_block.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("[dream] Phase 2: failed to parse Kimi response as JSON")
        return dict(_EMPTY_RESULT)

    if not isinstance(parsed, dict):
        return dict(_EMPTY_RESULT)

    result: Dict[str, List[Any]] = {
        "resolve_conflicts": parsed.get("resolve_conflicts", []) or [],
        "prune": parsed.get("prune", []) or [],
        "promote": parsed.get("promote", []) or [],
        "synthesize": parsed.get("synthesize", []) or [],
    }

    # Cap syntheses
    result["synthesize"] = result["synthesize"][:max_syntheses]

    return result


def run_phase2(
    report: ConsolidationReport,
    memories_by_id: Dict[str, MemoryItem],
    config_token_budget: int,
    config_max_syntheses: int,
) -> Optional[Dict[str, List[Any]]]:
    """Orchestrate Phase 2: prompt building, API call, and response parsing.

    Returns the parsed result dict, or None if Phase 2 was skipped or failed.
    """
    if not should_run_phase2(report):
        logger.info("[dream] Phase 2: skipped (nothing to do)")
        return None

    prompt = build_consolidation_prompt(
        flagged_conflicts=report.flagged_conflicts,
        flagged_low_value=report.flagged_low_value,
        all_active=report.all_active,
        memories_by_id=memories_by_id,
        token_budget=config_token_budget,
    )

    raw = call_kimi(prompt)
    if raw is None:
        return None

    return parse_kimi_response(raw, max_syntheses=config_max_syntheses)

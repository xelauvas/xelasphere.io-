"""Post-stream compliance auditor for scaffolded responses.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §5
Plan: W-17 (markdown parser) + W-18 (structured JSON short-circuit).

Usage (from chat_routes W-20):
    report = audit_response(scaffold, rendered_output,
                            meta={"render_path": "markdown", ...})
    # fire-and-forget write to Firestore (W-19)

Never raises. Any audit error is logged and a minimal report is returned
so the calling stream can never be broken by audit code.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


# Metric weights for compliance_rate (spec §5.3):
_WEIGHT_TITLE_MATCH      = 0.40
_WEIGHT_SECTION_COUNT    = 0.20
_WEIGHT_SEPARATOR        = 0.10
_WEIGHT_PARA_COMPLIANCE  = 0.30

# Separator is required to appear within the first 30% of the output.
_SEPARATOR_FIRST_N_PCT = 0.30

# Sentence splitter for paragraph-violation count (good-enough, not perfect).
_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")

# Header detector for markdown path.
_HEADER_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# Strip emoji + variation selectors + punctuation from a title for matching.
# Broad net: anything outside ASCII letters/digits/space gets dropped before
# case-folding. Keeps names simple and robust to emoji variants.
_TITLE_NONMATCH = re.compile(r"[^a-zA-Z0-9\s]+")


def audit_response(
    scaffold: Any,
    rendered_output: Optional[str],
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute a compliance report for a completed stream.

    Returns a dict matching the Firestore schema in spec §5.4.
    Never raises.
    """
    meta = meta or {}
    try:
        return _audit_inner(scaffold, rendered_output or "", meta)
    except Exception as e:
        logger.warning(f"[audit] unexpected error (swallowed): {e}")
        return _empty_report(scaffold, meta, error=str(e))


# ------------------------------------------------------------
# Core
# ------------------------------------------------------------


def _audit_inner(scaffold: Any, rendered: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    if scaffold is None:
        return _empty_report(scaffold, meta, error="scaffold_none")

    render_path = meta.get("render_path", "markdown")
    planned_titles = _planned_titles(scaffold)
    sections_planned = len(planned_titles)
    separator_planned = bool(getattr(scaffold, "separator", False))
    max_para = int(getattr(scaffold, "max_paragraph_sentences", 3))

    if render_path == "structured":
        data = meta.get("structured_data") or {}
        sections_rendered, title_match, para_violations = _measure_structured(
            data, planned_titles, max_para,
        )
        # JSON schema already guarantees shape → separator is N/A to audit
        separator_present = separator_planned
    else:
        sections_rendered, title_match = _measure_markdown_sections(
            rendered, planned_titles,
        )
        separator_present = _has_separator(rendered) if separator_planned else False
        para_violations = _count_paragraph_violations(rendered, max_para)

    metrics = {
        "sections_planned":  sections_planned,
        "sections_rendered": sections_rendered,
        "section_title_match": title_match,
        "separator_planned":  separator_planned,
        "separator_present":  separator_present,
        "max_paragraph_sentences_planned": max_para,
        "paragraph_violations": para_violations,
        "compliance_rate":     _compliance_rate(
            sections_planned, sections_rendered, title_match,
            separator_planned, separator_present,
            para_violations,
        ),
    }

    report = {
        "scaffold_planned": _scaffold_to_dict(scaffold),
        "render_path": render_path,
        "layout": getattr(scaffold, "layout", "unknown"),
        "fallback_reason": meta.get("fallback_reason"),
        "metrics": metrics,
        "output_length_chars": len(rendered),
    }
    return report


# ------------------------------------------------------------
# Measurements
# ------------------------------------------------------------


def _planned_titles(scaffold: Any) -> List[str]:
    sections = getattr(scaffold, "sections", None) or []
    titles: List[str] = []
    for s in sections:
        if isinstance(s, dict):
            titles.append(str(s.get("title", "")))
        else:
            titles.append(str(getattr(s, "title", "")))
    return titles


def _measure_markdown_sections(rendered: str, planned: List[str]) -> tuple:
    """Return (sections_rendered_count, title_match_count)."""
    headers = _HEADER_RE.findall(rendered)
    normalized_rendered = [_normalize_title(h) for h in headers]
    normalized_planned = [_normalize_title(p) for p in planned]
    title_match = sum(
        1 for p in normalized_planned
        if p and p in normalized_rendered
    )
    return len(headers), title_match


def _measure_structured(data: Dict[str, Any], planned: List[str],
                         max_para: int) -> tuple:
    """Return (sections_rendered_count, title_match_count, paragraph_violations)."""
    sections = data.get("sections") or []
    normalized_rendered = [
        _normalize_title(str(s.get("title", ""))) for s in sections
    ]
    normalized_planned = [_normalize_title(p) for p in planned]
    title_match = sum(
        1 for p in normalized_planned
        if p and p in normalized_rendered
    )
    violations = 0
    for s in sections:
        violations += _count_paragraph_violations(str(s.get("body", "")), max_para)
    return len(sections), title_match, violations


def _has_separator(rendered: str) -> bool:
    """True if `---` appears on its own line within the first 30% of content."""
    if not rendered.strip():
        return False
    cutoff = max(1, int(len(rendered) * _SEPARATOR_FIRST_N_PCT))
    head = rendered[:cutoff]
    return any(
        line.strip() == "---"
        for line in head.splitlines()
    )


def _count_paragraph_violations(text: str, max_sentences: int) -> int:
    """Count paragraphs whose sentence count exceeds max_sentences."""
    if not text.strip():
        return 0
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    violations = 0
    for para in paragraphs:
        # Skip paragraphs that are clearly headers/list items/separators.
        if para.startswith(("#", "-", "*", "1.", "2.", "3.", "4.", "5.", "---")):
            continue
        n = len([_ for _ in _SENTENCE_END.findall(para) if _])
        if n > max_sentences:
            violations += 1
    return violations


# ------------------------------------------------------------
# Compliance composite
# ------------------------------------------------------------


def _compliance_rate(sections_planned: int, sections_rendered: int,
                     title_match: int, separator_planned: bool,
                     separator_present: bool,
                     paragraph_violations: int) -> float:
    """Weighted 0.0-1.0 score per spec §5.3."""
    # Flat scaffolds (0 planned sections) skip section-related terms —
    # those terms would always be 1.0 anyway, but we re-weight so the
    # paragraph score carries the mass.
    if sections_planned == 0:
        # Prose/flat scaffold: only paragraph + separator (if planned) contribute.
        para_score = 1.0 if paragraph_violations == 0 else 0.0
        if separator_planned:
            sep_score = 1.0 if separator_present else 0.0
            return 0.5 * para_score + 0.5 * sep_score
        return para_score

    title_score = title_match / sections_planned if sections_planned else 1.0
    count_score = min(sections_rendered / sections_planned, 1.0) if sections_planned else 1.0
    sep_score = 1.0
    if separator_planned:
        sep_score = 1.0 if separator_present else 0.0
    # Paragraph compliance: 1.0 with 0 violations, decays per violation.
    para_score = max(0.0, 1.0 - 0.25 * paragraph_violations)
    return (
        _WEIGHT_TITLE_MATCH    * title_score
        + _WEIGHT_SECTION_COUNT  * count_score
        + _WEIGHT_SEPARATOR      * sep_score
        + _WEIGHT_PARA_COMPLIANCE * para_score
    )


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------


def _normalize_title(s: str) -> str:
    """Lowercase, strip emoji + punctuation, collapse whitespace."""
    s = _TITLE_NONMATCH.sub(" ", s)
    return " ".join(s.strip().lower().split())


def _scaffold_to_dict(scaffold: Any) -> Optional[Dict[str, Any]]:
    if hasattr(scaffold, "to_dict"):
        try:
            return scaffold.to_dict()
        except Exception:
            pass
    return None


def _empty_report(scaffold: Any, meta: Dict[str, Any], error: str = "") -> Dict[str, Any]:
    return {
        "scaffold_planned": _scaffold_to_dict(scaffold),
        "render_path": meta.get("render_path", "unknown"),
        "layout": getattr(scaffold, "layout", "unknown") if scaffold else "unknown",
        "fallback_reason": meta.get("fallback_reason"),
        "metrics": {
            "sections_planned": 0, "sections_rendered": 0,
            "section_title_match": 0,
            "separator_planned": False, "separator_present": False,
            "max_paragraph_sentences_planned": 0,
            "paragraph_violations": 0,
            "compliance_rate": 0.0,
        },
        "output_length_chars": 0,
        "error": error or None,
    }

"""W-17, W-18 — scaffold_auditor.audit_response behavior.

Parses completed stream output against planned Scaffold, computes
compliance metrics. See spec §5.3.
"""
from app.services.chat_engine import Scaffold
from app.services.scaffold_auditor import audit_response


def _structured_scaffold(n: int = 3, separator: bool = True,
                          max_para: int = 3) -> Scaffold:
    return Scaffold(
        layout="structured", opening="hook", separator=separator,
        sections=[
            {"title": f"Section {i}", "emoji": "", "style": "prose"}
            for i in range(n)
        ],
        emphasis="none", max_paragraph_sentences=max_para,
        list_threshold=3, width_hint="medium",
    )


def _flat_scaffold() -> Scaffold:
    return Scaffold(
        layout="prose", opening="direct", separator=False,
        sections=[], emphasis="none",
        max_paragraph_sentences=3, list_threshold=3, width_hint="medium",
    )


# ============================================================
# W-17 — markdown parser
# ============================================================


def test_auditor_counts_section_headers_from_markdown():
    scaffold = _structured_scaffold(3)
    rendered = (
        "Here's the overview.\n\n"
        "---\n\n"
        "## Section 0\n\nBody one.\n\n"
        "## Section 1\n\nBody two.\n\n"
        "## Section 2\n\nBody three.\n"
    )
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["sections_planned"] == 3
    assert r["metrics"]["sections_rendered"] == 3


def test_auditor_title_match_is_case_insensitive():
    scaffold = _structured_scaffold(2)
    rendered = "## SECTION 0\n\nBody.\n\n## section 1\n\nBody.\n"
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["section_title_match"] == 2


def test_auditor_title_match_ignores_emoji_prefix():
    scaffold = _structured_scaffold(2)
    rendered = "## 🎯 Section 0\n\nBody.\n\n## ⚖️ Section 1\n\nBody.\n"
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["section_title_match"] == 2


def test_auditor_separator_detected_in_first_third():
    scaffold = _structured_scaffold(1)
    # Separator appears at ~15% — within first 30%.
    rendered = "Opening paragraph.\n\n---\n\n## Section 0\n\n" + ("Body. " * 50)
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["separator_planned"] is True
    assert r["metrics"]["separator_present"] is True


def test_auditor_separator_not_present_when_missing():
    scaffold = _structured_scaffold(1)
    rendered = "## Section 0\n\nBody here.\n"
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["separator_present"] is False


def test_auditor_paragraph_violations_counted():
    scaffold = _flat_scaffold()  # max_paragraph_sentences=3
    # One 5-sentence paragraph = 1 violation.
    rendered = "A. B. C. D. E.\n\nOK single.\n\nOne. Two.\n"
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["paragraph_violations"] == 1


def test_auditor_compliance_rate_is_1_on_perfect_match():
    scaffold = _structured_scaffold(2, separator=True)
    rendered = (
        "Opening here.\n\n"
        "---\n\n"
        "## Section 0\n\nShort. Body. Here.\n\n"
        "## Section 1\n\nAnother. One. Body.\n"
    )
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["compliance_rate"] == 1.0


def test_auditor_compliance_rate_drops_when_sections_missing():
    scaffold = _structured_scaffold(3)
    rendered = "## Section 0\n\nBody only.\n"
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["compliance_rate"] < 0.6


def test_auditor_handles_empty_output():
    scaffold = _structured_scaffold(2)
    r = audit_response(scaffold, "", meta={"render_path": "markdown"})
    assert r["metrics"]["sections_rendered"] == 0
    assert r["metrics"]["compliance_rate"] < 0.5


def test_auditor_flat_scaffold_ignores_section_checks():
    """Prose layout with 0 planned sections — section metrics should not
    penalize output that happens to have no headers."""
    scaffold = _flat_scaffold()
    rendered = "This is just three sentences. Of prose. That's fine.\n"
    r = audit_response(scaffold, rendered, meta={"render_path": "markdown"})
    assert r["metrics"]["sections_planned"] == 0
    assert r["metrics"]["compliance_rate"] >= 0.9


# ============================================================
# W-18 — structured-path short-circuit
# ============================================================


def test_auditor_structured_path_uses_json_not_regex():
    scaffold = _structured_scaffold(2, separator=True)
    json_data = {
        "opening": "Quick overview.",
        "sections": [
            {"title": "Section 0", "emoji": "", "body": "One. Two. Three.", "style": "prose"},
            {"title": "Section 1", "emoji": "", "body": "Body only.", "style": "prose"},
        ],
        "closing": "",
    }
    r = audit_response(
        scaffold, "",  # empty string — structured path doesn't read rendered
        meta={"render_path": "structured", "structured_data": json_data},
    )
    assert r["metrics"]["sections_rendered"] == 2
    assert r["metrics"]["section_title_match"] == 2


def test_auditor_structured_path_counts_paragraph_violations_per_body():
    scaffold = _structured_scaffold(1, max_para=3)
    json_data = {
        "opening": "",
        "sections": [
            {"title": "Section 0", "emoji": "",
             "body": "One. Two. Three. Four. Five.",  # 5 sentences > 3 = 1 violation
             "style": "prose"},
        ],
        "closing": "",
    }
    r = audit_response(
        scaffold, "",
        meta={"render_path": "structured", "structured_data": json_data},
    )
    assert r["metrics"]["paragraph_violations"] == 1


def test_auditor_records_fallback_reason_when_present():
    scaffold = _structured_scaffold(2)
    r = audit_response(
        scaffold, "## Section 0\n\nBody.\n",
        meta={"render_path": "markdown_fallback",
              "fallback_reason": "schema_or_json_invalid"},
    )
    assert r["render_path"] == "markdown_fallback"
    assert r["fallback_reason"] == "schema_or_json_invalid"


def test_auditor_never_raises_on_malformed_input():
    """Auditor must be swallow-all-errors — audit failure can't break
    the stream that already completed."""
    r = audit_response(None, None, meta={})
    assert isinstance(r, dict)
    assert "metrics" in r

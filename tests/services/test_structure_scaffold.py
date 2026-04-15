"""Tests for Scaffold dataclass and serialization helpers (spec §2, §5)."""
import pytest
from app.services.chat_engine import Scaffold


ALLOWED_LAYOUTS = {
    "conversational", "prose", "long-prose", "technical",
    "code-heavy", "list-heavy", "structured",
}


def test_scaffold_construct_valid():
    s = Scaffold(
        layout="long-prose",
        opening="hook",
        separator=False,
        sections=[],
        emphasis="italic",
        max_paragraph_sentences=3,
        list_threshold=3,
        width_hint="narrow",
    )
    assert s.layout == "long-prose"
    assert s.opening == "hook"
    assert s.separator is False
    assert s.max_paragraph_sentences == 3
    assert s.width_hint == "narrow"


def test_scaffold_rejects_unknown_layout():
    with pytest.raises(ValueError, match="layout"):
        Scaffold(
            layout="bogus",
            opening="direct", separator=False, sections=[],
            emphasis="none", max_paragraph_sentences=3,
            list_threshold=3, width_hint="medium",
        )


def test_scaffold_to_dict_roundtrip():
    s = Scaffold(
        layout="technical", opening="direct", separator=False,
        sections=[{"title": "Setup", "emoji": "⚙️", "style": "steps"}],
        emphasis="bold", max_paragraph_sentences=3,
        list_threshold=3, width_hint="wide",
    )
    d = s.to_dict()
    assert d["layout"] == "technical"
    assert d["sections"][0]["title"] == "Setup"
    s2 = Scaffold.from_dict(d)
    assert s2 == s


from app.services.chat_engine import scaffold_to_instruction


def test_instruction_for_conversational_scaffold_is_empty_or_minimal():
    """A conversational scaffold should produce no heavy formatting directives."""
    s = Scaffold(
        layout="conversational", opening="direct", separator=False,
        sections=[], emphasis="none", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "section" not in out.lower()
    assert "---" not in out  # no separator
    assert "Answer in the first sentence" in out


def test_instruction_for_structured_scaffold_includes_sections():
    s = Scaffold(
        layout="structured", opening="hook", separator=True,
        sections=[
            {"title": "Core Idea", "emoji": "🎯", "style": "prose"},
            {"title": "Tradeoffs",  "emoji": "⚖️", "style": "list"},
        ],
        emphasis="bold", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "RESPONSE FORMAT INSTRUCTIONS" in out
    assert "## 🎯 Core Idea" in out
    assert "## ⚖️ Tradeoffs" in out
    assert "---" in out
    assert "Open with 1-2 sentences" in out
    assert "Maximum 3 sentences per paragraph" in out
    assert "Bold exactly one key phrase" in out


def test_instruction_italic_emphasis_excludes_bold_directive():
    s = Scaffold(
        layout="prose", opening="direct", separator=False,
        sections=[], emphasis="italic", max_paragraph_sentences=3,
        list_threshold=3, width_hint="narrow",
    )
    out = scaffold_to_instruction(s)
    assert "italic" in out.lower()
    assert "Bold exactly" not in out


def test_instruction_section_with_empty_emoji_has_no_double_space():
    """Bug regression: empty emoji must not produce double space."""
    s = Scaffold(
        layout="structured", opening="direct", separator=False,
        sections=[{"title": "Core Idea", "emoji": "", "style": "prose"}],
        emphasis="none", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "##  " not in out  # no double space after ##
    assert "## Core Idea" in out


def test_instruction_opening_none_produces_no_opening_directive():
    """Valid 'none' opening means no opening directive in the output."""
    s = Scaffold(
        layout="prose", opening="none", separator=False,
        sections=[], emphasis="none", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "Open with" not in out
    assert "Answer in the first sentence" not in out
    assert "Open with a single clarifying question" not in out
    # But other directives still emit:
    assert "Maximum 3 sentences per paragraph" in out

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


from app.services.chat_engine import _scaffold_from_task_type


def test_fallback_coding_task_maps_to_technical_wide():
    s = _scaffold_from_task_type("coding")
    assert s.layout == "technical"
    assert s.width_hint == "wide"
    assert s.opening == "direct"


def test_fallback_reasoning_task_maps_to_prose_medium():
    s = _scaffold_from_task_type("reasoning")
    assert s.layout == "prose"
    assert s.width_hint == "medium"
    assert s.opening == "hook"


def test_fallback_long_context_maps_to_long_prose_narrow():
    s = _scaffold_from_task_type("long_context")
    assert s.layout == "long-prose"
    assert s.width_hint == "narrow"


def test_fallback_unknown_task_returns_conversational_default():
    s = _scaffold_from_task_type("definitely-not-a-real-task-type")
    assert s.layout == "conversational"
    assert s.opening == "direct"


def test_fallback_never_returns_none_for_any_registered_task():
    """Per spec §4: fallback must always return a fully populated Scaffold."""
    for task in ("coding", "reasoning", "vision", "image_gen",
                 "uncensored", "long_context", "chat"):
        s = _scaffold_from_task_type(task)
        assert s is not None
        assert s.layout in {
            "conversational", "prose", "long-prose", "technical",
            "code-heavy", "list-heavy", "structured",
        }


from unittest.mock import patch, MagicMock
from app.services import chat_engine


def _mk_openai_response(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": __import__("json").dumps(body)}}]}
    return resp


def test_classify_returns_scaffold_when_llm_succeeds(monkeypatch):
    chat_engine._intent_cache.clear()
    fake_body = {
        "intent_type": "creative_task",
        "has_crypto_mention": False,
        "has_stock_mention": False,
        "asset_symbol": None,
        "wants_chart": False,
        "wants_image": False,
        "wants_code": False,
        "scaffold": {
            "layout": "prose", "opening": "hook", "separator": False,
            "sections": [], "emphasis": "italic",
            "max_paragraph_sentences": 3, "list_threshold": 3,
            "width_hint": "narrow",
        },
    }
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mk_openai_response(fake_body)
        result = chat_engine.classify_user_intent("write me a haiku about rain", use_cache=False)
    assert result["intent_type"] == "creative_task"
    assert "scaffold" in result
    assert isinstance(result["scaffold"], dict)
    assert result["scaffold"]["layout"] == "prose"
    assert result["scaffold"]["width_hint"] == "narrow"


def test_classify_falls_back_to_task_type_default_on_llm_failure(monkeypatch):
    chat_engine._intent_cache.clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = chat_engine.classify_user_intent("write a python script", use_cache=False)
    # No LLM available → scaffold is still populated via task-type fallback.
    assert "scaffold" in result
    assert result["scaffold"] is not None
    assert result["scaffold"]["layout"] in {
        "conversational", "prose", "long-prose", "technical",
        "code-heavy", "list-heavy", "structured",
    }


def test_classify_scaffold_validates_before_returning(monkeypatch):
    """If the LLM returns an invalid layout, fall back — never propagate garbage."""
    chat_engine._intent_cache.clear()
    bad_body = {
        "intent_type": "general_chat",
        "scaffold": {
            "layout": "spaghetti",  # invalid
            "opening": "direct", "separator": False, "sections": [],
            "emphasis": "none", "max_paragraph_sentences": 3,
            "list_threshold": 3, "width_hint": "medium",
        },
    }
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mk_openai_response(bad_body)
        result = chat_engine.classify_user_intent("hey", use_cache=False)
    assert result["scaffold"]["layout"] != "spaghetti"
    assert result["scaffold"]["layout"] in {
        "conversational", "prose", "long-prose", "technical",
        "code-heavy", "list-heavy", "structured",
    }

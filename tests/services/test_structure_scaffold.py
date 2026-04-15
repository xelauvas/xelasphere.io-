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
    assert "STRUCTURAL CONTRACT" in out
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


# ============================================================
# A — Skeleton-based instructions for structured layouts
# ============================================================

def test_instruction_structured_emits_markdown_skeleton_not_rule_list():
    """When sections are provided, the instruction should contain a literal
    markdown skeleton the model fills in (spec §5 enforcement upgrade)."""
    s = Scaffold(
        layout="structured", opening="hook", separator=True,
        sections=[
            {"title": "History",   "emoji": "📜", "style": "prose"},
            {"title": "Intuition", "emoji": "💡", "style": "prose"},
            {"title": "Mechanism", "emoji": "⚙️", "style": "prose"},
        ],
        emphasis="bold", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    # Literal response template the model fills in:
    assert "RESPONSE TEMPLATE" in out or "Fill this exact template" in out
    # All three section headers appear as actual markdown headers in the
    # template body (not just as a rule list):
    assert out.count("## ") >= 3
    assert "## 📜 History" in out
    assert "## 💡 Intuition" in out
    assert "## ⚙️ Mechanism" in out


def test_instruction_unstructured_layout_skips_skeleton():
    """Conversational / prose without sections must NOT emit a skeleton."""
    s = Scaffold(
        layout="conversational", opening="direct", separator=False,
        sections=[], emphasis="none", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )
    out = scaffold_to_instruction(s)
    assert "RESPONSE TEMPLATE" not in out
    assert "Fill this exact template" not in out


# ============================================================
# B — Deterministic signal layer (pre-LLM prompt analysis)
# ============================================================

from app.services.chat_engine import analyze_prompt_signals


def test_signals_detects_multi_angle_explainer():
    sig = analyze_prompt_signals(
        "write a deep explainer on how transformer attention works, "
        "including history and intuition"
    )
    assert sig["force_layout"] == "structured"
    # inferred sections pulled from the conjunction targets:
    titles = [s["title"].lower() for s in sig["inferred_sections"]]
    assert any("history" in t for t in titles)
    assert any("intuition" in t for t in titles)


def test_signals_detects_three_angle_comparison():
    sig = analyze_prompt_signals(
        "compare React, Vue, and Svelte across performance, learning curve, and ecosystem"
    )
    assert sig["force_layout"] == "structured"
    assert len(sig["inferred_sections"]) >= 3


def test_signals_leaves_short_conversational_alone():
    sig = analyze_prompt_signals("hey")
    assert sig.get("force_layout") is None
    assert sig.get("inferred_sections", []) == []


def test_signals_does_not_false_positive_on_simple_and():
    """A prompt with a single 'and' but no explainer framing stays free."""
    sig = analyze_prompt_signals("give me salt and pepper recipes")
    assert sig.get("force_layout") != "structured"


def test_signals_biases_code_prompts_to_technical():
    sig = analyze_prompt_signals("write a python function to parse CSV files")
    assert sig.get("force_layout") in ("technical", "code-heavy", None)
    # Not forcing prose/structured for code:
    assert sig.get("force_layout") != "structured"


# ============================================================
# A+B integration — classifier respects forced signals
# ============================================================

def test_classify_signals_override_weak_llm_layout(monkeypatch):
    """If deterministic signals say 'structured' but LLM says 'long-prose',
    the signals win. This is the v1.1 intelligence upgrade."""
    chat_engine._intent_cache.clear()
    bad_body = {
        "intent_type": "question",
        "scaffold": {
            "layout": "long-prose",  # LLM guess — should be overridden
            "opening": "direct", "separator": False, "sections": [],
            "emphasis": "none", "max_paragraph_sentences": 3,
            "list_threshold": 3, "width_hint": "narrow",
        },
    }
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mk_openai_response(bad_body)
        result = chat_engine.classify_user_intent(
            "write a deep explainer on transformer attention, "
            "including history and intuition",
            use_cache=False,
        )
    assert result["scaffold"]["layout"] == "structured"
    assert len(result["scaffold"]["sections"]) >= 2


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

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

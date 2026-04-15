"""W-21 + W-22 — SSE event wiring.

W-21: render_mode event fires after scaffold, before first content token.
W-22: structured event carries the generator's JSON payload; no token
events on the structured path.
"""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------- helpers ----------


def _valid_structured_payload():
    return {
        "opening": "Quick overview.",
        "sections": [
            {"title": "History",   "emoji": "", "body": "It began in 2017.", "style": "prose"},
            {"title": "Intuition", "emoji": "", "body": "Think about focus.", "style": "prose"},
        ],
        "closing": "",
    }


def _scaffold_with_sections():
    return {
        "layout": "structured", "opening": "hook", "separator": True,
        "sections": [
            {"title": "History",   "emoji": "", "style": "prose"},
            {"title": "Intuition", "emoji": "", "style": "prose"},
        ],
        "emphasis": "none", "max_paragraph_sentences": 3,
        "list_threshold": 3, "width_hint": "medium",
    }


# ---------- W-21 — render_mode event ----------


def test_maybe_emit_render_mode_returns_structured_when_sections_present():
    from app.blueprints.chat_routes import _render_mode_for_scaffold
    mode = _render_mode_for_scaffold(_scaffold_with_sections())
    assert mode == "structured"


def test_maybe_emit_render_mode_returns_markdown_for_flat_scaffold():
    from app.blueprints.chat_routes import _render_mode_for_scaffold
    flat = {**_scaffold_with_sections(), "sections": []}
    assert _render_mode_for_scaffold(flat) == "markdown"


def test_maybe_emit_render_mode_returns_markdown_when_no_scaffold():
    from app.blueprints.chat_routes import _render_mode_for_scaffold
    assert _render_mode_for_scaffold(None) == "markdown"


# ---------- W-22 — structured SSE path ----------


def test_structured_path_helper_yields_structured_chunk_and_done(monkeypatch):
    """The pre-flight helper runs the structured generator and, on success,
    yields (in order): scaffold, render_mode=structured, structured, done."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr

    # Patch the generator to return a successful structured chunk:
    from app.services.structured_generator import stream_structured_response as _real
    def fake_gen(*_, **__):
        yield {"type": "structured", "data": _valid_structured_payload()}
    with patch("app.blueprints.chat_routes.stream_structured_response", side_effect=fake_gen):
        events = list(cr._try_structured_stream(
            scaffold=_scaffold_with_sections(),
            prompt="deep explainer on transformer attention",
            model="gpt-4.1-mini",
            system_prompt_override=None,
        ))

    # Must yield at least: scaffold (pre-stream invariant), render_mode, structured, done.
    event_names = [e["event"] for e in events]
    assert "scaffold" in event_names
    assert "render_mode" in event_names
    assert "structured" in event_names
    assert "done" in event_names
    # Order: scaffold first, structured before done.
    assert event_names.index("scaffold") < event_names.index("render_mode")
    assert event_names.index("structured") < event_names.index("done")


def test_structured_path_helper_returns_fallback_sentinel_on_markdown_fallback(monkeypatch):
    """When the generator falls back, helper returns None so caller proceeds
    to the normal markdown-path generator."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr

    def fake_gen(*_, **__):
        yield {"type": "fallback_markdown", "reason": "schema_invalid"}
    with patch("app.blueprints.chat_routes.stream_structured_response", side_effect=fake_gen):
        events = list(cr._try_structured_stream(
            scaffold=_scaffold_with_sections(),
            prompt="p", model="gpt-4.1-mini", system_prompt_override=None,
        ))
    # Fallback: no events emitted — caller uses the normal path.
    assert events == []


def test_structured_path_skipped_when_scaffold_has_no_sections(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    flat = {**_scaffold_with_sections(), "sections": []}
    with patch("app.blueprints.chat_routes.stream_structured_response") as mock_gen:
        events = list(cr._try_structured_stream(
            scaffold=flat, prompt="p", model="m", system_prompt_override=None,
        ))
    assert mock_gen.call_count == 0
    assert events == []


def test_structured_path_skipped_when_flag_off(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "false")
    from app.blueprints import chat_routes as cr
    with patch("app.blueprints.chat_routes.stream_structured_response") as mock_gen:
        events = list(cr._try_structured_stream(
            scaffold=_scaffold_with_sections(),
            prompt="p", model="m", system_prompt_override=None,
        ))
    assert mock_gen.call_count == 0
    assert events == []


def test_structured_event_payload_contains_the_parsed_json(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    payload = _valid_structured_payload()
    def fake_gen(*_, **__):
        yield {"type": "structured", "data": payload}
    with patch("app.blueprints.chat_routes.stream_structured_response", side_effect=fake_gen):
        events = list(cr._try_structured_stream(
            scaffold=_scaffold_with_sections(),
            prompt="p", model="gpt-4.1-mini", system_prompt_override=None,
        ))
    structured_event = next(e for e in events if e["event"] == "structured")
    assert structured_event["data"]["data"] == payload


def test_no_token_events_on_structured_path(monkeypatch):
    """Structured path delivers one buffered JSON; never emits 'content' or 'token' events."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    def fake_gen(*_, **__):
        yield {"type": "structured", "data": _valid_structured_payload()}
    with patch("app.blueprints.chat_routes.stream_structured_response", side_effect=fake_gen):
        events = list(cr._try_structured_stream(
            scaffold=_scaffold_with_sections(),
            prompt="p", model="gpt-4.1-mini", system_prompt_override=None,
        ))
    event_names = [e["event"] for e in events]
    assert "content" not in event_names
    assert "token" not in event_names

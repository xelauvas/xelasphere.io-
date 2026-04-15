"""W-15, W-16 — stream_structured_response behavior.

W-15: OpenAI native json_schema path — happy + single-chunk shape.
W-16: Groq/DeepSeek json_mode fallback, retry once, fallback to markdown.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import structured_generator as sg
from app.services.chat_engine import Scaffold


def _scaffold_with_sections(n: int = 2) -> Scaffold:
    return Scaffold(
        layout="structured", opening="hook", separator=True,
        sections=[
            {"title": f"Section {i}", "emoji": "", "style": "prose"}
            for i in range(n)
        ],
        emphasis="none", max_paragraph_sentences=3,
        list_threshold=3, width_hint="medium",
    )


def _valid_payload(n: int = 2) -> dict:
    return {
        "opening": "Quick overview.",
        "sections": [
            {"title": f"Section {i}", "emoji": "",
             "body": f"Body paragraph for section {i}.", "style": "prose"}
            for i in range(n)
        ],
        "closing": "",
    }


def _mock_response(payload_or_content, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    if isinstance(payload_or_content, dict):
        content = json.dumps(payload_or_content)
    else:
        content = payload_or_content
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# ============================================================
# W-15 — OpenAI native json_schema path
# ============================================================


def test_generator_native_schema_path_returns_single_structured_chunk(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    scaffold = _scaffold_with_sections(3)
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_response(_valid_payload(3))
        chunks = list(sg.stream_structured_response(
            prompt="anything",
            model="gpt-4.1-mini",
            scaffold=scaffold,
        ))
    assert len(chunks) == 1
    assert chunks[0]["type"] == "structured"
    assert len(chunks[0]["data"]["sections"]) == 3


def test_generator_native_schema_uses_json_schema_response_format(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    scaffold = _scaffold_with_sections(2)
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_response(_valid_payload(2))
        list(sg.stream_structured_response(
            prompt="anything", model="gpt-4.1-mini", scaffold=scaffold,
        ))
    body = mock_req.post.call_args.kwargs.get("json", {})
    assert body.get("response_format", {}).get("type") == "json_schema"


def test_generator_falls_back_on_http_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    scaffold = _scaffold_with_sections(2)
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_response({"error": "boom"}, status=500)
        chunks = list(sg.stream_structured_response(
            prompt="q", model="gpt-4.1-mini", scaffold=scaffold,
        ))
    assert len(chunks) == 1
    assert chunks[0]["type"] == "fallback_markdown"
    assert "reason" in chunks[0]


# ============================================================
# W-16 — JSON-mode fallback, retry, markdown fallthrough
# ============================================================


def test_generator_json_mode_path_validates_output(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    scaffold = _scaffold_with_sections(2)
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_response(_valid_payload(2))
        chunks = list(sg.stream_structured_response(
            prompt="q",
            model="llama-3.3-70b-versatile",
            scaffold=scaffold,
        ))
    assert chunks[0]["type"] == "structured"
    # Must have used json_object response_format (not json_schema):
    body = mock_req.post.call_args.kwargs.get("json", {})
    assert body.get("response_format", {}).get("type") == "json_object"


def test_generator_retries_once_on_invalid_json(monkeypatch):
    """First response malformed → retry → valid → structured chunk."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    scaffold = _scaffold_with_sections(2)
    bad = _mock_response("not valid json at all")
    good = _mock_response(_valid_payload(2))
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.side_effect = [bad, good]
        chunks = list(sg.stream_structured_response(
            prompt="q",
            model="llama-3.3-70b-versatile",
            scaffold=scaffold,
        ))
    assert mock_req.post.call_count == 2
    assert chunks[0]["type"] == "structured"


def test_generator_falls_through_to_markdown_after_two_failures(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    scaffold = _scaffold_with_sections(2)
    bad = _mock_response("still garbage")
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.side_effect = [bad, bad]
        chunks = list(sg.stream_structured_response(
            prompt="q",
            model="llama-3.3-70b-versatile",
            scaffold=scaffold,
        ))
    assert mock_req.post.call_count == 2
    assert chunks[0]["type"] == "fallback_markdown"


def test_generator_falls_through_when_provider_unsupported(monkeypatch):
    """Unknown provider → no JSON mode → immediate markdown fallback,
    no network calls."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    scaffold = _scaffold_with_sections(2)
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        chunks = list(sg.stream_structured_response(
            prompt="q", model="deep-unknown-model", scaffold=scaffold,
        ))
    assert mock_req.post.call_count == 0
    assert chunks[0]["type"] == "fallback_markdown"


def test_generator_passes_system_prompt_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    scaffold = _scaffold_with_sections(2)
    with patch.object(sg, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_response(_valid_payload(2))
        list(sg.stream_structured_response(
            prompt="user q",
            model="gpt-4.1-mini",
            scaffold=scaffold,
            system_prompt_override="CUSTOM SYSTEM PREFIX",
        ))
    body = mock_req.post.call_args.kwargs.get("json", {})
    sys_msg = next(m for m in body["messages"] if m["role"] == "system")
    assert "CUSTOM SYSTEM PREFIX" in sys_msg["content"]

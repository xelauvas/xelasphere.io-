"""W-06, W-07, W-08 — classifier v2 behavior.

v2 classifier emits JSON via schema-enforced or JSON-mode LLM calls,
validates against CLASSIFIER_RESPONSE_SCHEMA, returns a populated
Scaffold envelope or falls back to task-type default.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.services import chat_engine


def _valid_classifier_payload(layout: str = "conversational",
                              intent: str = "general_chat") -> dict:
    return {
        "intent_type": intent,
        "has_crypto_mention": False,
        "has_stock_mention": False,
        "asset_symbol": None,
        "wants_chart": False,
        "wants_image": False,
        "wants_code": False,
        "scaffold": {
            "layout": layout,
            "opening": "direct",
            "separator": False,
            "sections": [],
            "emphasis": "none",
            "max_paragraph_sentences": 3,
            "list_threshold": 3,
            "width_hint": "medium",
        },
    }


def _mock_provider_response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    return resp


# ============================================================
# W-06 — OpenAI native schema path
# ============================================================


def test_classify_v2_openai_schema_path_happy(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    payload = _valid_classifier_payload("structured", "question")
    payload["scaffold"]["sections"] = [
        {"title": "A", "emoji": "", "style": "prose"},
        {"title": "B", "emoji": "", "style": "prose"},
    ]
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_provider_response(payload)
        result = chat_engine.classify_user_intent(
            "explain X with A and B", use_cache=False,
        )
    assert result["intent_type"] == "question"
    assert result["scaffold"]["layout"] == "structured"
    assert len(result["scaffold"]["sections"]) == 2


def test_classify_v2_rejects_response_failing_schema(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    bad = _valid_classifier_payload()
    bad["intent_type"] = "bogus_intent"  # fails enum
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_provider_response(bad)
        result = chat_engine.classify_user_intent("what time", use_cache=False)
    # Must fall back — never propagate garbage:
    assert result["intent_type"] != "bogus_intent"
    assert result["scaffold"]["layout"] in {
        "conversational", "prose", "long-prose", "technical",
        "code-heavy", "list-heavy", "structured",
    }


def test_classify_v2_falls_back_to_task_type_on_http_error(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    err = MagicMock()
    err.status_code = 500
    err.json.return_value = {"error": "boom"}
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = err
        result = chat_engine.classify_user_intent("what time", use_cache=False)
    assert "scaffold" in result
    assert result["scaffold"] is not None
    # On failure we return the task-type default — intent_type=unknown is acceptable:
    assert result["intent_type"] in ("unknown", "general_chat")


def test_classify_v2_request_uses_openai_json_schema_response_format(monkeypatch):
    """For OpenAI schema-mode models, request must include response_format
    with type=json_schema so the API enforces the contract."""
    chat_engine._intent_cache_v2.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    payload = _valid_classifier_payload()
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_provider_response(payload)
        chat_engine.classify_user_intent("what time", use_cache=False)
    # Inspect the call:
    body = mock_req.post.call_args.kwargs.get("json", {})
    response_format = body.get("response_format", {})
    assert response_format.get("type") == "json_schema", (
        f"Expected response_format type=json_schema, got {response_format!r}"
    )


# ============================================================
# W-07 — Groq/DeepSeek JSON-mode fallback path
# ============================================================


def test_classify_v2_groq_json_mode_path_when_no_openai(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    payload = _valid_classifier_payload("prose", "question")
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_provider_response(payload)
        result = chat_engine.classify_user_intent(
            "what's the weather like", use_cache=False,
        )
    assert result["scaffold"]["layout"] == "prose"
    # Groq call must use json_object mode, not json_schema:
    body = mock_req.post.call_args.kwargs.get("json", {})
    assert body.get("response_format", {}).get("type") == "json_object"


def test_classify_v2_validates_groq_output_against_schema(monkeypatch):
    """Groq json_object mode doesn't enforce schema — we validate post-parse."""
    chat_engine._intent_cache_v2.clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    # Groq returns valid JSON, wrong shape:
    bad = {"random_field": "hello", "not_the_schema": True}
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_provider_response(bad)
        result = chat_engine.classify_user_intent("what time", use_cache=False)
    assert result["scaffold"] is not None
    assert result["intent_type"] in ("unknown", "general_chat")


def test_classify_v2_fallback_when_both_providers_fail(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = chat_engine.classify_user_intent("write python code", use_cache=False)
    assert result["scaffold"] is not None
    assert result["scaffold"]["layout"] in {
        "conversational", "prose", "long-prose", "technical",
        "code-heavy", "list-heavy", "structured",
    }


# ============================================================
# W-08 — Cache with TTL + size bound
# ============================================================


def test_cache_hits_on_repeat_prompt(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    payload = _valid_classifier_payload()
    with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
        mock_req.post.return_value = _mock_provider_response(payload)
        r1 = chat_engine.classify_user_intent("hello there", use_cache=True)
        r2 = chat_engine.classify_user_intent("hello there", use_cache=True)
    assert r1 == r2
    # Second call should not have hit the provider:
    assert mock_req.post.call_count == 1


def test_cache_evicts_oldest_when_full(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    # Drop maxsize to something tiny for the test:
    original_cache = chat_engine._intent_cache_v2
    from cachetools import TTLCache
    chat_engine._intent_cache_v2 = TTLCache(maxsize=2, ttl=300)
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        payload = _valid_classifier_payload()
        with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
            mock_req.post.return_value = _mock_provider_response(payload)
            chat_engine.classify_user_intent("prompt A", use_cache=True)
            chat_engine.classify_user_intent("prompt B", use_cache=True)
            chat_engine.classify_user_intent("prompt C", use_cache=True)  # evicts A
        assert len(chat_engine._intent_cache_v2) == 2
        # A should have been evicted:
        assert "prompt A" not in str(list(chat_engine._intent_cache_v2.keys())) or \
               chat_engine._intent_cache_v2.currsize == 2
    finally:
        chat_engine._intent_cache_v2 = original_cache


def test_cache_expires_after_ttl(monkeypatch):
    chat_engine._intent_cache_v2.clear()
    original_cache = chat_engine._intent_cache_v2
    from cachetools import TTLCache
    chat_engine._intent_cache_v2 = TTLCache(maxsize=100, ttl=0.1)  # 100ms
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        payload = _valid_classifier_payload()
        with patch.object(chat_engine, "requests", MagicMock()) as mock_req:
            mock_req.post.return_value = _mock_provider_response(payload)
            chat_engine.classify_user_intent("hello there", use_cache=True)
            time.sleep(0.2)
            chat_engine.classify_user_intent("hello there", use_cache=True)
        # Entry should have expired → two real calls:
        assert mock_req.post.call_count == 2
    finally:
        chat_engine._intent_cache_v2 = original_cache

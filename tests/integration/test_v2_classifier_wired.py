"""W-09 — classifier v2 wired into /chat/stream behind SCAFFOLD_V2_ENABLED.

Spec §9: flag off → no classifier call from the stream route, no scaffold
log line. Flag on → classifier runs once, scaffold logged with layout.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest


# ---- scope to integration; opt out of markers that slow CI on this file
pytestmark = pytest.mark.integration


def test_flag_off_stream_does_not_call_v2_classifier(monkeypatch):
    """With flag off, the stream route must not invoke the v2 classifier
    from any scaffold-injection code path. (It may still call the v1-legacy
    classifier for unrelated recipe detection — we only assert the
    scaffold-specific call site is inactive.)"""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "false")

    from app.blueprints import chat_routes as cr
    # Look for a 'maybe_inject_scaffold_v2' function or similar gated helper.
    # It must be callable and must no-op when flag is off.
    assert hasattr(cr, "_maybe_classify_and_inject_scaffold"), (
        "chat_routes must expose _maybe_classify_and_inject_scaffold() — "
        "the single gate-point for the v2 scaffold injection."
    )

    fake_prompt = "tell me about quantum physics"
    with patch("app.blueprints.chat_routes.classify_user_intent") as mock_clf:
        mock_clf.return_value = {"scaffold": {"layout": "prose"}, "intent_type": "question"}
        result = cr._maybe_classify_and_inject_scaffold(
            base_system="You are XelaSphere.",
            user_prompt=fake_prompt,
        )
    # Flag off: no classifier call, no system-prompt mutation.
    assert mock_clf.call_count == 0
    assert result["base_system"] == "You are XelaSphere."
    assert result["scaffold"] is None


def test_flag_on_stream_calls_v2_classifier_and_logs_layout(monkeypatch, caplog):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")

    from app.blueprints import chat_routes as cr
    fake_envelope = {
        "intent_type": "question",
        "scaffold": {
            "layout": "structured",
            "opening": "hook",
            "separator": True,
            "sections": [
                {"title": "A", "emoji": "", "style": "prose"},
                {"title": "B", "emoji": "", "style": "prose"},
            ],
            "emphasis": "none",
            "max_paragraph_sentences": 3,
            "list_threshold": 3,
            "width_hint": "medium",
        },
    }
    import logging
    with patch("app.blueprints.chat_routes.classify_user_intent") as mock_clf:
        mock_clf.return_value = fake_envelope
        with caplog.at_level(logging.INFO, logger="app.blueprints.chat_routes"):
            result = cr._maybe_classify_and_inject_scaffold(
                base_system="You are XelaSphere.",
                user_prompt="deep explainer on X including A and B",
            )
    assert mock_clf.call_count == 1
    assert result["scaffold"] is not None
    assert result["scaffold"]["layout"] == "structured"
    # Base system prompt was augmented with something scaffold-related:
    assert result["base_system"] != "You are XelaSphere."
    # Log line emitted, structured enough to grep:
    joined = "\n".join(r.message for r in caplog.records)
    assert "scaffold" in joined.lower()
    assert "structured" in joined

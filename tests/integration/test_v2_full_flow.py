"""W-25 — end-to-end flow integration test.

Exercises the whole v2 chain with mocked boundaries (no real LLM, no
real Firestore). Covers both paths:

  structured  :  classifier → scaffold → structured_generator → SSE
                 events → audit_response → write_scaffold_audit
  markdown    :  classifier → scaffold → skip structured → render_mode
                 → normal stream → audit on rendered markdown

And the "audit captures every flow" invariant: regardless of path,
if v2 is on and a scaffold exists, the audit writer is called once.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.integration


_STRUCTURED_SCAFFOLD = {
    "layout": "structured", "opening": "hook", "separator": True,
    "sections": [
        {"title": "History",   "emoji": "", "style": "prose"},
        {"title": "Intuition", "emoji": "", "style": "prose"},
    ],
    "emphasis": "none", "max_paragraph_sentences": 3,
    "list_threshold": 3, "width_hint": "medium",
}

_FLAT_SCAFFOLD = {
    "layout": "prose", "opening": "direct", "separator": False,
    "sections": [],
    "emphasis": "none", "max_paragraph_sentences": 3,
    "list_threshold": 3, "width_hint": "medium",
}


def _valid_payload():
    return {
        "opening": "Quick overview.",
        "sections": [
            {"title": "History",   "emoji": "", "body": "2017.", "style": "prose"},
            {"title": "Intuition", "emoji": "", "body": "Focus.", "style": "prose"},
        ],
        "closing": "",
    }


# ============================================================
# Structured path end-to-end
# ============================================================


def test_structured_flow_classifier_to_audit_write(monkeypatch):
    """Classifier returns structured scaffold → structured generator
    succeeds → SSE events include scaffold+render_mode+structured+done
    → audit fires with render_path='structured' → Firestore write_
    scaffold_audit called once with correct metadata."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")

    from app.blueprints import chat_routes as cr

    # Mock: classifier returns a structured scaffold envelope
    classifier_envelope = {
        "intent_type": "question", "scaffold": _STRUCTURED_SCAFFOLD,
        "confidence": 0.9,
    }
    # Mock: structured generator yields a valid payload
    def fake_gen(*_, **__):
        yield {"type": "structured", "data": _valid_payload()}

    mock_executor = MagicMock()
    mock_executor.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)

    with patch.object(cr, "classify_user_intent", return_value=classifier_envelope) as m_clf, \
         patch.object(cr, "stream_structured_response", side_effect=fake_gen) as m_gen, \
         patch.object(cr, "audit_response", return_value={"metrics": {"compliance_rate": 0.95}}) as m_audit, \
         patch.object(cr, "write_scaffold_audit") as m_write, \
         patch.object(cr, "_AUDIT_EXECUTOR", mock_executor):

        # Step 1 — classify + inject (flag-on path)
        _v2 = cr._maybe_classify_and_inject_scaffold(
            base_system="You are XelaSphere.",
            user_prompt="explain attention including history and intuition",
        )
        assert _v2["scaffold"]["layout"] == "structured"
        assert m_clf.call_count == 1

        # Step 2 — structured pre-flight
        events = list(cr._try_structured_stream(
            scaffold=_v2["scaffold"],
            prompt="explain attention including history and intuition",
            model="gpt-4.1-mini",
            system_prompt_override=None,
        ))
        assert [e["event"] for e in events] == ["scaffold", "render_mode", "structured", "done"]

        # Step 3 — audit fire (structured path uses JSON directly)
        done_data = next(e for e in events if e["event"] == "done")["data"]
        cr._fire_audit_async(
            scaffold=_v2["scaffold"], rendered_output="",
            meta={"render_path": "structured",
                  "structured_data": done_data["structured_data"]},
            user_id="u_1", session_id="s_1",
            model="gpt-4.1-mini", provider="openai",
        )

    # Full chain — one audit per completed stream:
    assert m_audit.call_count == 1
    assert m_write.call_count == 1
    write_kwargs = m_write.call_args.kwargs
    assert write_kwargs["user_id"] == "u_1"
    assert write_kwargs["provider"] == "openai"
    # Audit got the structured-path meta:
    audit_meta = m_audit.call_args.kwargs.get("meta") or m_audit.call_args.args[2]
    assert audit_meta["render_path"] == "structured"


# ============================================================
# Markdown fallback end-to-end
# ============================================================


def test_markdown_fallback_flow_classifier_to_audit_write(monkeypatch):
    """Classifier picks a flat scaffold (no sections) → structured
    pre-flight yields nothing → caller would use legacy markdown
    stream → post-stream audit fires with render_path='markdown'."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")

    from app.blueprints import chat_routes as cr

    classifier_envelope = {
        "intent_type": "question", "scaffold": _FLAT_SCAFFOLD,
        "confidence": 0.9,
    }
    mock_executor = MagicMock()
    mock_executor.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)

    with patch.object(cr, "classify_user_intent", return_value=classifier_envelope), \
         patch.object(cr, "stream_structured_response") as m_gen, \
         patch.object(cr, "audit_response", return_value={"metrics": {"compliance_rate": 0.85}}) as m_audit, \
         patch.object(cr, "write_scaffold_audit") as m_write, \
         patch.object(cr, "_AUDIT_EXECUTOR", mock_executor):

        _v2 = cr._maybe_classify_and_inject_scaffold(
            base_system="x", user_prompt="why is the sky blue",
        )
        assert _v2["scaffold"]["layout"] == "prose"

        events = list(cr._try_structured_stream(
            scaffold=_v2["scaffold"], prompt="why is the sky blue",
            model="gpt-4.1-mini", system_prompt_override=None,
        ))
        # Flat scaffold → pre-flight skipped, no events:
        assert events == []
        assert m_gen.call_count == 0

        # The markdown stream emits its own content via legacy path; then
        # chat_routes fires audit with render_path='markdown'.
        cr._fire_audit_async(
            scaffold=_v2["scaffold"],
            rendered_output="The sky is blue because of Rayleigh scattering.",
            meta={"render_path": "markdown"},
            user_id="u_2", session_id="s_2",
            model="gpt-4.1-mini", provider="openai",
        )

    assert m_audit.call_count == 1
    assert m_write.call_count == 1
    audit_meta = m_audit.call_args.kwargs.get("meta") or m_audit.call_args.args[2]
    assert audit_meta["render_path"] == "markdown"


# ============================================================
# Generator fallback → markdown path
# ============================================================


def test_structured_generator_fallback_routes_to_markdown_audit(monkeypatch):
    """Classifier picks structured, but the generator falls back. Caller
    runs the markdown stream and audit fires with render_path='markdown'."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")

    from app.blueprints import chat_routes as cr

    classifier_envelope = {
        "intent_type": "question", "scaffold": _STRUCTURED_SCAFFOLD,
        "confidence": 0.9,
    }
    def fake_gen_fallback(*_, **__):
        yield {"type": "fallback_markdown", "reason": "schema_invalid"}
    mock_executor = MagicMock()
    mock_executor.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)

    with patch.object(cr, "classify_user_intent", return_value=classifier_envelope), \
         patch.object(cr, "stream_structured_response", side_effect=fake_gen_fallback), \
         patch.object(cr, "audit_response", return_value={"metrics": {"compliance_rate": 0.7}}) as m_audit, \
         patch.object(cr, "write_scaffold_audit") as m_write, \
         patch.object(cr, "_AUDIT_EXECUTOR", mock_executor):

        _v2 = cr._maybe_classify_and_inject_scaffold(
            base_system="x", user_prompt="deep explainer on X covering A and B",
        )
        events = list(cr._try_structured_stream(
            scaffold=_v2["scaffold"], prompt="p",
            model="gpt-4.1-mini", system_prompt_override=None,
        ))
        # Fallback: no events — caller proceeds to markdown path
        assert events == []
        cr._fire_audit_async(
            scaffold=_v2["scaffold"],
            rendered_output="## History\n\nBody.\n\n## Intuition\n\nBody.",
            meta={"render_path": "markdown"},
            user_id="u_3", session_id="s_3",
            model="gpt-4.1-mini", provider="openai",
        )

    assert m_audit.call_count == 1
    assert m_write.call_count == 1


# ============================================================
# Flag off — nothing happens
# ============================================================


def test_flag_off_no_classifier_no_audit(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "false")
    from app.blueprints import chat_routes as cr
    mock_executor = MagicMock()
    mock_executor.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
    with patch.object(cr, "classify_user_intent") as m_clf, \
         patch.object(cr, "stream_structured_response") as m_gen, \
         patch.object(cr, "audit_response") as m_audit, \
         patch.object(cr, "write_scaffold_audit") as m_write, \
         patch.object(cr, "_AUDIT_EXECUTOR", mock_executor):
        v2 = cr._maybe_classify_and_inject_scaffold(
            base_system="x", user_prompt="anything",
        )
        events = list(cr._try_structured_stream(
            scaffold=v2.get("scaffold"), prompt="p", model="m",
            system_prompt_override=None,
        ))
        cr._fire_audit_async(
            scaffold=v2.get("scaffold"), rendered_output="x",
            meta={}, user_id="u", session_id="s",
            model="m", provider="p",
        )
    assert m_clf.call_count == 0
    assert m_gen.call_count == 0
    assert m_audit.call_count == 0
    assert m_write.call_count == 0

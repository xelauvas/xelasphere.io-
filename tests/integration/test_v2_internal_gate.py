"""W-26 — internal-only rollout gate.

When SCAFFOLD_V2_ENABLED=true AND SCAFFOLD_V2_INTERNAL_EMAILS is non-empty,
only users whose session email is in the allowlist get the v2 path.
All others — including guests — get the legacy path even with the flag on.

Empty allowlist → open to everyone (matches spec §9 rollout step 3 onward).
"""
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.integration


def test_helper_returns_false_when_flag_off(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "false")
    monkeypatch.delenv("SCAFFOLD_V2_INTERNAL_EMAILS", raising=False)
    from app.blueprints import chat_routes as cr
    assert cr._v2_enabled_for_email("anyone@example.com") is False


def test_helper_returns_true_for_open_rollout(monkeypatch):
    """Flag on, allowlist empty → v2 is open to everyone."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    monkeypatch.delenv("SCAFFOLD_V2_INTERNAL_EMAILS", raising=False)
    from app.blueprints import chat_routes as cr
    assert cr._v2_enabled_for_email("random@example.com") is True
    assert cr._v2_enabled_for_email(None) is True


def test_helper_blocks_non_internal_when_allowlist_set(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    monkeypatch.setenv(
        "SCAFFOLD_V2_INTERNAL_EMAILS",
        "xela@xelasphere.io, team@xelasphere.io",
    )
    from app.blueprints import chat_routes as cr
    assert cr._v2_enabled_for_email("outsider@gmail.com") is False
    assert cr._v2_enabled_for_email(None) is False  # guests excluded


def test_helper_allows_internal_when_allowlist_set(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    monkeypatch.setenv(
        "SCAFFOLD_V2_INTERNAL_EMAILS",
        "xela@xelasphere.io,team@xelasphere.io",
    )
    from app.blueprints import chat_routes as cr
    assert cr._v2_enabled_for_email("xela@xelasphere.io") is True
    assert cr._v2_enabled_for_email("team@xelasphere.io") is True


def test_helper_is_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    monkeypatch.setenv(
        "SCAFFOLD_V2_INTERNAL_EMAILS",
        "  Xela@XelaSphere.io , team@xelasphere.io ",
    )
    from app.blueprints import chat_routes as cr
    assert cr._v2_enabled_for_email("xela@xelasphere.io") is True
    assert cr._v2_enabled_for_email("XELA@xelasphere.io") is True
    assert cr._v2_enabled_for_email("team@xelasphere.io") is True


def test_classify_and_inject_respects_internal_gate(monkeypatch):
    """Full call to _maybe_classify_and_inject_scaffold — when the caller
    passes a non-internal user_email, the scaffold is skipped even
    with the flag on."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    monkeypatch.setenv("SCAFFOLD_V2_INTERNAL_EMAILS", "insider@xelasphere.io")
    from app.blueprints import chat_routes as cr
    with patch.object(cr, "classify_user_intent") as mock_clf:
        r = cr._maybe_classify_and_inject_scaffold(
            base_system="x", user_prompt="anything",
            user_email="outsider@gmail.com",
        )
    assert r["scaffold"] is None
    assert mock_clf.call_count == 0


def test_classify_and_inject_fires_for_internal_user(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    monkeypatch.setenv("SCAFFOLD_V2_INTERNAL_EMAILS", "insider@xelasphere.io")
    from app.blueprints import chat_routes as cr
    fake_env = {
        "intent_type": "question", "confidence": 0.9,
        "scaffold": {
            "layout": "prose", "opening": "direct", "separator": False,
            "sections": [], "emphasis": "none",
            "max_paragraph_sentences": 3, "list_threshold": 3,
            "width_hint": "medium",
        },
    }
    with patch.object(cr, "classify_user_intent", return_value=fake_env) as mock_clf:
        r = cr._maybe_classify_and_inject_scaffold(
            base_system="x", user_prompt="anything",
            user_email="insider@xelasphere.io",
        )
    assert r["scaffold"] is not None
    assert mock_clf.call_count == 1

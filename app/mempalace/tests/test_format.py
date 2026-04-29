"""Tests for app.mempalace.format — the wakeup → prompt-context formatter.

These exercise the contract documented in ``format.py`` against
synthetic wakeup dicts shaped like real sidecar responses.
"""
from __future__ import annotations

from app.mempalace.format import format_wakeup_for_prompt


def _wakeup(*, l2: list[dict] | None = None, l3: list[dict] | None = None) -> dict:
    """Build a minimal wakeup dict with the required tier keys."""
    return {
        "l0_recent": [],
        "l1_session": [],
        "l2_topic": l2 or [],
        "l3_semantic": l3 or [],
    }


def test_returns_empty_string_for_none_wakeup():
    assert format_wakeup_for_prompt(None) == ""


def test_returns_empty_string_for_empty_wakeup():
    assert format_wakeup_for_prompt(_wakeup()) == ""


def test_formats_user_statement_with_role_prefix():
    wakeup = _wakeup(l2=[
        {"text": "I'm Riley, documentary filmmaker in Toledo.",
         "score": 0.6},
    ])
    out = format_wakeup_for_prompt(wakeup)
    assert "<conversation_recall>" in out
    assert "</conversation_recall>" in out
    assert "[user]: I'm Riley, documentary filmmaker in Toledo." in out


def test_strips_action_chat_prefix_for_assistant_turns():
    wakeup = _wakeup(l2=[
        {"text": "ACTION: chat\nThe Sony FX3 is a great choice for documentary work.",
         "score": 0.5},
    ])
    out = format_wakeup_for_prompt(wakeup)
    assert "[assistant]: The Sony FX3 is a great choice" in out
    # Prefix must be stripped, not just left in.
    assert "ACTION: chat" not in out


def test_user_turns_come_before_assistant_turns():
    """User-turn precedence: when the LLM resolves conflicting recall,
    user statements should anchor first. Format orders them accordingly.
    """
    wakeup = _wakeup(l2=[
        {"text": "ACTION: chat\nYour camera is an Arri Alexa.",
         "score": 0.7},
        {"text": "Equipment: borrowed Sony FX3 from my friend.",
         "score": 0.4},
    ])
    out = format_wakeup_for_prompt(wakeup)
    user_idx = out.find("[user]:")
    assistant_idx = out.find("[assistant]:")
    assert user_idx >= 0 and assistant_idx >= 0
    assert user_idx < assistant_idx, (
        "user turns must appear before assistant turns in the formatted "
        "context so user statements anchor the LLM's belief"
    )


def test_deduplicates_across_l2_and_l3_tiers():
    """v1 ``topic_search`` delegates to ``semantic_search`` so l2 and
    l3 typically have identical items. The formatter must not list the
    same text twice.
    """
    same_item = {"text": "Editor is my friend Devon.", "score": 0.5}
    wakeup = _wakeup(l2=[same_item], l3=[same_item])
    out = format_wakeup_for_prompt(wakeup)
    # 'Devon' appears exactly once in the formatted output.
    assert out.count("Devon") == 1


def test_truncates_long_items_at_600_chars():
    """Per-item character cap keeps total context bounded; long items
    are truncated with an ellipsis.
    """
    long_text = "I am the user and I am verbose. " * 30  # ~960 chars
    wakeup = _wakeup(l2=[{"text": long_text.strip(), "score": 0.5}])
    out = format_wakeup_for_prompt(wakeup)
    # Truncated marker present.
    assert "..." in out
    # No verbatim 800+ char run.
    user_line = [l for l in out.splitlines() if l.startswith("  [user]:")][0]
    assert len(user_line) < 700  # 600 cap + " [user]: " prefix + buffer


def test_safety_wrapper_includes_precedence_comment():
    """The wrapper comments must explain that user statements take
    precedence over assistant prior responses — this is the prompt-
    engineering instruction that protects against the system trusting
    a hallucinated past assistant answer over the user's truth.
    """
    wakeup = _wakeup(l2=[{"text": "Some user content.", "score": 0.5}])
    out = format_wakeup_for_prompt(wakeup)
    assert "User statements are authoritative" in out
    assert "trust the user" in out


def test_skips_empty_text_items():
    """Defensive: malformed items with empty/missing text are skipped."""
    wakeup = _wakeup(l2=[
        {"text": "", "score": 0.5},
        {"text": "real content", "score": 0.4},
        {"text": None, "score": 0.3},
        {},
    ])
    out = format_wakeup_for_prompt(wakeup)
    assert "[user]: real content" in out
    # Only one item lands in the output.
    assert out.count("[user]:") == 1
    assert out.count("[assistant]:") == 0


def test_multiple_distinct_items_all_formatted():
    wakeup = _wakeup(l2=[
        {"text": "I'm Riley, documentary filmmaker.", "score": 0.6},
        {"text": "Equipment: Sony FX3.", "score": 0.4},
        {"text": "ACTION: chat\nGreat camera choice.", "score": 0.3},
    ])
    out = format_wakeup_for_prompt(wakeup)
    assert "I'm Riley" in out
    assert "Sony FX3" in out
    assert "Great camera choice" in out
    assert out.count("[user]:") == 2
    assert out.count("[assistant]:") == 1

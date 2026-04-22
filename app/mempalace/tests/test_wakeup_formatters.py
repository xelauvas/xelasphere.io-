"""M0.17 Stage 1 RED — wake-up formatter tests.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4105-4137 (tests for Task M0.17.2).

At Stage 1 RED: tests fail at the formatter call (NotImplementedError).
Expected RED shape.

Structural assertions per spec §6 line 563: "structural assertions
(exact N entries in L0, token budget, monotonic timestamps, pair_id
present) — not byte-exact snapshots." Byte-exact snapshots are brittle
against whitespace tweaks; structural invariants are what the downstream
orchestrator actually depends on.

Drawer shape (per sidecar `schemas.py:80`):
  Drawer { text: str, wing: Optional[str], room: Optional[str],
           ts: float, pair_id: Optional[str], score: Optional[float] }
"""

from __future__ import annotations

import pytest

from app.mempalace.wakeup import (
    to_anthropic_system,
    to_gemini_contents,
    to_local_prompt_block,
    to_openai_system_msg,
)


@pytest.fixture
def sample_ctx():
    """Fixture fills L0 (2 entries, ts-decreasing), L1 (1), L2 (1), L3 (1).

    ts-decreasing within L0 is the invariant that formatters preserve
    (most-recent-first rendering). Non-empty pair_id on all drawers —
    tests that consumers can group by pair_id end-to-end.
    """
    return {
        "user_id": "u-123",
        "schema_version": 1,
        "l0_recent": [
            {
                "text": "hello",
                "wing": "conversations",
                "room": "session-x",
                "ts": 100.0,
                "pair_id": "p1",
                "score": None,
            },
            {
                "text": "earlier",
                "wing": "conversations",
                "room": "session-x",
                "ts": 90.0,
                "pair_id": "p0",
                "score": None,
            },
        ],
        "l1_session": [
            {"text": "session note", "wing": "", "room": "", "ts": 85.0,
             "pair_id": "p2", "score": None},
        ],
        "l2_topic": [
            {"text": "topic note", "wing": "topics", "room": "", "ts": 80.0,
             "pair_id": "p3", "score": 0.8},
        ],
        "l3_semantic": [
            {"text": "semantic note", "wing": "semantic", "room": "",
             "ts": 70.0, "pair_id": "p4", "score": 0.7},
        ],
        "meta": {"hot": True, "embed_ms": 1.0, "total_ms": 10.0, "warnings": []},
    }


@pytest.fixture
def empty_layers_ctx():
    """Only L0 populated; L1/L2/L3 empty."""
    return {
        "user_id": "u-456",
        "schema_version": 1,
        "l0_recent": [
            {"text": "hi", "wing": "", "room": "", "ts": 100.0, "pair_id": "p1",
             "score": None},
        ],
        "l1_session": [],
        "l2_topic": [],
        "l3_semantic": [],
        "meta": {"hot": True, "embed_ms": 0.0, "total_ms": 0.0, "warnings": []},
    }


@pytest.fixture
def none_wing_ctx():
    """H6-adjacent: wing and room are None (Optional[str] from sidecar
    schema), not empty string. Renderer must handle None without leaking
    "None" into the prompt.
    """
    return {
        "user_id": "u-789",
        "schema_version": 1,
        "l0_recent": [
            {"text": "bare drawer", "wing": None, "room": None, "ts": 100.0,
             "pair_id": "p1", "score": None},
        ],
        "l1_session": [],
        "l2_topic": [],
        "l3_semantic": [],
        "meta": {"hot": True, "embed_ms": 0.0, "total_ms": 0.0, "warnings": []},
    }


# --- Anthropic format ---


def test_anthropic_includes_all_layers_with_entries(sample_ctx):
    out = to_anthropic_system(sample_ctx)
    assert "## Recent" in out
    assert "## Session" in out
    assert "## Topic" in out
    assert "## Semantic" in out


def test_anthropic_empty_layer_omitted(empty_layers_ctx):
    """Empty L1/L2/L3 must not produce headers (no '## Session' with
    nothing under it — confuses model and burns tokens).
    """
    out = to_anthropic_system(empty_layers_ctx)
    assert "## Session" not in out
    assert "## Topic" not in out
    assert "## Semantic" not in out
    assert "## Recent" in out


def test_anthropic_ts_ordering_preserved_in_l0(sample_ctx):
    """Formatter preserves most-recent-first ordering within L0."""
    out = to_anthropic_system(sample_ctx)
    recent_start = out.index("## Recent")
    # L1 header ends L0 block; fixture populates L1 so the end is safe.
    recent_end = out.index("## Session", recent_start)
    recent_block = out[recent_start:recent_end]
    rendered_tss = [
        float(line.split()[0].lstrip("["))
        for line in recent_block.splitlines()
        if line.startswith("[")
    ]
    assert rendered_tss == sorted(rendered_tss, reverse=True)


def test_anthropic_none_wing_does_not_leak_literal(none_wing_ctx):
    """H6: wing=None must not render as 'None' in output."""
    out = to_anthropic_system(none_wing_ctx)
    assert "None" not in out, (
        f"output leaked literal 'None' for a None wing: {out!r}"
    )
    assert "bare drawer" in out  # text still renders


# --- OpenAI / Gemini / local bridges ---


def test_openai_wraps_anthropic_in_system_role(sample_ctx):
    msg = to_openai_system_msg(sample_ctx)
    assert isinstance(msg, dict)
    assert msg.get("role") == "system"
    assert isinstance(msg.get("content"), str)
    assert msg["content"] == to_anthropic_system(sample_ctx)


def test_gemini_wraps_anthropic_in_contents_list(sample_ctx):
    out = to_gemini_contents(sample_ctx)
    assert isinstance(out, list)
    assert len(out) == 1
    entry = out[0]
    assert entry.get("role") == "user"
    parts = entry.get("parts")
    assert isinstance(parts, list)
    assert len(parts) == 1
    assert parts[0].get("text") == to_anthropic_system(sample_ctx)


def test_local_returns_plain_string(sample_ctx):
    out = to_local_prompt_block(sample_ctx)
    assert isinstance(out, str)
    assert out == to_anthropic_system(sample_ctx)


# --- pair_id invariant ---


def test_pair_id_present_on_all_drawers(sample_ctx):
    """Fixture invariant — every drawer in every layer has pair_id.
    Guard against regressions in the fixture itself.
    """
    for layer in ("l0_recent", "l1_session", "l2_topic", "l3_semantic"):
        for d in sample_ctx.get(layer) or []:
            assert "pair_id" in d
            assert d["pair_id"] is not None

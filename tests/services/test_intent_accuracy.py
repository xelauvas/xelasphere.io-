"""Spec §3 guardrail: unified prompt must not regress intent accuracy by more
than 5 percentage points absolute vs the baseline."""
import os
import pytest
from app.services import chat_engine


FIXTURES = [
    ("what's BTC trading at today?",              "financial_query"),
    ("ETH price chart please",                    "financial_query"),
    ("write me a haiku about the ocean",          "creative_task"),
    ("compose a short poem about stars",          "creative_task"),
    ("why is the sky blue?",                      "question"),
    ("explain RAG like I'm five",                 "question"),
    ("give me a recipe for chicken curry",        "recipe_request"),
    ("how do I make pancakes?",                   "recipe_request"),
    ("write a python script to sort a list",      "code_request"),
    ("show me a for-loop in javascript",          "code_request"),
    ("draw me a picture of a dragon",             "image_request"),
    ("generate an image of a sunset",             "image_request"),
    ("hey, how's it going?",                      "general_chat"),
    ("good morning!",                             "general_chat"),
]

# Baseline on main (pre-Task-4) was 78.6% (11/14). Per spec §3, the unified
# prompt must stay within 5 percentage points absolute. Absolute floor: 73.6%.
BASELINE_PCT = 78.6
ACCEPTABLE_FLOOR_PCT = BASELINE_PCT - 5.0  # 73.6%


@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="Requires LLM access for accuracy measurement",
)
def test_unified_prompt_does_not_regress_intent_accuracy():
    chat_engine._intent_cache.clear()
    hits = 0
    misses = []
    for prompt, expected in FIXTURES:
        result = chat_engine.classify_user_intent(prompt, use_cache=False)
        got = result.get("intent_type")
        if got == expected:
            hits += 1
        else:
            misses.append((prompt, expected, got))

    rate = (hits / len(FIXTURES)) * 100
    print(f"\nINTENT ACCURACY (unified prompt): {hits}/{len(FIXTURES)} = {rate:.1f}%")
    print(f"Baseline: {BASELINE_PCT}% | Floor: {ACCEPTABLE_FLOOR_PCT}%")
    if misses:
        print(f"Misses ({len(misses)}):")
        for p, exp, got in misses:
            print(f"  {p!r}: expected={exp!r}, got={got!r}")

    assert rate >= ACCEPTABLE_FLOOR_PCT, (
        f"Unified prompt dropped intent accuracy to {rate:.1f}% "
        f"(floor {ACCEPTABLE_FLOOR_PCT}%, baseline {BASELINE_PCT}%). "
        "Per spec §3, refactor classify_user_intent() into two sequential "
        "internal LLM calls: (1) original intent-only prompt, (2) scaffold-only "
        "prompt conditioned on intent_type."
    )


# Regression test for v1.1 intelligence upgrade (A+B+E).
# This is the exact prompt that produced a monolithic 15-sentence paragraph
# in v1 — the user-reported failure case. With signal override + skeleton
# instructions, the scaffold must come back as structured with ≥2 sections.
@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="Requires LLM access",
)
def test_deep_explainer_with_named_angles_produces_structured_scaffold():
    chat_engine._intent_cache.clear()
    prompt = (
        "write a deep explainer on how transformer attention works, "
        "including history and intuition"
    )
    result = chat_engine.classify_user_intent(prompt, use_cache=False)
    scaffold = result.get("scaffold") or {}
    assert scaffold.get("layout") == "structured", (
        f"Expected structured (signal override), got {scaffold.get('layout')!r}"
    )
    sections = scaffold.get("sections") or []
    assert len(sections) >= 2, (
        f"Expected ≥2 sections for multi-angle explainer, got {len(sections)}: {sections}"
    )
    # The skeleton must actually render headers for these sections:
    from app.services.chat_engine import Scaffold, scaffold_to_instruction
    rendered = scaffold_to_instruction(Scaffold.from_dict(scaffold))
    assert "TEMPLATE" in rendered
    assert rendered.count("## ") >= 2

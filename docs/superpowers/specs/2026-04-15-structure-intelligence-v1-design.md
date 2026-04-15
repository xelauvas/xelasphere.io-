# Structure Intelligence v1 — Design Spec

**Date:** 2026-04-15
**Status:** Approved, ready for implementation planning
**Scope:** Pre-stream response scaffold planner, unified into existing intent classifier

---

## Problem

Today, response structure is decided **after** tokens arrive. `streamRenderer.classifyContext()` in `app/static/js/streamRenderer.js` inspects rendered content post-stream and assigns a `data-ctx` attribute (e.g., `long-prose`, `technical`, `conversational`). The renderer then applies width/spacing rules.

This is reactive. The main model generates without knowing the target structure, and the UI reflows when `data-ctx` changes class. The result: inconsistent structure, layout flicker, and no way to enforce "narrow column for prose, wide column for code."

## Goal

Decide structure **before** the main model streams its first token. Inject a format instruction into the system prompt so the model generates content that matches the target layout. Send the chosen layout to the client ahead of the stream so `data-ctx` is set before content arrives — no reflow.

**Non-goals (explicitly deferred to later specs):**

- Intra-conversation arc adaptation (conversation 5 ≠ conversation 1 density)
- Semantic feedback from next message ("explain differently" = scaffold failed)
- Per-user scaffold learning (memory-aware cache keys)
- Sentence-level typography editing (second-pass rewrite)
- Feedback collector + scaffold quality scoring (requires above to be useful)

## Architectural decision: extend, don't add a layer

The codebase already has four orthogonal classification layers:

| File | Classifies | Output | Purpose |
|---|---|---|---|
| `smart_router.py` | task type (coding/reasoning/vision/…) | `(model_id, task_type)` | Model selection |
| `chat_engine.py::classify_user_intent` | intent (financial/creative/recipe/…) | `{intent_type, confidence}` | Feature routing |
| `smart_guard.py` | action continuation, missing slots | `InterceptResult` | Input gating |
| `streamRenderer.js::classifyContext` | rendering layout | `data-ctx` | Render (post-stream) |

Scaffold generation is **layer 4 moved pre-stream**, not a fifth layer. It extends `classify_user_intent()` because that function is already:

- Pre-stream
- LLM-backed (OpenAI provider already configured)
- Cached (5-minute TTL via `_intent_cache`)
- Returning structured JSON
- Owned by the chat engine that assembles the system prompt

No new module. No new cache. No new service.

## §1. Surface area

`classify_user_intent()` in `app/services/chat_engine.py` keeps its name and signature. The return dict gains one new key:

```python
{
  "intent_type": "creative_task",     # EXISTING — all current callers work
  "confidence": 0.87,                 # EXISTING
  "scaffold": Scaffold | None         # NEW — additive, None on fallback failure
}
```

All existing callers that read only `intent_type` continue to work with zero changes. New consumers (system-prompt assembler, SSE envelope) read `scaffold`.

A new helper `scaffold_to_instruction(scaffold: Scaffold) -> str` lives in the same file and converts a `Scaffold` into a system-prompt fragment.

## §2. Scaffold schema

The `layout` vocabulary matches the existing `data-ctx` values emitted by `streamRenderer.classifyContext()` so no translation layer is needed:

```python
@dataclass
class Scaffold:
    layout: str
      # one of: conversational | prose | long-prose | technical
      #       | code-heavy | list-heavy | structured | sequential | creative
    opening: str                         # hook | direct | question | none
    separator: bool                      # insert --- after opening paragraph
    sections: list[dict]                 # [{"title": str, "emoji": str, "style": "prose|list|steps"}]
    emphasis: str                        # bold | italic | none
    max_paragraph_sentences: int         # 2-4
    list_threshold: int                  # min parallel items before using a bullet list
    width_hint: str                      # narrow | medium | wide
```

No CSS changes required to wire v1 — every `layout` value already has matching rules in `app/static/css/stream-blocks.css`.

## §3. LLM call — single round-trip, with split-fallback guardrail

**Default path: unified prompt.** Extend the existing `classification_prompt` in `classify_user_intent()` to return both `intent_type` and `scaffold` in one JSON response. One call, one cache entry, one round-trip.

**Model:** `gpt-4.1-nano`, accessed via the existing provider config in `app/memory_v2/llm_retrieval.py:26`. Same API key, same client, same error handling.

**Parameters:**
- `temperature: 0.1`
- `max_tokens: 250` (enough for both fields with comfortable margin)
- `timeout: 1.5s` hard cap — the stream never waits longer

**Cache:** reuse `_intent_cache` (`app/services/chat_engine.py:122`). Key stays the same prompt hash. Cached value now holds the combined envelope. TTL unchanged.

**Accuracy guardrail (from review):** the combined prompt is longer than the original intent-only prompt. If post-build measurement shows intent classification accuracy degrades versus the pre-change baseline (>5 percentage points absolute drop on the test set), **split into two sequential calls within the same `classify_user_intent()` function**:

1. First call: original intent prompt (accuracy preserved)
2. Second call: scaffold-only prompt, conditioned on intent_type

Both results still return as one dict, still share the same cache entry, still look identical to all callers. Only the internal implementation swaps one LLM call for two. The API surface does not change.

**Measurement:** intent-classifier test suite runs pre-change (baseline) and post-change (unified) on the same fixture set. Acceptance threshold: no more than 5% accuracy drop on intent classification. If exceeded, ship the split-call implementation instead.

## §4. Fallback chain — never fail open

**Order of precedence:**

1. **LLM returns valid JSON** → use returned scaffold
2. **LLM times out, errors, or returns unparseable output** → derive default scaffold from `smart_router.detect_task_type()` (already computed pre-stream, free). Mapping:

   | task_type | layout | opening | width_hint |
   |---|---|---|---|
   | `coding` | `technical` | `direct` | `wide` |
   | `reasoning` | `prose` | `hook` | `medium` |
   | `image_gen` | `conversational` | `direct` | `medium` |
   | `vision` | `prose` | `direct` | `medium` |
   | `uncensored` | `prose` | `direct` | `medium` |
   | `long_context` | `long-prose` | `direct` | `narrow` |
   | `chat` (default) | `conversational` | `direct` | `medium` |

3. **Even the task-type fallback itself errors** (e.g., import-time failure, truly exceptional path) → `scaffold: None` is returned → client-side `classifyContext()` runs post-stream as today. Current behavior preserved. No regression risk.

Under normal conditions the fallback at step 2 always returns a fully populated `Scaffold` object; step 3 is the degenerate safety net that exists so `None` remains a legal value of the return key rather than an unhandled exception.

## §5. Delivery

**To the main model (server-side):**
`scaffold_to_instruction(scaffold)` builds a format-instruction block and appends it to the system prompt at stream start, before the main model is invoked. The model sees the structure contract before token one.

Example output of `scaffold_to_instruction()`:

```
RESPONSE FORMAT INSTRUCTIONS:
Open with 1-2 sentences that frame the topic — no header, no preamble.
Add --- after the opening paragraph.
Structure the response with these exact sections:
- ## 🎯 Core Idea
- ## ⚙️ How It Works
- ## ⚠️ Tradeoffs
Maximum 3 sentences per paragraph.
Bold exactly one key phrase per section. No more.
Only use bullet lists when there are 3+ parallel items.
```

**To the client (streaming):**
The scaffold is sent as the **first SSE event** of the stream response, event type `scaffold`, before any model tokens. The client handler in `streamRenderer.js` reads it and sets `data-ctx` on the message element immediately — before content arrives.

If the `scaffold` event is missing (older server, network hiccup, fallback failure), the existing post-stream `classifyContext()` runs as today. Backward compatible with every transport and every client version.

## §6. Scope boundary

**In v1:**

- Extend `classify_user_intent()` return dict
- Add `Scaffold` dataclass + `scaffold_to_instruction()` in `chat_engine.py`
- Extend unified prompt; on accuracy regression, split into two internal calls
- Add scaffold → SSE event in streaming path
- Add scaffold → system-prompt injection
- Fallback to task_type-derived default scaffold on LLM failure
- `classifyContext()` remains as the client-side last-resort fallback

**NOT in v1:**

- Feedback collector (scroll depth, copy events)
- Scaffold quality store / scoring
- Intra-conversation arc tracking
- Per-user scaffold learning
- Sentence-level typography pass
- Regeneration-as-negative-signal tracking
- Next-message-failure detection

These are called out in "Goals / Non-goals" above and each warrants its own spec when v1 data justifies building them.

## §7. Test plan

**Acceptance tests** — four representative user messages, each asserts scaffold shape + resulting `data-ctx`:

| Input | Expected `layout` | Expected `opening` | Expected `width_hint` | Expected sections |
|---|---|---|---|---|
| `"hey what's up"` | `conversational` | `direct` | `medium` | 0 |
| `"explain how RAG works"` | `prose` or `long-prose` | `hook` | `narrow` | 0 |
| `"write a python script to parse CSV"` | `technical` | `direct` | `wide` | 0 |
| `"plan a 3-day Tokyo trip with food, sights, and a day trip"` | `structured` | `hook` (separator: true) | `medium` | ≥3 |

**Fallback test:**
Set `OPENAI_API_KEY=""` in the test environment; rerun all four cases. Assert each returns a valid `Scaffold` (from task-type-derived defaults) and all four still pass basic shape assertions (layout is in the allowed vocabulary, all required fields populated).

**Intent accuracy regression test:**
Run the existing intent-classifier fixture set twice:
- Before the change (baseline — record pass rate)
- After the unified prompt change (record new pass rate)

If the new pass rate drops by more than 5 percentage points absolute, ship the split-call variant (§3) instead of the unified prompt.

**Integration test:**
Start the server, send one message matching each of the four acceptance cases, inspect the SSE stream. Assert:
- First SSE event has `event: scaffold` with valid JSON body
- `data-ctx` is set on the message element before any content tokens are rendered
- No layout flicker (no `data-ctx` change mid-stream)

## §8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Unified prompt degrades intent accuracy | Measured; split-call fallback already specced in §3 |
| LLM returns malformed JSON | `json.loads` wrapped in try/except → fallback to task-type default |
| LLM adds >1.5s latency on cache miss | Hard timeout; fallback is instant; cache hit rate projected 60-80% makes latency negligible at steady state |
| SSE first-event change breaks old clients | First event is additive — clients that don't handle it fall through to existing post-stream classifier |
| Scaffold layout vocabulary drifts from CSS | Lock vocabulary via a shared constant; add a unit test that asserts every `layout` value has a matching `[data-ctx="..."]` rule in `stream-blocks.css` |
| Cache pollution if scaffold changes invalidate intent results | Cache invalidates on deploy (process restart); 5-min TTL is already aggressive |

## §9. Rollback

Single feature flag: `STRUCTURE_INTELLIGENCE_ENABLED` (env var, default on after verification).

When `False`:
- `classify_user_intent()` returns `scaffold: None`
- Server skips the SSE `scaffold` event
- System prompt gets no `scaffold_to_instruction()` block
- Client falls back to existing post-stream `classifyContext()`

Zero-risk rollback path. Flip the flag, restart the app, everything behaves as before the change.

---

## Summary

**What ships in v1:** a pre-stream scaffold planner unified into `classify_user_intent()`, delivering structure hints to both the main model (via system prompt) and the renderer (via SSE first-event). Falls back cleanly at every level. Adds one optional key to an existing return dict. Zero new services, zero new caches, zero new modules.

**What's explicitly deferred:** feedback collection, quality scoring, conversation-arc adaptation, per-user learning, sentence-level typography. v1 proves the core hypothesis (structure pre-injection reduces reflow and improves response shape). Later specs layer on top once v1 data shows the foundation works.

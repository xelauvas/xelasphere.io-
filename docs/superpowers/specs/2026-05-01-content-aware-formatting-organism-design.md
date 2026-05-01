# Content-Aware Formatting Organism — Design Spec

**Date:** 2026-05-01
**Status:** Approved, ready for implementation planning
**Scope:** Pre-stream classifier picks one of six visual styles per user message; server injects a matching system-prompt fragment so the model writes in that style; first SSE event tells the frontend which CSS context to render in.

**Supersedes:** [`2026-04-15-structure-intelligence-v1-design.md`](./2026-04-15-structure-intelligence-v1-design.md). v1 was approved but never implemented. This spec keeps v1's "decide-before-stream + ship-via-first-SSE-event" plumbing and replaces v1's layout-vocabulary scaffold with six concrete visual styles that have distinct typography.

---

## Problem

Today, response styling is decided **after** content is fully streamed:

1. The model writes whatever it writes (currently bound by a long heavy-handed FORMAT MANDATE block in `DEVELOPER_INSTRUCTIONS` that tries to force ChatGPT-style structure on every answer).
2. After the stream ends, `streamRenderer.classifyContext()` post-classifies the rendered DOM and assigns `data-ctx` for layout (column width, line-height).

Two separate problems:

- **Visual sameness across question types.** A casual greeting, a code question, and a longform explainer all render in the same typography. There is no editorial voice, no scannable blog look for tips, no monospace-first treatment for code. The user describes wanting an "organism… like a professor who knows how to write" — different visual treatment for different question types.
- **Format-by-mandate is brittle.** The current prompt tells the model to use rich Markdown for *every* non-trivial response. This produces over-formatted casual replies and under-formatted technical ones. The right answer is per-question, not blanket.

## Goal

Decide a **visual style** before the main model streams its first token, drive both the prompt fragment and the CSS context from that decision, and deliver consistent per-question-type formatting end-to-end.

The classifier picks one of **six styles** per turn. Each style is a coordinated bundle of (prompt fragment that shapes content) × (CSS context that shapes typography).

**Non-goals (deferred):**

- Per-user style preferences
- Mid-stream style switching
- Admin UI for tweaking classifier rules or charters
- Style A/B testing infrastructure
- Custom user fonts
- Feedback collection on style fit (no scroll-depth tracking, no quality score)
- Intra-conversation style memory ("they liked editorial last turn → bias toward editorial again")
- Sentence-level typography pass (italicize-this-phrase second-pass rewrite)

## The six styles

| ID | Name | When | Typography signature |
|----|------|------|----------------------|
| `editorial` | Editorial / Magazine | Explainers, opinion, longform "professor" answers | Serif body, italic lede, small-caps section labels, considered tone |
| `modern_blog` | Modern Blog / Substack | Tips, how-tos, listicles | Sans, big bold headings, emoji bullets, "Bottom line:" callout box |
| `technical` | Code / Technical | "Write a function", "fix this bug", "explain this code" | Sans body, mono code blocks with side-bar accent, sparse emoji, ⚠️ callouts only |
| `chat` | Conversational / Chat | Greetings, "thanks", short factual ("what year was X"), banter | Sans, plain prose, no headings, single emoji max |
| `comparison` | Comparison / Table | "X vs Y", "which is better", "differences between A and B" | Verdict-first callout, then comparison table |
| `creative` | Creative / Narrative | "Write me a story", poems, scene descriptions, roleplay | Centered serif title, italic dialogue, `· · ·` scene breaks, no emoji |

All six are visually demonstrated in the project's `.superpowers/brainstorm/` mockups (see `visual-style-direction.html` and `style-coverage.html` from the brainstorming session that produced this spec).

## Architectural decision: extend `classify_user_intent`, add a regex fast path

The codebase already has `classify_user_intent()` in `app/services/chat_engine.py` — pre-stream, LLM-backed (`gpt-4.1-nano`), cached (`_intent_cache`, 5-min TTL), returning structured JSON. v1 already specified extending it. We do the same here, with one addition: **a regex fast path runs first**.

```
user message
    │
    ▼
classify_response_style(message)         ← new helper in chat_engine.py
    │
    ├── regex_match()  →  style ∈ {chat, technical, comparison, creative,
    │                              modern_blog, editorial} | None
    │
    └── if regex returns None:
        classify_user_intent(message)    ← existing function, gains a "style" key
            └── LLM call returns style ∈ above | "modern_blog" on error
    │
    ▼
style ∈ ResponseStyle (always populated, never None at use site)
```

The regex fast path covers the obvious 80% of traffic at zero latency. The LLM path handles ambiguous queries ("explain why React hooks work" — `editorial` or `technical`?) at the existing `_intent_cache` cost. Both feed the same downstream consumers.

`classify_user_intent()` keeps its name and signature. Its return dict gains one new key:

```python
{
  "intent_type": "creative_task",   # EXISTING
  "confidence": 0.87,               # EXISTING
  "style": "creative",              # NEW — additive, always one of the 6 ResponseStyle values
}
```

All existing callers that read only `intent_type` continue to work with zero changes.

## §1. Components

| File | New / Modified | Approx size | Purpose |
|------|---------------|-------------|---------|
| `app/services/response_style.py` | new | ~250 lines | `ResponseStyle` enum, regex classifier, default fallback, prompt-fragment registry |
| `app/services/chat_engine.py` | modified | +30 / -50 lines | Plug `classify_response_style()` into existing `classify_user_intent()`; remove the heavy FORMAT MANDATE block from `DEVELOPER_INSTRUCTIONS` |
| `app/blueprints/chat_routes.py` | modified | +20 lines | Inject the style-charter system_addition; emit `event: stream-meta` as first SSE event |
| `app/static/js/streamRenderer.js` | modified | +25 lines | Adopt a `streamMeta.style` passed in by `codeWindow.js`; set `data-style="..."` on `messageEl` immediately after constructor |
| `app/static/js/codeWindow.js` | modified | +20 lines | Parse the `stream-meta` SSE event before any content event; pass `streamMeta` into `new StreamRenderer(...)` |
| `app/static/css/stream-styles.css` | new | ~500 lines | Six scoped style blocks, one per `[data-style="..."]` value |
| `app/templates/index.html` | modified | +1 line | Link the new `stream-styles.css` |

## §2. ResponseStyle enum and registry

`app/services/response_style.py`:

```python
from enum import Enum

class ResponseStyle(str, Enum):
    EDITORIAL    = "editorial"
    MODERN_BLOG  = "modern_blog"
    TECHNICAL    = "technical"
    CHAT         = "chat"
    COMPARISON   = "comparison"
    CREATIVE     = "creative"

    @classmethod
    def default(cls) -> "ResponseStyle":
        return cls.MODERN_BLOG  # fallback when classification fails or is absent
```

`MODERN_BLOG` is the default because it's the closest analog to ChatGPT's house style and is the safest fall-through for "I don't know what kind of question this is."

## §3. Regex fast path

`classify_by_rules(message: str) -> ResponseStyle | None` returns one of the six values, or `None` to defer to the LLM path.

```
ORDER OF EVALUATION (first match wins):

1. message.strip() == "" or len(message) < 4
   → CHAT
2. starts with greeting/affirmation pattern
   ^(hi|hello|hey|sup|yo|thanks|thank you|ok|sure|cool|nice|got it|right|y(?:eah|es|up)|n(?:o|ah|ope))\b
   → CHAT
3. matches "X vs Y" / comparison patterns
   \b(vs\.?|versus|compared? to|differences? between|which is better|should i (?:use|pick|choose)|or)\b
   → COMPARISON
4. matches creative-writing patterns
   ^(write (?:me )?a (?:story|poem|haiku|scene|essay|monologue)|tell me a story|describe (?:a |the )?(?:scene|setting)|continue the story|roleplay)
   → CREATIVE
5. matches code/technical patterns
   \b(write|implement|fix|debug|refactor|optimi[sz]e) (?:a |the |this )?(function|method|class|component|module|script|test|loop|query|api|endpoint|migration)\b
   OR contains a code fence (```) in the message
   OR matches \b(in (?:python|javascript|typescript|rust|go|java|c\+\+|sql|bash))\b at end of message
   → TECHNICAL
6. matches tips/how-to patterns
   ^(give me |list |what are )?(?:tips|ways|best practices|hacks|ideas|examples) (?:on|for|to)
   OR ^how (?:do i|to|can i)
   OR ^(?:top|best) \d+\b
   → MODERN_BLOG
7. matches longform-explainer patterns
   ^(explain|why does|how does|what is the (?:meaning|history|theory|reason)|tell me about) \b
   AND message length ≥ 50 chars
   → EDITORIAL

Anything else → return None (defer to LLM)
```

The regex set is intentionally narrow. Better to defer ambiguous queries to the LLM than to mis-classify them confidently.

## §4. LLM path (extends `classify_user_intent`)

The existing `classify_user_intent()` LLM call gets one new field in its JSON schema:

```
{
  "intent_type": "...",
  "confidence": 0.0-1.0,
  "style": "editorial" | "modern_blog" | "technical" | "chat" | "comparison" | "creative"
}
```

Prompt addendum (one paragraph, appended to the existing intent-classifier prompt):

> Also classify the visual style this question deserves. Pick exactly one from: `editorial` (longform explainers, opinion, "tell me about X"), `modern_blog` (tips, how-tos, listicles, "give me ways to Y"), `technical` (code questions, debugging, technical reference), `chat` (greetings, thanks, short factual, casual), `comparison` ("X vs Y", "which is better"), `creative` (stories, poems, fiction, scene-writing). Prefer `modern_blog` when uncertain.

**Accuracy guardrail:** if the combined prompt drops `intent_type` accuracy on the existing fixture set by more than 5 percentage points, split into two sequential LLM calls within `classify_user_intent()`. Same fallback escape hatch as v1 §3.

**Timeout:** existing 1.5s hard cap unchanged. On timeout/error, regex fast path's None-result triggers fallback to `ResponseStyle.default()` (`MODERN_BLOG`).

## §5. Style charters (prompt fragments)

`app/services/response_style.py` exports `STYLE_CHARTERS: dict[ResponseStyle, str]`. Each charter is a focused 20-40 line addendum injected into the system prompt as `system_addition`. Not a giant rules dump — voice + structure rules + one example.

Sketch of the `modern_blog` charter (full content of all six lives in the registry):

```
You are writing in MODERN_BLOG style.

Structure:
- Open with a one-line hook in regular prose (no header).
- ## H2 topic with leading emoji (## ⏰ Time management essentials).
- 2-4 ### sub-headings, each with its own emoji (### 🎯 Core techniques).
- Bullets with topic-emoji prefix (- 📊 Eisenhower Matrix — sort by urgent × important).
- End with a "**Bottom line:**" one-liner that compresses the answer.

Voice: punchy, scannable, energetic. Bold key terms liberally.
Length: 150-400 words.

Example:
[concrete example of a well-formatted modern_blog answer]
```

Charters for the other five styles follow the same shape with content matching their visual signature (see §The six styles).

The existing 80-line "FINAL FORMAT MANDATE" block at the end of `DEVELOPER_INSTRUCTIONS` is **deleted** as part of this change. Charters do its job, per-question. The mandate's blanket "every answer must be richly formatted" rule is what currently produces over-formatted casual replies — removing it is the point, not a regression.

## §6. SSE first event

Same shape as v1 §5 used.

After classifier returns, before any content streams, the server emits:

```
event: stream-meta
data: {"style": "modern_blog", "via": "regex"}

```

The `via` field (`regex` | `llm` | `default`) is for telemetry/debugging — frontend ignores it. Extensible for future fields (confidence, alternate styles, etc.).

If the event is missing or malformed (network hiccup, older client, server-side fallback failure), the frontend falls back to existing post-stream `classifyContext()` and its `data-ctx` attribute. **No regression.**

## §7. Frontend wiring

**`codeWindow.js`** — the SSE consumer that constructs `StreamRenderer` already knows the response stream. Add one branch:

```js
// Before content events arrive — first event is `stream-meta`.
if (event.event === 'stream-meta') {
  try {
    streamMeta = JSON.parse(event.data);   // { style: "...", via: "..." }
  } catch (_) { /* ignore — fall through to default */ }
  return;  // not a content chunk
}
```

`streamMeta` (default `{}`) is passed into the `StreamRenderer` constructor.

**`streamRenderer.js`** — constructor reads `streamMeta.style` and stamps `data-style` on `messageEl` immediately:

```js
if (streamMeta && streamMeta.style && VALID_STYLES.has(streamMeta.style)) {
    inner.dataset.style = streamMeta.style;
    if (this.wrapperEl) this.wrapperEl.dataset.style = streamMeta.style;
}
```

`VALID_STYLES` is a `Set` of the six allowed values, hard-coded in JS. Unknown values are ignored — `data-style` simply doesn't get set, fall-through to existing CSS.

`data-style` and `data-ctx` coexist: `data-style` (server-decided, set at t=0) drives typography; `data-ctx` (client-classified, set at t=stream-end) drives column width and layout-tier rules. They are orthogonal vocabularies.

## §8. CSS contexts

`app/static/css/stream-styles.css` (new file). Six scoped blocks:

```css
[data-style="editorial"] .text-message { /* serif body, italic lede,
    small-caps section labels, considered spacing rhythm */ }
[data-style="editorial"] .text-message .stream-block.block-paragraph:first-child {
    /* lede styling */
}
[data-style="editorial"] .text-message h2 {
    font-family: 'Georgia', serif;
    font-style: italic;
    font-weight: 700;
}
/* … etc per style */

[data-style="modern_blog"] .text-message { font-family: 'Inter', system-ui, sans-serif; }
[data-style="modern_blog"] .text-message h2 { font-weight: 800; letter-spacing: -0.02em; }
[data-style="modern_blog"] .text-message blockquote {
    /* The MODERN_BLOG charter instructs the model to emit "Bottom line:"
       lines as `> **Bottom line:** ...`, so blockquote becomes the callout
       container — no special class plumbing needed. */
    background: rgba(100,200,255,0.08);
    border-left: 3px solid rgba(100,200,255,0.6);
    padding: 8px 12px;
    border-radius: 4px;
}
/* … etc */
```

Concrete rule sets are derived directly from the brainstorming mockups (`.superpowers/brainstorm/.../visual-style-direction.html` and `.../style-coverage.html`). Implementation plan turns those into the CSS rules.

**Webfonts:**

- Inter — already loaded for the rest of the UI.
- Georgia — system serif, no extra load.
- JetBrains Mono — already loaded for code blocks.

No new font loads required for v1.

## §9. Fallback chain — never fail open

```
1. regex_match() returns a style → use it
2. else: classify_user_intent() LLM returns a style → use it
3. else: ResponseStyle.default() (MODERN_BLOG) → use it

At every level, a valid style is always returned. The frontend always
receives a stream-meta event with one of the six values.

Frontend-side safety net: if stream-meta event is missing/malformed,
data-style is not set; existing data-ctx-driven CSS still renders
the message. The chat does not break.
```

## §10. Test plan

**Acceptance — regex fast path:**

| Input | Expected style | Path |
|-------|---------------|------|
| `"hi"` | `chat` | regex |
| `"thanks!"` | `chat` | regex |
| `"react vs vue?"` | `comparison` | regex |
| `"write a python function to debounce"` | `technical` | regex |
| `"write me a haiku about coffee"` | `creative` | regex |
| `"give me tips on time management"` | `modern_blog` | regex |
| `"explain how RAG works in detail"` | `editorial` | regex |
| `"the vibes are off in this codebase"` | None → LLM | regex returns None |

**Acceptance — LLM fallback path:** mock the LLM to return each style; assert the same SSE event is emitted and the same charter is injected.

**Fallback test:** simulate LLM timeout. Expect `MODERN_BLOG` to be picked, charter injected, SSE event fires, frontend renders with `data-style="modern_blog"`.

**Frontend integration test:** start a stream, inspect SSE; assert first event is `stream-meta` and arrives before any content event. Assert `messageEl.dataset.style` is set before the first paint.

**Regression — intent classifier accuracy:** existing intent fixture set must not drop more than 5 percentage points after the LLM prompt addendum. If it does, split into two LLM calls (per v1 §3 / our §4 guardrail).

**Visual smoke:** manual review of the six rendered styles against the brainstorm mockups. Specific check: a `chat` reply must NOT have headings or emoji bullets; a `creative` reply must NOT have any emoji.

## §11. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Classifier picks wrong style for edge-case query | LLM fallback handles ambiguity; `MODERN_BLOG` default is safe. Add a small "user override" hatch in v1.5 if mis-classification rate is annoying. |
| Charters drift from CSS contexts (model produces emoji bullets but CSS expects something else) | Each charter has a concrete example whose markdown is rendered through the CSS to verify they're aligned. Visual smoke test catches regressions. |
| LLM prompt addendum harms intent accuracy | Measured pre/post; split-call fallback already specced. |
| `stream-meta` SSE event breaks older clients | Additive event; clients that don't handle it ignore it; existing `classifyContext()` post-stream is the safety net. |
| Removing the FORMAT MANDATE block makes responses regress to flat prose for users on `MODERN_BLOG` default | The `MODERN_BLOG` charter is itself heavy with structure rules. Default behavior remains "format like a blog post." This is the same outcome the mandate was after, but per-question instead of blanket. |
| Unknown `data-style` value from server | Frontend whitelists the six values; unknowns are ignored. |

## §12. Rollback

Single feature flag: `RESPONSE_STYLE_ORGANISM_ENABLED` (env var, default `false` for first deploy → `true` after verification).

When `False`:
- `classify_response_style()` is not called.
- No `system_addition` charter is injected (existing `DEVELOPER_INSTRUCTIONS` is the full prompt).
- No `stream-meta` SSE event is emitted.
- Frontend falls through to existing post-stream `classifyContext()`.

Zero-risk rollback path. Flip the flag, restart the app, behavior is exactly as before the change.

The flag is removed in a follow-up PR after one week of verified-working production traffic.

## §13. Removal of the FINAL FORMAT MANDATE block

The 80-line `🚨 FINAL FORMAT MANDATE` block currently in `DEVELOPER_INSTRUCTIONS` (added in this conversation's earlier rounds) is **deleted** as part of this change. Charter-per-question replaces blanket-mandate.

The earlier RESPONSE FORMATTING section's concrete bad-vs-good time-management example is preserved but moved into the `MODERN_BLOG` charter where it belongs.

---

## Summary

**What ships:** a six-style response-style classifier (regex fast path + LLM fallback), per-style prompt charters injected as `system_addition`, a `stream-meta` SSE first event carrying the chosen style, and a `data-style` attribute on the message wrapper that drives a new `stream-styles.css` containing six typography rule sets.

**Why this and not v1:** v1 picked a layout vocabulary; this picks a visual style. Layouts (column width, line-height tier) are already handled by the existing `data-ctx` system. What was missing — and what the user actually asked for — is editorial typography per content type. Six styles, one decision per turn, end-to-end.

**What's deferred:** per-user preferences, A/B, mid-stream switching, feedback loop. Each warrants its own spec once v1 of the organism shows the foundation works.

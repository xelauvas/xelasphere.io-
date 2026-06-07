# Smart, Context-Adaptive Message Rendering — Design Spec

Date: 2026-06-07
Status: Approved (brainstorming) — pending implementation plan
Area: Chat message rendering (`app/static/js/streamRenderer.js`, `stream/renderers/*`,
`premiumFeatures.js`), response styling CSS, and the structured-output path
(`app/services/chat_engine.py`, `app/blueprints/chat_routes.py`).

## Overview

Make the chat window adapt the *presentation* of an answer to the *content and
context* of that answer — the way ChatGPT does — while keeping the existing
intent "style personas" (editorial / modern_blog / technical / chat / comparison /
creative). This adds four capabilities on top of the current renderer:

1. **Adaptive density & width** — the already-computed content context drives real
   layout (reading width, leading, spacing), not just font tweaks.
2. **Auto-structure detection** — high-confidence content shapes (definition runs,
   step sequences, model tables, stat lines) get upgraded rendering. Additive and
   conservative; never fabricates structure from free prose.
3. **Collapsible long code** — long code blocks collapse to a preview with a
   soft-wrap toggle and "Open in artifact".
4. **Follow-up chips** — model-generated next-question suggestions under the latest
   answer, prefilling the composer on tap.

This is an *enhancement layer*, not a rewrite. The streaming engine is untouched on
the hot path. Everything ships behind a global `window.SMART_RENDER` flag so each
feature can be toggled and reverted independently.

## Goals

- Presentation adapts to what the content actually is, consistently and predictably.
- Keep the six intent personas working exactly as today.
- Zero changes to the per-token streaming path; all new work runs once at finalize.
- Each feature independently toggleable and reversible (taste is subjective here;
  see the φ-pass history — a full golden-ratio pass shipped and was reverted as ugly).

## Non-Goals (YAGNI)

- No "show more / collapse" for long *messages* (un-ChatGPT-like; dropped).
- No speculative prose → table conversion. We enhance structure the model already
  emitted; we do not invent it.
- No client-side heuristic fallback for follow-up chips — chips appear only when the
  model supplies them (avoids generic filler).
- No new server "style" categories; no model swap.
- No auto-send on chip tap (prefill + focus only).

## Current State (as built)

- **Server** emits a `style` field in its structured output (chat_engine prompt),
  with a regex fallback in `response_style.py`; delivered to the client via StreamMeta.
- **Client** streams SSE → block state machine → block AST → per-type renderers
  (`prose` = markdown-it + DOMPurify, `code` = Prism + language badge + copy, `list`,
  `math` = KaTeX, `mermaid`). Paced, smooth, with a cursor.
- Two adaptation signals land on the message DOM at finalize:
  - `data-style` = server intent.
  - `data-ctx` = client content profile from `classifyContext()`
    (`conversational · code-heavy · technical · list-heavy · structured · long-prose · prose`).
- `stream-styles.css` applies **structural** persona cues; aesthetics are unified.
  Today `data-ctx` only lightly tweaks fonts/margins.
- `premiumFeatures.js` already does post-render enhancement (emoji → colored callouts,
  terminal-style code) via a MutationObserver.
- Tables already have CSS; citations and collapsible reasoning already exist.

## Design

### Entry point

A single function `enhanceMessage(messageEl, ast, meta)` in a new module
`app/static/js/messageEnhancer.js`. Called at the two existing deterministic points
only:

1. `StreamRenderer.finalize()` (after the AST is frozen and `data-ctx` is set).
2. The history-replay render path (so reloaded conversations look identical).

It is **not** a MutationObserver and does **not** run per token. It guards every pass
behind `if (!window.SMART_RENDER) return;`, marks processed nodes with `data-enhanced`
flags (idempotent), and wraps each detector in its own try/catch so one failure can
never break the message. `ast` is the finalized block list (preferred); the DOM is the
fallback when replaying history without an AST.

### Component 1 — Adaptive density & width

`data-ctx` drives three CSS custom properties on the message text column, defined in a
new `app/static/css/smart-render.css`:

- `--msg-maxw` — reading width of the prose column.
- `--msg-leading` — line-height.
- `--msg-para-gap` — vertical rhythm between blocks.

Per-context values (starting point; tuned visually with the user):

| data-ctx        | --msg-maxw | --msg-leading | --msg-para-gap | notes |
|-----------------|-----------|---------------|----------------|-------|
| conversational  | 54ch      | 1.6           | 0.5rem         | compact bubble feel |
| prose           | 66ch      | 1.7           | 0.85rem        | comfortable reading |
| long-prose      | 68ch      | 1.75          | 1rem           | longform |
| list-heavy      | 62ch      | 1.6           | 0.6rem         | tighter list rhythm |
| structured      | 66ch      | 1.65          | 0.8rem         | document-like |
| technical       | 72ch      | 1.6           | 0.7rem         | wider for code context |
| code-heavy      | 74ch      | 1.55          | 0.7rem         | wider |

Rules:
- The prose column uses the custom properties; **code blocks, tables, and mermaid
  break out to the full message width** regardless of `--msg-maxw` (overflow-x where
  needed), matching ChatGPT.
- To avoid a reflow jump at finalize, a **provisional ctx** is seeded from
  `streamMeta.style` when streaming starts (e.g. `technical` → a code-leaning ctx,
  `chat` → conversational) and **refined** to the real `data-ctx` at finalize. Only
  spacing/width transition; no font-size churn.
- No `!important`. If `data-ctx` is absent, the column inherits today's defaults
  unchanged.

### Component 2 — Auto-structure detection

In `messageEnhancer.js`, run after finalize over the AST/DOM. Only high-confidence,
additive upgrades; each is purely presentational and reversible.

- **Definition runs.** ≥2 consecutive paragraphs or list items matching
  `^\s*\*\*(?<term>[^*]{1,40})\*\*\s*[:—-]\s+(?<def>.+)` (or `Term — value`) →
  render as a `<dl class="smart-deflist">` with aligned terms. Stops at the first
  non-matching line.
- **Step sequences.** An ordered list, or consecutive headings/paragraphs prefixed
  `Step \d+`/`^\d+\.`, that read procedural (≥2 items, imperative-ish) → wrap each in
  a `.smart-step` (number chip + body). Ordered lists are the primary trigger;
  heading-based steps require the explicit `Step N` token.
- **Model tables.** Existing markdown tables get the ChatGPT treatment via
  `.smart-table` wrapper: horizontal scroll on overflow, zebra rows, sticky header.
  The `comparison` persona keeps its accent header.
- **Stat lines.** A standalone paragraph matching
  `^\s*[A-Z][\w ]{1,30}:\s*[$€£]?\d[\d.,]*\s*(?:[KMB%]|\([+−-]?\d[\d.,]*%\))` →
  emphasize the figure (`.smart-stat`).

Hard rule: **no prose → table conversion.** A "X vs Y" answer only becomes a table if
the model emitted one. If a detector's match is not clean/complete, leave the original
rendering untouched.

### Component 3 — Collapsible long code

Extend `stream/renderers/code.js` (which already builds a `.code-header` with a
language badge + copy button):

- When a finalized code block exceeds **24 rendered lines**, collapse it to a
  ~16-line preview with a bottom fade and a `Show all (N lines)` toggle.
- Add a **soft-wrap toggle** to the header (off by default; persists per-session).
- Add an **"Open in artifact"** button that loads the block into the existing artifact
  panel (reuses current artifact open API). Shown for code blocks (any language);
  primary value for long/runnable code.

State (collapsed, wrap) is per-block and survives re-highlight. Short blocks are
unaffected.

### Component 4 — Follow-up chips (model-generated)

- **Server.** Extend the structured output the model already produces (same JSON that
  carries `style`) with an optional `follow_ups` array of ≤3 short next-question
  strings. Prompt change in `chat_engine.py`; tolerant parse (missing/empty → none).
  Pass `follow_ups` through the `done` payload in `chat_routes.py` (default `null`).
- **Client.** `app/static/js/followups.js` renders ≤3 chips beneath the **latest
  finished** assistant message when `done.follow_ups` is non-empty. Tapping a chip
  fills the composer with that text and focuses it (no auto-send). Chips are removed
  when the next user message is sent and are never shown on historical (non-latest)
  turns.

### Data flow

```
stream finalize
  → data-ctx set (existing)
  → smart-render.css density/width applies (CSS, automatic)
  → enhanceMessage(): definition / step / table / stat passes (idempotent)
  → code renderer: collapse + wrap + open-in-artifact wired
done payload (has follow_ups?)
  → followups.js renders chips under the latest message
```

All client-side except the `follow_ups` field added to the structured output and
forwarded in the `done` payload.

### Error handling

- `enhanceMessage` and every detector are individually try/caught; a throw leaves the
  original block intact and logs once.
- Idempotency via `data-enhanced` / `dataset` flags; safe to call twice (finalize +
  replay).
- `follow_ups` parsing is tolerant; any shape other than a non-empty array of strings
  yields no chips.
- Global kill switch: `window.SMART_RENDER = false` disables all four passes and the
  density tokens, restoring today's exact rendering.

## Files Touched

- New: `app/static/js/messageEnhancer.js` — `enhanceMessage` + the four detectors.
- New: `app/static/js/followups.js` — chip component.
- New: `app/static/css/smart-render.css` — density tokens, deflist/step/table/stat,
  collapsible code, chips.
- Modified: `app/static/js/streamRenderer.js` — call `enhanceMessage` at finalize;
  seed provisional ctx from `streamMeta.style`.
- Modified: `app/static/js/stream/renderers/code.js` — collapse + wrap + open-in-artifact.
- Modified: `app/static/css/stream-styles.css` (or message-styles.css) — wire
  `--msg-*` consumption into the text column.
- Modified: `app/services/chat_engine.py` — `follow_ups` in the structured-output
  schema/prompt.
- Modified: `app/blueprints/chat_routes.py` — forward `follow_ups` in the done payload.
- Modified: `app/static/js/codeWindow.js` — on `done`, hand `follow_ups` to followups.js.
- Modified: `app/templates/index.html` — load the two new scripts + CSS; cache bumps.

## Testing

- **Unit** (detectors, pure functions): definition run (match / partial-no-match),
  step sequence (ordered list / `Step N` / negative), stat line (match / negative),
  table wrapper. Assert resulting DOM/structure and idempotency.
- **Headless render harness** (the existing Flask-render + headless-browser setup):
  render sample assistant messages for each `data-ctx` and each structure type;
  screenshot; assert the density vars resolve, the enhanced nodes exist, code collapse
  toggles, chips render and prefill the composer, and there are no console errors.
- **Visual iteration** with the user (taste authority) before enabling each feature.

## Rollout

Build behind `window.SMART_RENDER`. Verify headless, deploy to `/opt/xelasphere` with
cache-version bumps and a graceful reload, sync to the dev repo. Enable the features
one at a time after visual sign-off; the flag allows instant revert of any piece.

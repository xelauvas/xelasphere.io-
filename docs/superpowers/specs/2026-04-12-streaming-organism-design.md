# Streaming Rendering Organism

**Date**: 2026-04-12
**Status**: Proposed
**Scope**: Evolve `StreamRenderer` into a block-oriented streaming pipeline with state-machine block detection, AST isolation, per-type renderers for code/lists/math/mermaid, content-type pre-warming, and gentle per-block entrance animations.
**Supersedes**: Builds on top of `2026-04-10-stream-renderer-design.md`. Does not replace it; that spec remains the foundation for the SSE → DOM path.

## Motivation

XelaSphere is positioning against Claude and ChatGPT. Both of their UIs re-render more than they need to, pop code blocks in whole at the end of a response, flash half-rendered LaTeX, and lose the thread when tokens burst-and-pause. None of that is catastrophic — it's just uncraft.

This spec is a craft bet: the interface should feel alive in a way users notice without being able to name why. Secondary goal: the block-AST substrate this introduces is the foundation for future inline structured content (cards, widgets, action buttons) without further architectural change.

This is not a bug-hunt. Nothing user-visible is broken today. The current StreamRenderer is solid within its model. This spec replaces that model with a more granular one.

## Scope

### In scope

- **L2** — Block-level streaming state machine. Hand-rolled. Replaces `findSafePoint` in `streamRenderer.js`.
- **L3** — Block AST with frozen/live isolation. The structural core.
- **L4 (scoped)** — Per-type renderers for: code blocks, lists, math (`$$…$$` and `$…$`), mermaid. Tables are explicitly deferred.
- **Pre-warm** — Response-type inference from first ~100 characters of the stream, adapting the container class.
- **Entrance animations** — Gentle (opacity + 4px slide, 80–120ms), per-block-type, CSS-only.

### Out of scope

- **L1 (velocity normalizer)** — Rejected. Intentional latency for smoothness is a user-preference land-mine and we are not willing to slow fast responses.
- **Range-tracking cursor** — Rejected. Current CSS-only inline blinking line is correct. `getBoundingClientRect` per chunk is a mobile CPU risk with no perceptible visual win.
- **Table streaming** — Deferred. Hardest to stream correctly; rare in XelaSphere's query mix. Revisit in v2 only if real users paste tabular data.
- **Parser swap (marked → micromark)** — Rejected. See "Parser Decision" below.
- **Shiki** — Rejected. Prism is already loaded on every page; shiki is async-heavy and overkill for v1.
- **hljs** — Rejected. Prism is already present, so introducing hljs would add a second highlighter for no reason.

## Parser Decision: Keep `markdown-it` (via the `window.marked` shim)

The codebase uses `markdown-it` as the actual markdown library. `index.html` defines a shim that exposes it as `window.marked.parse(text)` so call sites read as `marked.parse(…)` while the real parser is `markdown-it`. This spec preserves that shim; every `marked.parse` reference below resolves to `markdown-it` underneath.

The original pitch recommended `micromark` for streaming-friendly event emission. That advice is correct for a greenfield renderer where the parser *is* the stream processor. It does not apply here, because L3 (block AST) solves the performance motivation architecturally:

1. Block-level detection is hand-rolled in L2 regardless of which parser sits underneath. `micromark` doesn't remove that work; it still wants complete constructs to emit clean events.
2. Under L3, only the **live (tail) block's text** is re-parsed per frame — at most a paragraph or list-item, a few hundred characters. `markdown-it` on 200 chars is sub-millisecond. The O(n²) problem in the current implementation is an architecture problem, solved by L3, not a library problem.
3. Code, math, and mermaid blocks bypass the markdown parser entirely — they are routed by L2 to their per-type renderers.
4. `markdown-it`'s only remaining job is inline formatting (bold/italic/links/inline-code) inside prose, heading, list-item, and blockquote blocks. Small, complete inputs. Exactly its strength.
5. Swap cost (ESM integration, bundler change, DOMPurify rewire, safe-boundary re-derivation, team familiarity) is non-trivial for zero architectural benefit once L3 is in place.

Decision: keep `markdown-it` (via the `window.marked` shim) for inline parsing inside per-block renderers. Hand-roll L2. Use **Prism.js** (already loaded on every page with an autoloader and js/css/python components pre-registered) for code-block syntax highlighting — no new dep, no lazy-load step.

## Architecture Overview

```
SSE chunk
   │
   ▼
StreamController.append(text)
   │
   ▼
L2: Block State Machine
   │   scans raw buffer; emits events:
   │     BLOCK_START { type, meta }
   │     BLOCK_TEXT  { text }
   │     BLOCK_END   { reason }
   ▼
L3: Block AST (ordered array of Block nodes)
   │   frozen blocks: never mutated, DOM never touched again
   │   live block (tail): re-rendered per RAF frame
   ▼
L4: Per-Type Renderer (dispatched by block.type)
   │   prose/heading/list-item/blockquote → marked.parse(block.text) → DOMPurify → morphdom patch
   │   code        → Prism.highlightElement on <code class="language-{lang}"> → <pre><code> DOM
   │   math        → buffer until close → KaTeX render
   │   mermaid     → buffer until close → mermaid.render
   │   list        → per-item stagger animation
   ▼
Pre-warm (once, after first ~100 chars):
   container class mutation — .stream-layout-{prose|structured|code-heavy|enumerated}
```

## L2: Block State Machine

Single-pass, character-level scanner over the raw buffer. Operates on the unscanned suffix only (cursor advances monotonically). Not a full CommonMark parser — only recognizes block openers at line-start.

### States

```
PARAGRAPH     (default)
HEADING       # … newline
CODE_FENCE    ```lang … ```
LIST_UL       - /+/*  item … blank-line-or-non-list
LIST_OL       1. item … blank-line-or-non-list
BLOCKQUOTE    > line …
MATH_BLOCK    $$ … $$
MERMAID       ```mermaid … ```  (special-case of CODE_FENCE)
```

### Transitions

All transitions fire on line-start characters (position 0 of a line). Triggers listed with a "line-start" guard must match at column 0; otherwise the character is treated as content.

| Trigger (line-start unless noted) | Action |
|---|---|
| `` ``` `` followed by `mermaid` | end current block; start MERMAID |
| `` ``` `` optional language tag | end current block; start CODE_FENCE |
| `` ``` `` while in CODE_FENCE or MERMAID | emit `BLOCK_END { reason: 'natural' }`, return to PARAGRAPH |
| `#{1,6} ` | end current block; start HEADING (single-line) |
| `- `, `+ `, `* ` | if not in LIST_UL: end current, start LIST_UL. Otherwise: emit list-item boundary |
| `\d+\. ` | same as above for LIST_OL |
| `> ` | end current block; start BLOCKQUOTE |
| `$$` (line-start only — guards against inline `$$50`) | end current block; start MATH_BLOCK |
| `$$` while in MATH_BLOCK (line-start only) | emit `BLOCK_END { reason: 'natural' }`, return to PARAGRAPH |
| non-empty line matching no opener, while in LIST_UL/LIST_OL | append to current list item's text (continuation line — not a new item, not a new paragraph) |
| other | if no active block: start PARAGRAPH |
| blank line (`\n\n`) while in PARAGRAPH/LIST_UL/LIST_OL/BLOCKQUOTE | end current block |

Inline `$…$` math is not a block trigger; it stays inside prose blocks and is handled by KaTeX auto-render on `BLOCK_END` of the enclosing prose block.

### Events emitted

```
BLOCK_START { id, type, meta: { lang?, ordinal? } }
BLOCK_APPEND { id, text }                  // text added to live block
LIST_ITEM_START { blockId, itemIndex }     // within LIST_UL / LIST_OL
LIST_ITEM_END { blockId, itemIndex }
BLOCK_END { id, reason: 'natural' | 'finalize' }
```

### Safe-boundary semantics inside PARAGRAPH

Inline inside-prose constructs (`**bold**`, `*em*`, `` `code` ``, `[text](url)`) are handled the same way as the current `findSafePoint`: hold back the open construct's opener until closed. This runs only on the live block's unfrozen text — not on the whole buffer.

### Code-fence special cases

- **Open fence, no close yet**: emit `BLOCK_START { type: 'code', meta: { lang } }` immediately. Subsequent lines emit `BLOCK_APPEND`. Renderer shows skeleton immediately, highlights incrementally.
- **`mermaid` language tag**: routed to `MERMAID` block type instead of `CODE_FENCE`. Buffered, not streamed. No incremental render.
- **Unclosed at stream end (`finalize`)**: emit `BLOCK_END { reason: 'finalize' }`; renderer shows what was received.

## L3: Block AST

### Schema

```js
block = {
  id: 'b{n}',           // stable, monotonic
  type: 'paragraph' | 'heading' | 'code' | 'list-ul' | 'list-ol' | 'blockquote' | 'math' | 'mermaid',
  text: '',             // raw markdown text inside the block
  meta: {},             // type-specific: { level } for heading, { lang } for code, { items: [] } for lists
  frozen: false,        // true once BLOCK_END fires
  el: HTMLElement,      // the DOM node owned by this block
}
```

### Lifecycle

1. `BLOCK_START` → create block, append to `blocks[]`, create DOM element, append to message container, run entrance animation.
2. `BLOCK_APPEND` → update `block.text`, re-render only this block's DOM.
3. `BLOCK_END` → set `frozen = true`. Block's DOM is **never touched again** for the rest of the stream.

### Isolation guarantee

`morphdom` diffs are scoped to the **live block's element only**, not the message container. Frozen blocks sit in the DOM untouched. This is the core win: a 2000-word response re-renders ~200 characters per frame, not 12,000.

### List-item sub-isolation

Inside a `LIST_UL`/`LIST_OL` block, items are sub-nodes. Completed items are sub-frozen — their `<li>` DOM is stable once the next `\n- ` arrives. Only the tail `<li>` re-renders per frame.

## L4: Per-Type Renderers

### Prose / Heading / Blockquote

- Input: `block.text` (raw markdown).
- Pipeline: `marked.parse(text)` → `DOMPurify.sanitize(html)` → `morphdom(block.el, html, { childrenOnly: true })`.
- Same pipeline as today, but scoped to one block's text, not the whole buffer.
- Keep the existing `onBeforeElUpdated` guard for `.citation-pill` and `.sources-btn`.

### Code blocks

- Input: raw text (no markdown parsing).
- Highlighter: **Prism.js**, already loaded on every page. No lazy-load, no new dep.
- DOM: `<pre class="block-code"><code class="language-{lang}">…</code></pre>`.
  - `{lang}` comes from the fence tag (e.g. `` ```python `` → `language-python`).
  - If the fence tag is empty or unrecognized, use `language-plaintext` — Prism handles this natively.
  - Prism's autoloader picks up the `language-*` class and loads the grammar on first use.
- On each `BLOCK_APPEND`: update `code.textContent` with the full block text, then call `Prism.highlightElement(codeEl)`. Prism re-highlights in place; it's fast enough for streaming (<1ms for typical block sizes).
- Skeleton: on `BLOCK_START` with no text yet, render `<pre class="block-code is-streaming"><code class="language-{lang}"></code></pre>`. CSS adds a shimmer on `.is-streaming`. Remove `.is-streaming` class on `BLOCK_END`.
- Copy button: added on `BLOCK_END`, not before (prevents flicker).

### Lists

- Input: `block.text` — the whole list as a markdown string.
- State machine tracks item boundaries via `LIST_ITEM_START` / `LIST_ITEM_END`.
- DOM: `<ul>` or `<ol>`, one `<li>` per item. Each `<li>`'s inline text is rendered with `marked.parse` (for inline formatting inside items).
- Per-item entrance animation: `animation: blockIn 60ms ease-out; animation-delay: calc(var(--i) * 30ms)`.
- `--i` is set via `style="--i: {itemIndex}"` on each `<li>`.
- Only the tail `<li>` re-renders on `BLOCK_APPEND`. Completed items are sub-frozen.

### Math (KaTeX)

- Input: full text between `$$` opener and `$$` closer (block math), or `$` pair (inline — handled by the prose renderer, not as its own block).
- Buffered: no incremental render. While `MATH_BLOCK` is live, show a placeholder: `<div class="block-math is-pending"><span class="pulse-dots">…</span></div>`.
- On `BLOCK_END`: call `katex.render(text, el, { throwOnError: false, displayMode: true })`.
- On render failure: fall back to `<pre>` of the raw text with a `.math-error` class.

### Mermaid

- Input: full text between `` ```mermaid `` and `` ``` ``.
- Buffered. Placeholder while streaming: `<div class="block-mermaid is-pending"><span class="pulse-dots">…</span></div>`.
- On `BLOCK_END`: validate with `mermaid.parse(text)`; if invalid, render `<pre class="mermaid-error">` with the raw source. If valid, `mermaid.render(id, text)` and insert the SVG.
- Defer mermaid initialization until first `BLOCK_START { type: 'mermaid' }` (it's already loaded in the page, but `mermaid.initialize` runs lazily).

## Pre-Warm

Runs **once per stream**, when the raw buffer first crosses 100 characters (or on `BLOCK_END` if the stream is short).

```js
function inferLayout(firstChars) {
  if (/```/.test(firstChars)) return 'code-heavy';
  if (/^#{1,3} /m.test(firstChars)) return 'structured';
  if (/^\d+\. /m.test(firstChars)) return 'enumerated';
  return 'prose';
}
```

Adaptation: set `data-stream-layout="{value}"` on the message container. CSS rules:

- `prose` — reading-optimized max-width (~65ch), comfortable line-height.
- `structured` — wider max-width (~75ch), heading-oriented spacing.
- `code-heavy` — widest (~85ch), monospace-leaning tab-stops.
- `enumerated` — ordered-list-optimized indent and marker spacing.

Pre-warm happens once. It does not re-fire on later evidence — premature adaptation is less bad than mid-stream layout jitter.

## Entrance Animations

All CSS-only. One keyframe, per-type durations.

```css
@keyframes blockIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

.block-paragraph   { animation: blockIn 120ms ease-out; }
.block-heading     { animation: blockIn 120ms ease-out; }
.block-blockquote  { animation: blockIn 120ms ease-out; }
.block-code        { animation: blockIn  80ms ease-out; }   /* faster — feels technical */
.block-math        { animation: blockIn 100ms ease-out; }
.block-mermaid     { animation: blockIn 100ms ease-out; }
.block-list-item   { animation: blockIn  60ms ease-out; animation-delay: calc(var(--i) * 30ms); }
```

`prefers-reduced-motion: reduce` disables all `blockIn` animations.

Animation fires only on initial block creation (first paint of the block's DOM node) — not on `BLOCK_APPEND`. Implementation: apply the animation class in `BLOCK_START`, remove the class after `animationend` to prevent re-fire on morphdom patches that replace children.

## Cursor

Unchanged from current spec. CSS-only inline blinking line (`.stream-cursor`). Lives at the end of the live block's DOM. Removed on `finalize()`.

## Integration with Existing StreamRenderer

This is an **evolution**, not a replacement. The shipped `StreamRenderer` class (`static/js/streamRenderer.js`) keeps its outer contract:

```
new StreamRenderer(container)
  .append(text)
  .finalize()
  .destroy()
```

Callers (`codeWindow.js`, `startNewChatModule.js`, `voiceModeModule.js`) are not touched.

What changes: the **internals**. `findSafePoint` is replaced by the L2 state machine. The single `renderTarget` div is replaced by a block-AST-owned DOM structure. `_render` becomes a dispatcher that routes to per-type renderers.

The existing `ProgressiveTextDisplay` path (history replay, background session buffering) is not touched.

## Files

### Created

| File | Purpose |
|---|---|
| `static/js/stream/blockStateMachine.js` | L2 — character-level block detector, emits events |
| `static/js/stream/blockAST.js` | L3 — block array, frozen/live invariants |
| `static/js/stream/renderers/prose.js` | markdown-it (via `window.marked` shim) + DOMPurify + morphdom, scoped to one block |
| `static/js/stream/renderers/code.js` | Prism.highlightElement + skeleton + copy button |
| `static/js/stream/renderers/list.js` | per-item stagger, sub-frozen items |
| `static/js/stream/renderers/math.js` | buffered KaTeX render |
| `static/js/stream/renderers/mermaid.js` | buffered mermaid parse + render |
| `static/js/stream/preWarm.js` | layout inference, container class mutation |
| `static/css/stream-blocks.css` | block classes, entrance animations, skeleton shimmer, layout variants |

### Modified

| File | Change |
|---|---|
| `static/js/streamRenderer.js` | Outer class keeps API. Internals rewritten to delegate to the modules above. |
| `app/templates/index.html` | `<script type="module">` tags for new stream modules. Prism, markdown-it, KaTeX, Mermaid, morphdom already loaded — no new CDN additions. |

### Not modified

- `codeWindow.js`, `startNewChatModule.js`, `voiceModeModule.js` — callers of StreamRenderer, contract unchanged.
- `progressiveTextDisplay.js` — history replay path untouched.
- Backend (`app/services/stream_normalizer.py`, `chat_engine.py`) — zero changes. This is a pure client-side evolution.

## Performance Targets

- Live-block re-render per RAF frame: < 1ms at p95 (measure with `performance.mark`).
- Per-chunk CPU on a 2000-word response: flat, not growing with response length (the L3 isolation guarantee).
- No dropped frames during stream on a mid-range mobile device (target: iPhone 12 / equivalent Android).
- Prism autoloader latency (first code fence in an uncached language → first highlighted frame): < 200ms on fast 4G. Pre-registered languages (js, css, python) highlight instantly.

## Risks

- **Block-boundary edge cases**: markdown with nested constructs (list item containing a code block containing `$math$`) — the L2 state machine must be tested against a corpus. Mitigation: build a test suite of ~30 real LLM-output samples before wiring in.
- **Prism autoloader race**: if `BLOCK_APPEND` fires before the autoloader has fetched the grammar, `Prism.highlightElement` runs with no grammar and leaves the text unhighlighted. Mitigation: re-run `Prism.highlightElement` on `BLOCK_END` as a final pass; pre-registered languages (js/css/python) are unaffected.
- **Animation churn on very fast streams**: if blocks open and close within 50ms, the entrance animation never finishes. Acceptable — `animationend` cleanup handles it.
- **morphdom + animation interaction**: entering-block animation must not be interrupted by morphdom patches on the same element. Mitigation: animation is on the block's outer element; morphdom operates on `childrenOnly: true`.
- **Pre-warm false positives**: a prose response that happens to start with a `#` character (not a heading) mis-infers `structured`. Acceptable cost — the layout adaptation is subtle.

## Non-goals

- Not trying to be a CommonMark parser. L2 recognizes only the block openers listed above.
- Not supporting table streaming in v1.
- Not adding structured inline content (cards, widgets) in v1 — the AST supports them architecturally, but renderer types are the five listed.
- Not changing the SSE protocol or backend.
- Not touching the artifact path (`parsed_action.action === 'create_artifact'` still destroys the StreamRenderer and falls through, as today).

## Success criteria

- Side-by-side with ChatGPT and Claude on three canonical prompts (long code block, nested list, mixed prose + math), XelaSphere's streaming visibly feels smoother — demonstrable on a screen recording.
- **Hard number**: live-block re-render CPU is flat across response length — no more than 2× cost at 2000 words vs 200 words, measured via `performance.mark` / `performance.measure` around the RAF tick.
- Zero regressions in the existing streaming test set.
- The block AST is usable, next quarter, as the substrate for inline structured content without further pipeline rework.

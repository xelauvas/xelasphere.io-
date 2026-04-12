# Replay Convergence — single rendering pipeline for stream + history replay

**Date**: 2026-04-12
**Status**: Proposed
**Scope**: Make history replay of AI text messages use the same `StreamRenderer` pipeline as live streaming, so stream-time DOM and refresh-time DOM are byte-identical.
**Builds on**: `2026-04-12-streaming-organism-design.md` (merged in `fd73d45`).

## Motivation

The streaming organism shipped (commit `fd73d45`). Live streaming renders AI text via `StreamRenderer` → block AST → per-type renderers → `<div class="stream-block block-paragraph">…</div>` DOM. On page refresh, the same saved message renders via `appendTextMessage` → `marked.parse` → single `<div class="text-message">…</div>`. Content is identical (both consume raw markdown), but DOM shape differs, producing visible divergence for the user: "stream shows in one way, after refresh shows different way."

Two pipelines for the same content is a maintenance burden that only grows. The fix is to converge onto one.

## Scope

### In

- Route AI text messages on history replay through `StreamRenderer.append(fullContent).finalize()`.
- Add a `replay: true` option to `StreamRenderer` that suppresses block-entrance animations on every block it renders during that session.
- CSS helper `.stream-no-anim { animation: none !important; }`.
- One fixture + one Node test verifying large-input one-shot append through the state machine (complements existing corpus 030).

### Out

- User messages — keep `appendTextMessage`. Simple markdown, simple DOM, simple user-bubble styling. No need to involve the block AST.
- Tree view, Recipe Detector, Email Composer, Crypto Chart, Artifact, Image rendering — all keep their dedicated paths. These are not plain prose.
- Backend changes — zero.
- Cursor behavior on replay — the StreamRenderer constructor appends no cursor until the first `BLOCK_START`; `finalize()` removes any cursor. Since both are synchronous in the same tick on replay, no cursor is visible. No change needed.
- Post-stream "dead air" (4.7s server-side post-processing latency) — that's Issue B from the diagnosis, separate spec.
- Performance-mark instrumentation path — `_scheduleRender()` still fires on replay; not a regression.

## Rationale for animation gate

Live streaming: one message, user is watching it emerge. Entrance animation is earned — it reinforces the streaming feel.

History replay: 20–50 messages re-render at once on page load. Every message animating its blocks simultaneously is a wall of fade-and-slide the user didn't ask for. Same end-state visually, worse perceived performance, no craft benefit.

Gating at the renderer level rather than the CSS level is cleaner: the decision is "this render session is a replay" and the render code knows, rather than spraying class selectors based on ambient `body.is-replaying` state.

## Architecture

### Current state (post-merge)

```
┌─ live stream ─────────────────────────────────┐
│ SSE → StreamRenderer.append(token)             │
│     → L2 state machine                         │
│     → L3 block AST                             │
│     → per-type renderers                       │
│     → <.stream-block.block-{type}> DOM         │
└────────────────────────────────────────────────┘

┌─ history replay ──────────────────────────────┐
│ loadSession() → for each message:              │
│   appendTextMessage(container, text)           │
│     → marked.parse(text) + DOMPurify            │
│     → single <.text-message> innerHTML          │
└────────────────────────────────────────────────┘
```

### Target state

```
┌─ live stream ─────────────────────────────────┐
│ SSE → StreamRenderer(container)                │
│       .append(token) × N                       │
│       .finalize()                              │
└────────────────────────────────────────────────┘

┌─ history replay ──────────────────────────────┐
│ loadSession() → for each AI text message:      │
│   new StreamRenderer(container, {replay:true}) │
│       .append(fullContent)  // one shot        │
│       .finalize()                              │
└────────────────────────────────────────────────┘

Both paths: identical block AST → identical DOM → identical CSS → identical visual.
```

## Implementation detail

### `StreamRenderer` constructor accepts `replay` flag

```js
// streamRenderer.js
class StreamRenderer {
    constructor(container, { replay = false } = {}) {
        this.container = container;
        this.replay = replay;
        // ... existing init ...
    }
}
```

### Per-type renderers consult the flag

Each renderer's `create(block, messageContainer)` already exists. Add a reference to the parent renderer's `replay` flag through the block AST, or pass it in via renderer factories.

**Chosen approach**: pass the renderer itself (or the replay flag) to each renderer via a third argument to `create(block, messageContainer, opts)`, where `opts = { replay: boolean }`. The StreamRenderer instance owns the flag and forwards it on every `create` call via `_handleEvent`:

```js
// streamRenderer.js _handleEvent, BLOCK_START case
case 'BLOCK_START': {
    const block = this.ast.startBlock({
        id: event.id,
        type: event.blockType,
        meta: event.meta,
    });
    const r = rendererFor(block.type);
    r.create(block, this.messageEl, { replay: this.replay });
    if (!this.replay) {
        transferCursor(this.messageEl, block.el);
    }
    break;
}
```

Cursor transfer is also suppressed on replay — no cursor should ever flash on history load.

### Renderers append `stream-no-anim` when `opts.replay === true`

```js
// prose.js (and same pattern in code.js, list.js, math.js, mermaid.js)
create(block, messageContainer, opts = {}) {
    const el = document.createElement('div');
    const noAnim = opts.replay ? ' stream-no-anim' : '';
    el.className = `stream-block block-${block.type}${noAnim}`;
    el.dataset.blockId = block.id;
    messageContainer.appendChild(el);
    block.el = el;
    return el;
}
```

List renderer adds the no-anim class on each `<li>` as well:

```js
// list.js in update(), when creating new <li>
const noAnim = block.meta.replayMode ? ' stream-no-anim' : '';
li.className = `block-list-item${noAnim}`;
```

Where `block.meta.replayMode` is set by `list.js create()` from `opts.replay`:

```js
create(block, messageContainer, opts = {}) {
    // ... existing ...
    block.meta.replayMode = !!opts.replay;
    // ...
}
```

### CSS kill-switch

```css
/* stream-blocks.css */
.stream-no-anim {
    animation: none !important;
}
```

Single universal selector. Turns off `streamBlockIn`, `streamShimmer`, and any future animation applied to a stream-block element.

### History replay call site

In `app/static/js/startNewChatModule.js`, inside `renderMessage()` when rendering an AI text message, replace the existing `appendTextMessage` call with a StreamRenderer invocation. The exact location depends on the current branching logic inside `renderMessage` — need to identify the precise branch that routes AI prose to `appendTextMessage` and swap it there, leaving tree/recipe/email/artifact/image branches untouched.

Pseudocode:

```js
if (isAiTextMessage) {
    // build message wrapper as appendTextMessage currently does
    const renderer = new StreamRenderer(messageContentEl, { replay: true });
    renderer.append(aiContent);
    renderer.finalize();
} else if (isTreeStructure(aiContent)) {
    // existing path
} else if (isRecipe(aiContent)) {
    // existing path
} // ... etc
```

The message container (wrapper `<div>` with message id, header, footer) is still built by the existing `createMessageDiv` / `appendMessageMetadata` helpers. Only the `.message-content` inner gets fed through StreamRenderer instead of `appendTextMessage`.

## Files

### Modified

| File | Change |
|---|---|
| `app/static/js/streamRenderer.js` | Constructor accepts `{ replay }`; `_handleEvent` passes `opts` to `r.create()`; suppresses cursor transfer on replay. |
| `app/static/js/stream/renderers/prose.js` | `create` accepts `opts`, appends `.stream-no-anim` when `opts.replay`. |
| `app/static/js/stream/renderers/code.js` | Same pattern. |
| `app/static/js/stream/renderers/list.js` | Same pattern + propagates flag to item-level class. |
| `app/static/js/stream/renderers/math.js` | Same pattern. |
| `app/static/js/stream/renderers/mermaid.js` | Same pattern. |
| `app/static/css/stream-blocks.css` | Add `.stream-no-anim { animation: none !important; }` rule. |
| `app/static/js/startNewChatModule.js` | Route AI text messages on replay through StreamRenderer instead of `appendTextMessage`. |

### Created

| File | Purpose |
|---|---|
| `tests/stream/oneShotReplay.test.mjs` | Verifies `createBlockStateMachine` correctly processes a large one-shot `append()` — mixed markdown content with all 5 in-scope block types. |

### Untouched

- `app/blueprints/chat_routes.py`, `app/services/stream_normalizer.py`, `app/services/chat_engine.py`, `app/utilities.py` — backend, no changes.
- `app/static/js/codeWindow.js` — live streaming path already shipped; no changes needed. `appendTextMessage` remains for user messages.
- All other renderer / caller files.

## Contract and invariants

- `StreamRenderer` public API **backward compatible**. Existing call sites (`new StreamRenderer(container)`) continue to work; the `replay` option defaults to `false`.
- `renderer.create(block, messageContainer)` **backward compatible**. Existing two-arg callers continue to work; `opts` defaults to `{}`.
- CSS cascade: `.stream-no-anim` uses `!important` on a single property (`animation`). No other rule in the codebase is affected.
- DOM shape: a replay-rendered message matches a live-streamed message's final DOM block for block, class for class. The only difference is the presence of the `.stream-no-anim` class on each block element, which has no box/layout effect — purely suppresses the one-shot entrance animation.

## Testing

### Unit

- `tests/stream/oneShotReplay.test.mjs` — new. Feed a ~2 KB mixed markdown (prose + heading + code fence + ordered list + `$$…$$` math + mermaid) as ONE `append()`, then `finalize()`. Assert the emitted block-AST sequence matches the expectation produced by chunk-by-chunk feeding of the same content.

### Manual

- Send a fresh prompt live on production. Record DOM of `.message-content` via `el.outerHTML`.
- Hard-refresh the page.
- Record DOM of the same message on reload.
- Diff the two strings. Expected: identical except for the `.stream-no-anim` class on block elements on the replay side.

### Regression

- Send a prompt with each content type. Verify rendering identical to pre-merge production behavior for all block types.
- Load a session with 20+ messages. Verify no visible entrance animations flashing on page load.

## Performance

- Replay path is synchronous: `append(fullContent)` runs the state machine over N characters; `finalize()` closes. For a typical 5 KB message, this is sub-millisecond. For a session of 50 messages, replay cost is still well under 100 ms for the state machine work; rendering the DOM remains the bottleneck and is unchanged from the per-block renderer cost we already ship.
- No incremental / RAF-batched work on replay: one synchronous pass is the simplest possible execution model. Matches user expectation ("content is already there when I look at the screen").

## Risks

- **R1**: a renderer that consults `opts.replay` forgets to propagate it for its internal sub-elements (e.g. list items). Mitigated by the explicit list-renderer propagation spec above and the DOM-diff manual test.
- **R2**: the replay path is invoked from a site that assumes `appendTextMessage`'s return shape (a Promise). Verify call sites don't depend on that Promise resolution; if they do, wrap `renderer.finalize()` in `Promise.resolve()` at the call site.
- **R3**: a message is saved with `content` that has already been HTML-rendered somewhere (not raw markdown). Diagnosis says this does not happen on the current save path (`codeWindow.js:3739` saves `displayContent` which is stripped raw markdown), but add a defensive assert during implementation: if `content` contains `<` before processing, log a warning and still pass through (StreamRenderer will render it as-is since the state machine treats `<` as prose character).

## Non-goals

- Changing how live streaming renders.
- Changing backend streaming behavior or payload shape.
- Fixing post-stream "dead air" (Issue B from diagnosis) — separate spec.
- Unifying user-message rendering with StreamRenderer — YAGNI; `appendTextMessage` is fine for user markdown.
- Replacing or modifying tree/recipe/email/artifact/image renderers.

## Success criteria

1. **Byte-identical DOM on a fresh-prompt-then-refresh cycle**. Diff of `.message-content` before and after refresh is empty, ignoring the presence of `.stream-no-anim` class on replay-side blocks.
2. **Zero entrance animations on page load** for a session with 10+ historic messages. Visible on a screen recording.
3. **Existing live-streaming behavior unchanged** — animations still fire on live messages, perf page still reports ratio ≤ 2.0, harness page still passes all 6 cases, 59-test Node suite stays green.
4. **No backend logs errors**, no CSP violations, no new console warnings.

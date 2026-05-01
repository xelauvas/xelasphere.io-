# Style replay invariant — design

**Date:** 2026-05-01
**Branch:** `fix/stream-first-line-pop`
**Scope:** Sub-project A of the cleanup brainstorm A → B → C. Stops the live-vs-refresh styling mismatch and locks the fix with a runtime invariant assertion. Does not refactor render paths (B/C territory).

## Problem

An AI response renders one way during the live SSE stream and a *different* way after the page is refreshed and history reloads. The chat-engine classifies each user prompt into one of six styles (editorial / modern_blog / technical / chat / comparison / creative) and the per-style CSS at `app/static/css/stream-styles.css` is gated on a `data-style="..."` attribute. During live stream the attribute is set; on replay it was not, so the same message rendered with different typography depending on whether you'd just streamed it or just refreshed.

Earlier in this session we landed targeted fixes in two passes:

1. **Persistence + targeted patches** (PR #167-style scope): added a `style` field to `save_interaction`, threaded it through the four `renderMessage` call sites in `displayHistoricalMessage`, added a `style = null` destructured param to `renderMessage`, and updated every known `StreamRenderer` construction site to pass `streamMeta: style ? { style } : {}` (or the live `streamMeta` variable for in-stream rebuilds).

2. **Defensive sweep** (when targeted patches alone weren't enough): added a sweep at the end of `renderActivePage` in `app/static/js/startNewChatModule.js` that iterates persisted messages, builds a `response_id → style` map, and walks every `.ai-message` in the DOM applying `data-style` from the map if missing.

The sweep is verified working in production: hard-refreshing the chat shows every historical AI bubble carrying the correct uppercase diagnostic pill matching the persisted style.

## Decision

**A2 — sweep + invariant assertion.** Keep the sweep (it works and is robust against future render-path additions). Add a runtime assertion at the end of the sweep that fires `console.error` if any AI bubble whose persisted message has a `style` value ends up without that `data-style` on the DOM.

Rejected alternatives:

- **A1 (patched, no test).** Sweep alone. Fragile against future regressions because a future refactor could break the sweep silently.
- **A3 (architectural fix).** Identify the singular broken render path and fix it directly, drop the sweep. Rejected because the wide grep + audit didn't pin a single path; multiple render paths (live-stream, image, search-banner, artifact-reseed, history-replay, fallback) can produce bubbles, and adding new paths is common in this codebase. The sweep enforces correctness post-hoc regardless of which path created the bubble — strictly more robust.

## Architecture

The invariant: for every `.ai-message` in the DOM whose corresponding persisted message has a non-empty `style` field, the `.text-message-container` inside that bubble carries `data-style` matching the persisted style.

The sweep enforces the invariant. The assertion verifies it held after the sweep ran.

### What's already in code (the sweep)

`app/static/js/startNewChatModule.js`, in `renderActivePage`, immediately after the `messages.forEach(... displayHistoricalMessage(message))` block. Already shipped at `?v=19`.

```js
try {
    const styleByRid = new Map();
    messages.forEach(m => {
        if (m && m.response_id && m.style) styleByRid.set(m.response_id, m.style);
    });
    if (styleByRid.size && this.elements.chatDisplay) {
        this.elements.chatDisplay.querySelectorAll('.ai-message').forEach(msgEl => {
            const id = msgEl.id || msgEl.dataset.messageId || '';
            const rid = id.replace(/^ai-/, '');
            const style = styleByRid.get(rid);
            if (!style) return;
            const tmc = msgEl.querySelector('.text-message-container');
            const inner = tmc && tmc.querySelector('.text-message');
            if (tmc && !tmc.hasAttribute('data-style')) tmc.setAttribute('data-style', style);
            if (inner && !inner.hasAttribute('data-style')) inner.setAttribute('data-style', style);
        });
    }
} catch (_e) { /* sweep is best-effort */ }
```

### What this design adds (the assertion)

Appended to the sweep block, inside the same `try`:

```js
const violations = [];
this.elements.chatDisplay.querySelectorAll('.ai-message').forEach(msgEl => {
    const id = msgEl.id || msgEl.dataset.messageId || '';
    const rid = id.replace(/^ai-/, '');
    const expected = styleByRid.get(rid);
    if (!expected) return;
    const actual = msgEl.querySelector('.text-message-container')?.getAttribute('data-style');
    if (actual !== expected) {
        violations.push({ rid, expected, actual: actual || '(missing)' });
    }
});
if (violations.length) {
    console.error('[style-replay] invariant violated for', violations.length, 'bubble(s):', violations);
}
```

Self-contained; reads the DOM and the in-scope `styleByRid` map only.

## Components / data flow

- **Producer** — `renderActivePage` already builds `styleByRid` for the sweep. The assertion reuses that map. No new data plumbing.
- **Consumer** — DevTools console. Anyone looking at the console of a session where the invariant breaks sees an error with rid / expected / actual for every violator.
- **Telemetry path (deferred):** structured `console.error` with a stable prefix (`[style-replay]`) makes it greppable in browser logs and easy to wire to an `/api/log` endpoint later if the user wants persistent telemetry. Out of scope for this spec.

## Acceptance criteria

After this design ships:

- The sweep block at end of `renderActivePage` ends with the assertion.
- Hard-refreshing a chat with mixed-style AI history continues to render every bubble with its persisted style (no regression of the working sweep behavior).
- Manually corrupting `data-style` via DevTools and triggering re-render produces a `[style-replay] invariant violated` console error within the same render cycle.
- The cache-bust query string on `startNewChatModule.js` is bumped to `?v=20` so browsers fetch the new file.
- Source `fix/stream-first-line-pop` and deployed `/opt/xelasphere/` are byte-identical for the touched files.
- xelasphere.service restart picks up the change without errors.

## Out of scope

- Replacing the sweep with a single canonical render path (B/C work).
- Adding a JS test framework. The codebase has none today; introducing one is its own decision.
- Wiring telemetry from the assertion to a server endpoint. The console.error is the entire feedback channel for this spec.
- Server-side validation that all persisted AI messages have `style`. The sweep simply ignores messages without a style field, which is the correct behavior for legacy data.
- Refactoring `codeWindow.js` size. That's C scope.
- ChatGPT-grade visual polish (typography, code blocks, spacing). That's B scope.

## Risks

- **False alarms.** If a future change deliberately suppresses a style (e.g. for a guest-mode or shared-link view that should render unstyled), the assertion would fire. Mitigation: any such suppression should clear the message's `style` field at the data layer rather than removing `data-style` from a bubble that has it, so the violation surfaces a real architectural mistake.
- **Console-noise risk.** If the sweep ever fails partially in production, the assertion will log on every page-load until fixed. That's a feature for catching real regressions, not a bug.
- **Test infrastructure absent.** Without a test framework we can't unit-test the assertion. Mitigation: the assertion *itself* is the test — it runs on every real session.

## Implementation steps (high level — detailed plan in writing-plans output)

1. Edit `app/static/js/startNewChatModule.js` to append the assertion block to the sweep `try`.
2. Bump `?v=19` → `?v=20` on the `startNewChatModule.js` `<script>` tag in `app/templates/index.html`.
3. Mirror both files to `/opt/xelasphere/`.
4. Verify byte-identity, JS syntax-check.
5. Restart `xelasphere.service`.
6. Manually verify: open chat, hard-refresh, confirm no `[style-replay]` errors. Then corrupt a `data-style` attribute via DevTools, trigger a session reload, confirm the assertion fires.
7. Commit on `fix/stream-first-line-pop`.

No push, no merge into main this session — same discipline as the prior commits on this branch.

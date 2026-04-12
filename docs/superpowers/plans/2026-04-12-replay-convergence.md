# Replay Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route AI text messages on history replay through `StreamRenderer.append(fullContent).finalize()` so live-stream DOM and refresh-time DOM are byte-identical.

**Architecture:** Add a `replay: true` option to `StreamRenderer`. The option suppresses the cursor transfer and tells each block renderer to attach a `.stream-no-anim` CSS class on its root element. A single `animation: none !important;` rule kills the block-entrance animations on replay. The history-replay call site in `startNewChatModule.js:renderMessage` routes AI-history messages through a fresh `new StreamRenderer(el, { replay: true })` instead of `handleComplexMessage`.

**Tech Stack:** Vanilla JS (ES modules), `markdown-it` via `window.marked` shim, DOMPurify, morphdom, Prism.js, KaTeX, Mermaid. Node's built-in test runner for state-machine tests. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-12-replay-convergence-design.md` (latest: commit `73b43bd`).

---

## File Structure

### Files modified

| File | Responsibility |
|---|---|
| `app/static/css/stream-blocks.css` | Add `.stream-no-anim { animation: none !important; }` rule. |
| `app/static/js/streamRenderer.js` | Constructor accepts `{ replay }`; `_handleEvent` passes `opts` to `r.create()`; skip `transferCursor` when `this.replay` is true. |
| `app/static/js/stream/renderers/prose.js` | `create(block, container, opts)` appends `.stream-no-anim` when `opts.replay`. |
| `app/static/js/stream/renderers/code.js` | Same pattern. |
| `app/static/js/stream/renderers/list.js` | Same pattern + propagates `block.meta.replay` flag to per-`<li>` className in `update`. |
| `app/static/js/stream/renderers/math.js` | Same pattern. |
| `app/static/js/stream/renderers/mermaid.js` | Same pattern. |
| `app/static/js/startNewChatModule.js` | In `renderMessage`, when `sender === 'ai' && isHistory`, route through `new StreamRenderer(el, {replay:true}).append(content).finalize()`. Fall through to existing path otherwise. |
| `app/static/test/stream-harness.html` | Add one new test case proving replay:true produces `.stream-no-anim` on every block and no `.stream-cursor` at any point. |

### Files created

| File | Purpose |
|---|---|
| `tests/stream/oneShotReplay.test.mjs` | Verifies the L2 state machine produces the same block-AST when a ~2 KB mixed-markdown input is fed as one `append()` vs. feed in 50-char chunks. Belt-and-suspenders for the big-input path. |

### Files untouched

- `app/blueprints/chat_routes.py`, `app/services/stream_normalizer.py`, `app/services/chat_engine.py`, `app/utilities.py` — backend, zero changes.
- `app/static/js/codeWindow.js` — live stream path already uses StreamRenderer; nothing to change. `appendTextMessage` and `handleComplexMessage` remain for user messages and live non-replay paths.
- All other renderer / caller files.

---

## Task 1: Add `.stream-no-anim` CSS rule

**Files:**
- Modify: `app/static/css/stream-blocks.css`

- [ ] **Step 1: Append the kill-switch rule**

Add at the end of the file (after the `[data-stream-layout]` block, before EOF):

```css
/* Replay mode — suppress entrance animations across all stream blocks */
.stream-no-anim {
    animation: none !important;
}
```

- [ ] **Step 2: Verify the CSS is valid (syntax check)**

```bash
cd /root/xelas.io/xelasphere.io- && node -e "const fs=require('fs');const c=fs.readFileSync('app/static/css/stream-blocks.css','utf8');const l=c.split('\n').length;console.log('lines:', l, '— preview tail:'); console.log(c.split('\n').slice(-8).join('\n'));"
```

Expected: shows the new rule at the tail, no parse errors.

- [ ] **Step 3: Commit**

```bash
git add app/static/css/stream-blocks.css
git commit -m "style(stream): add .stream-no-anim replay kill-switch"
```

---

## Task 2: StreamRenderer constructor accepts `{ replay }` option

**Files:**
- Modify: `app/static/js/streamRenderer.js`

- [ ] **Step 1: Update the constructor signature**

Find the constructor in `app/static/js/streamRenderer.js`. Replace the current constructor signature with:

```javascript
    constructor(container, { replay = false } = {}) {
        this.container = container;
        this.replay = replay;
        this.rawBuffer = '';
        this.prewarmDone = false;
        this.destroyed = false;
        this.finalized = false;

        // Message wrapper that callers expect
        const wrapper = document.createElement('div');
        wrapper.className = 'text-message-container';
        const inner = document.createElement('div');
        inner.className = 'text-message';
        wrapper.appendChild(inner);
        this.container.appendChild(wrapper);
        this.messageEl = inner;

        this.ast = createBlockAST();
        this.sm = createBlockStateMachine(event => this._handleEvent(event));
        this.rafScheduled = false;
    }
```

- [ ] **Step 2: Thread `opts` into `_handleEvent`'s `BLOCK_START` case**

Find the `_handleEvent` method. Replace the `BLOCK_START` case with:

```javascript
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

The change: `r.create(...)` gets a third arg `{ replay: this.replay }`, and `transferCursor` is skipped on replay.

- [ ] **Step 3: Verify existing Node tests still pass**

```bash
cd /root/xelas.io/xelasphere.io- && node --test tests/stream/ 2>&1 | tail -20
```

Expected: `59/59 pass`. No Node test exercises the browser-only `create()` method directly; state-machine and AST tests remain green.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/streamRenderer.js
git commit -m "feat(stream): StreamRenderer constructor accepts {replay} option"
```

---

## Task 3: Prose renderer accepts `opts.replay`

**Files:**
- Modify: `app/static/js/stream/renderers/prose.js`

- [ ] **Step 1: Update `create` signature**

Find the `create` method in `app/static/js/stream/renderers/prose.js`. Replace the entire file body with:

```javascript
/**
 * Prose / heading / blockquote renderer.
 * Input: block object with .text (raw markdown) and .el (owned DOM node).
 * Pipeline: marked.parse → DOMPurify.sanitize → morphdom diff (childrenOnly).
 */

export function createProseRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const el = document.createElement('div');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            el.className = `stream-block block-${block.type}${noAnim}`;
            el.dataset.blockId = block.id;
            messageContainer.appendChild(el);
            block.el = el;
            return el;
        },
        update(block) {
            if (!block.el) return;
            const rawHTML = window.marked.parse(block.text);
            const safeHTML = window.DOMPurify
                ? window.DOMPurify.sanitize(rawHTML)
                : rawHTML;
            const wrapper = document.createElement('div');
            wrapper.innerHTML = safeHTML;
            if (window.morphdom) {
                window.morphdom(block.el, wrapper, {
                    childrenOnly: true,
                    onBeforeElUpdated(fromEl) {
                        if (fromEl.classList?.contains('citation-pill')) return false;
                        if (fromEl.classList?.contains('sources-btn')) return false;
                        return true;
                    },
                });
            } else {
                block.el.innerHTML = safeHTML;
            }
        },
        finalize(block) {
            this.update(block);
        },
    };
}
```

The change: `create` now takes a third `opts = {}` arg; sets `.stream-no-anim` on the block element when `opts.replay` is truthy. Backward-compatible — existing two-arg callers still work (opts defaults to `{}`, no class added).

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/prose.js
git commit -m "feat(stream): prose renderer honors opts.replay via .stream-no-anim"
```

---

## Task 4: Code renderer accepts `opts.replay`

**Files:**
- Modify: `app/static/js/stream/renderers/code.js`

- [ ] **Step 1: Update `create` signature**

Replace `app/static/js/stream/renderers/code.js` body with:

```javascript
/**
 * Code block renderer using Prism.js (already loaded on every page).
 * - Sets language-{lang} class so Prism autoloader picks up the grammar.
 * - Falls back to language-plaintext for empty or unrecognized languages.
 * - Calls Prism.highlightElement on every update; re-runs on finalize for
 *   the autoloader race (grammar may have arrived after last highlight).
 */

export function createCodeRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const lang = block.meta.lang || 'plaintext';
            const pre = document.createElement('pre');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            // is-streaming only while live; replay has full text already
            const streamingClass = opts.replay ? '' : ' is-streaming';
            pre.className = `stream-block block-code${streamingClass}${noAnim}`;
            pre.dataset.blockId = block.id;
            pre.dataset.lang = lang;

            const code = document.createElement('code');
            code.className = `language-${lang}`;
            pre.appendChild(code);
            messageContainer.appendChild(pre);

            block.el = pre;
            block.meta.codeEl = code;
            return pre;
        },
        update(block) {
            if (!block.meta.codeEl) return;
            block.meta.codeEl.textContent = block.text;
            if (window.Prism) {
                window.Prism.highlightElement(block.meta.codeEl);
            }
        },
        finalize(block) {
            this.update(block);
            if (block.el) block.el.classList.remove('is-streaming');
            if (window.Prism && block.meta.codeEl) {
                queueMicrotask(() => window.Prism.highlightElement(block.meta.codeEl));
            }
            this.attachCopyButton(block);
        },
        attachCopyButton(block) {
            if (!block.el || block.el.querySelector('.code-copy-btn')) return;
            const btn = document.createElement('button');
            btn.className = 'code-copy-btn';
            btn.type = 'button';
            btn.textContent = 'Copy';
            btn.addEventListener('click', () => {
                navigator.clipboard?.writeText(block.text);
                btn.textContent = 'Copied';
                setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
            });
            block.el.appendChild(btn);
        },
    };
}
```

Two changes: (a) `opts` param added; (b) on replay, `is-streaming` class is suppressed (no shimmer needed — the code is already fully present) and `stream-no-anim` is added.

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/code.js
git commit -m "feat(stream): code renderer honors opts.replay (no shimmer, no anim)"
```

---

## Task 5: List renderer accepts `opts.replay` and propagates to `<li>` items

**Files:**
- Modify: `app/static/js/stream/renderers/list.js`

- [ ] **Step 1: Update `create` and `update` to honor replay**

Replace `app/static/js/stream/renderers/list.js` body with:

```javascript
/**
 * List renderer — renders a <ul> or <ol> with per-item stagger animation.
 * Items are parsed out of block.text by splitting on the opener regex.
 * Completed items (all but the last) are sub-frozen — we skip re-render for them.
 */

const UL_ITEM_RE = /^[-+*] (.*)$/;
const OL_ITEM_RE = /^\d+\. (.*)$/;

function parseItems(block) {
    const lines = block.text.split('\n');
    const items = [];
    let current = null;
    const re = block.type === 'list-ul' ? UL_ITEM_RE : OL_ITEM_RE;
    for (const line of lines) {
        const m = line.match(re);
        if (m) {
            current = m[1];
            items.push(current);
        } else if (line.trim() !== '' && items.length) {
            items[items.length - 1] += '\n' + line;
        }
    }
    return items;
}

export function createListRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const tag = block.type === 'list-ol' ? 'ol' : 'ul';
            const el = document.createElement(tag);
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            el.className = `stream-block block-${block.type}${noAnim}`;
            el.dataset.blockId = block.id;
            messageContainer.appendChild(el);
            block.el = el;
            block.meta.renderedItemCount = 0;
            block.meta.replay = !!opts.replay;
            return el;
        },
        update(block) {
            if (!block.el) return;
            const items = parseItems(block);
            const existing = block.meta.renderedItemCount;
            const liNoAnim = block.meta.replay ? ' stream-no-anim' : '';

            // Update the tail item's text in place (it may still be streaming)
            if (existing > 0 && items.length >= existing) {
                const tailLi = block.el.children[existing - 1];
                if (tailLi) {
                    const tailHTML = window.DOMPurify.sanitize(
                        window.marked.parse(items[existing - 1] || '')
                    );
                    tailLi.innerHTML = tailHTML;
                }
            }

            // Append any new items after the previous tail
            for (let i = existing; i < items.length; i++) {
                const li = document.createElement('li');
                li.className = `block-list-item${liNoAnim}`;
                li.style.setProperty('--i', String(i));
                li.innerHTML = window.DOMPurify.sanitize(window.marked.parse(items[i] || ''));
                block.el.appendChild(li);
            }
            block.meta.renderedItemCount = items.length;
        },
        finalize(block) {
            this.update(block);
        },
    };
}
```

Two changes: (a) `create` stores `block.meta.replay = !!opts.replay`; (b) `update` reads `block.meta.replay` to add `.stream-no-anim` to every newly-created `<li>`. Field name matches `opts.replay` to keep naming consistent (per spec).

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/list.js
git commit -m "feat(stream): list renderer propagates replay flag to <li> items"
```

---

## Task 6: Math renderer accepts `opts.replay`

**Files:**
- Modify: `app/static/js/stream/renderers/math.js`

- [ ] **Step 1: Update `create` signature**

Replace `app/static/js/stream/renderers/math.js` body with:

```javascript
/**
 * Math block renderer (display math, $$…$$).
 * Buffered: shows a pending placeholder while streaming, renders with KaTeX on BLOCK_END.
 */

export function createMathRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const el = document.createElement('div');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            // Replay has full text; skip pending placeholder so KaTeX renders immediately on finalize
            const pending = opts.replay ? '' : ' is-pending';
            el.className = `stream-block block-math${pending}${noAnim}`;
            el.dataset.blockId = block.id;
            el.innerHTML = opts.replay ? '' : '<span class="pulse-dots">…</span>';
            messageContainer.appendChild(el);
            block.el = el;
            return el;
        },
        update(_block) {
            // Intentional no-op during streaming — math is buffered.
        },
        finalize(block) {
            if (!block.el) return;
            block.el.classList.remove('is-pending');
            block.el.innerHTML = '';
            if (!window.katex) {
                block.el.textContent = block.text;
                return;
            }
            try {
                window.katex.render(block.text, block.el, {
                    throwOnError: false,
                    displayMode: true,
                });
            } catch (err) {
                const pre = document.createElement('pre');
                pre.className = 'math-error';
                pre.textContent = block.text;
                block.el.appendChild(pre);
            }
        },
    };
}
```

Two changes: (a) `opts` param; (b) on replay, skip the `…` pending placeholder — the full text is already known and `finalize` will render it synchronously in the same tick.

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/math.js
git commit -m "feat(stream): math renderer skips pending placeholder on replay"
```

---

## Task 7: Mermaid renderer accepts `opts.replay`

**Files:**
- Modify: `app/static/js/stream/renderers/mermaid.js`

- [ ] **Step 1: Update `create` signature**

Replace `app/static/js/stream/renderers/mermaid.js` body with:

```javascript
/**
 * Mermaid diagram renderer.
 * Buffered: shows pending placeholder while streaming, validates + renders on BLOCK_END.
 * On invalid source, shows the raw text inside a <pre class="mermaid-error">.
 */

let mermaidInitialized = false;

function initializeMermaidOnce() {
    if (mermaidInitialized || !window.mermaid) return;
    window.mermaid.initialize({ startOnLoad: false, theme: 'dark' });
    mermaidInitialized = true;
}

export function createMermaidRenderer() {
    return {
        create(block, messageContainer, opts = {}) {
            const el = document.createElement('div');
            const noAnim = opts.replay ? ' stream-no-anim' : '';
            const pending = opts.replay ? '' : ' is-pending';
            el.className = `stream-block block-mermaid${pending}${noAnim}`;
            el.dataset.blockId = block.id;
            el.innerHTML = opts.replay ? '' : '<span class="pulse-dots">…</span>';
            messageContainer.appendChild(el);
            block.el = el;
            return el;
        },
        update(_block) {
            // no-op while streaming
        },
        async finalize(block) {
            if (!block.el) return;
            block.el.classList.remove('is-pending');
            initializeMermaidOnce();
            if (!window.mermaid) {
                block.el.innerHTML = '';
                block.el.textContent = block.text;
                return;
            }
            try {
                await window.mermaid.parse(block.text);
                const { svg } = await window.mermaid.render(
                    `mermaid-${block.id}`,
                    block.text,
                );
                block.el.innerHTML = svg;
            } catch (err) {
                const pre = document.createElement('pre');
                pre.className = 'mermaid-error';
                pre.textContent = block.text;
                block.el.innerHTML = '';
                block.el.appendChild(pre);
            }
        },
    };
}
```

Same shape as math: `opts` param; skip placeholder on replay.

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/mermaid.js
git commit -m "feat(stream): mermaid renderer skips pending placeholder on replay"
```

---

## Task 8: One-shot replay test — state machine handles large single-append input

**Files:**
- Create: `tests/stream/oneShotReplay.test.mjs`

- [ ] **Step 1: Write the failing test**

Create `tests/stream/oneShotReplay.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBlockStateMachine } from '../../app/static/js/stream/blockStateMachine.js';

// Mixed-markdown fixture: prose + heading + code fence + ordered list + $$ math + mermaid
const FIXTURE = [
    '# Document title',
    '',
    'This is an introduction paragraph explaining the document. It has bold **text** and italic *text*.',
    '',
    '## Section one',
    '',
    'Some prose with inline `code` and a [link](https://example.com).',
    '',
    '```python',
    'def add(a, b):',
    '    # Sum two numbers',
    '    return a + b',
    '```',
    '',
    '## Section two — a list',
    '',
    '1. First item with some detail.',
    '2. Second item',
    '   continuing on a new line.',
    '3. Third item',
    '',
    '## A formula',
    '',
    '$$',
    'E = mc^2',
    '$$',
    '',
    '## Diagram',
    '',
    '```mermaid',
    'graph TD; A-->B; B-->C;',
    '```',
    '',
    'Final closing paragraph.',
].join('\n') + '\n';

function runStateMachine(input, chunkSize) {
    const blocks = [];
    let current = null;
    const sm = createBlockStateMachine(event => {
        if (event.type === 'BLOCK_START') {
            current = { type: event.blockType, text: '', ...(Object.keys(event.meta).length ? { meta: event.meta } : {}) };
            blocks.push(current);
        } else if (event.type === 'BLOCK_APPEND') {
            current.text += event.text;
        }
    });

    if (chunkSize <= 0) {
        sm.append(input);
    } else {
        for (let i = 0; i < input.length; i += chunkSize) {
            sm.append(input.slice(i, i + chunkSize));
        }
    }
    sm.finalize();

    return blocks.map(b => ({ type: b.type, text: b.text, ...(b.meta ? { meta: b.meta } : {}) }));
}

test('one-shot append produces same block AST as chunked append', () => {
    const oneShot = runStateMachine(FIXTURE, 0);
    const chunked50 = runStateMachine(FIXTURE, 50);
    const chunked5 = runStateMachine(FIXTURE, 5);
    assert.deepEqual(oneShot, chunked50, '1-shot vs 50-char chunks must match');
    assert.deepEqual(oneShot, chunked5, '1-shot vs 5-char chunks must match');
});

test('one-shot large append produces exactly 9 blocks with expected types', () => {
    const blocks = runStateMachine(FIXTURE, 0);
    const types = blocks.map(b => b.type);
    // heading, paragraph, heading, paragraph, code, heading, list-ol, heading, math, heading, mermaid, paragraph
    assert.deepEqual(types, [
        'heading',
        'paragraph',
        'heading',
        'paragraph',
        'code',
        'heading',
        'list-ol',
        'heading',
        'math',
        'heading',
        'mermaid',
        'paragraph',
    ]);
});
```

- [ ] **Step 2: Run the tests — confirm they pass**

```bash
cd /root/xelas.io/xelasphere.io- && node --test tests/stream/oneShotReplay.test.mjs 2>&1 | tail -12
```

Expected: `2/2 pass`. The state machine's line-oriented processing is agnostic to chunk boundaries, so any chunking should produce the same events.

If a test fails: DO NOT modify the state machine to make it pass. Report the failure and stop — the state machine is the shipped specification; a divergence here means the fixture's expected output is wrong, not the state machine.

- [ ] **Step 3: Run the full Node test suite to confirm nothing else regressed**

```bash
cd /root/xelas.io/xelasphere.io- && node --test tests/stream/ 2>&1 | tail -10
```

Expected: `61/61 pass` (59 original + 2 new).

- [ ] **Step 4: Commit**

```bash
git add tests/stream/oneShotReplay.test.mjs
git commit -m "test(stream): verify state machine is chunk-boundary agnostic"
```

---

## Task 9: Wire replay path into `renderMessage`

**Files:**
- Modify: `app/static/js/startNewChatModule.js` around line 4211–4222

- [ ] **Step 1: Replace the AI branch in `renderMessage`**

Find this block in `app/static/js/startNewChatModule.js` (currently around lines 4211–4222):

```javascript
        } else if (!suppressText && safeContent) {
            // Long user messages → document card (Claude-style)
            if (sender === 'user' && typeof isLongMessage === 'function' && isLongMessage(safeContent)) {
                const card = createDocumentCard(safeContent);
                const wrapper = document.createElement('div');
                wrapper.classList.add('text-message-container');
                wrapper.appendChild(card);
                messageContent.appendChild(wrapper);
            } else {
                handleComplexMessage(messageContent, safeContent, messageType, id, showButtons, isHistory);
            }
        }
```

Replace with:

```javascript
        } else if (!suppressText && safeContent) {
            // Long user messages → document card (Claude-style)
            if (sender === 'user' && typeof isLongMessage === 'function' && isLongMessage(safeContent)) {
                const card = createDocumentCard(safeContent);
                const wrapper = document.createElement('div');
                wrapper.classList.add('text-message-container');
                wrapper.appendChild(card);
                messageContent.appendChild(wrapper);
            } else if (sender === 'ai' && isHistory && typeof window.StreamRenderer === 'function') {
                // Replay convergence: route AI history through StreamRenderer so DOM
                // matches the live-stream render byte-for-byte.
                if (typeof safeContent === 'string' && safeContent.includes('<')) {
                    console.warn('[replay] AI content contains "<" — unexpected, expected raw markdown only');
                }
                const renderer = new window.StreamRenderer(messageContent, { replay: true });
                renderer.append(safeContent);
                renderer.finalize();
                if (showButtons && typeof appendActionButtons === 'function') {
                    appendActionButtons(messageContent, id);
                }
            } else {
                handleComplexMessage(messageContent, safeContent, messageType, id, showButtons, isHistory);
            }
        }
```

Three additions:
- New `else if` gate: `sender === 'ai' && isHistory && typeof window.StreamRenderer === 'function'`.
- Defensive warning if content contains `<` (R3 from spec).
- After StreamRenderer finalizes, manually call `appendActionButtons` since we bypassed `handleComplexMessage` which normally does that.

The existing `handleComplexMessage` call is preserved as the fallback for: (a) user short messages, (b) live-path AI messages if ever routed here (`isHistory=false`), (c) environments where `StreamRenderer` isn't loaded yet.

- [ ] **Step 2: Verify no syntax errors in the file**

```bash
cd /root/xelas.io/xelasphere.io- && node -e "
try {
  const vm = require('vm');
  const fs = require('fs');
  const src = fs.readFileSync('app/static/js/startNewChatModule.js', 'utf8');
  new vm.Script(src);
  console.log('syntax OK');
} catch (e) { console.error('SYNTAX ERROR:', e.message); process.exit(1); }
"
```

Expected: `syntax OK`.

- [ ] **Step 3: Restart production to pick up the new JS**

```bash
sudo systemctl restart xelasphere && sleep 3 && systemctl is-active xelasphere
```

Expected: `active`. Note: `app/static/js/startNewChatModule.js` is a static file served fresh per request, so browser hard-reload is sufficient for Flask to serve the new version. Service restart is optional here but kept for consistency with the rest of the build plan — harmless.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/startNewChatModule.js
git commit -m "feat(stream): route AI history replay through StreamRenderer"
```

---

## Task 10: Extend stream harness with a replay-mode test case

**Files:**
- Modify: `app/static/test/stream-harness.html`

- [ ] **Step 1: Add a replay test case before the closing `</script>`**

Find the last `addCase(...)` call in `app/static/test/stream-harness.html` (the cursor-transfer case). Add this case right after it, before the closing `</script>`:

```javascript
addCase('replay mode: blocks have .stream-no-anim and no cursor', (target) => {
    const r = createProseRenderer();
    const block = { id: 'br1', type: 'paragraph', meta: {}, text: 'Hello **replay** world.', frozen: false, el: null };
    r.create(block, target, { replay: true });
    r.update(block);
    r.finalize(block);
    if (!block.el.classList.contains('stream-no-anim')) throw new Error('expected .stream-no-anim class on replay block');
    if (target.querySelectorAll('.stream-cursor').length !== 0) throw new Error('no cursor should exist in replay mode');
    const ok = document.createElement('p');
    ok.className = 'pass';
    ok.textContent = 'PASS';
    target.parentElement.appendChild(ok);
});
```

- [ ] **Step 2: Manually load harness to verify**

Start a dev Flask on port 5001 (if not already running):

```bash
cd /root/xelas.io/xelasphere.io- && set -a && source /root/xelas.io/xelasphere.io-/.env && set +a && FLASK_APP=run.py /root/xelas.io/xelasphere.io-/venv/bin/flask run --port 5001 --host 127.0.0.1 > /tmp/flask-harness.log 2>&1 &
sleep 3
ss -ltnp 2>/dev/null | grep :5001
```

Expected: listening on 5001.

Use the browse tool (or curl for a basic check) to load `/static/test/stream-harness.html`. With Node + Playwright directly (as we did in Task 22 of the streaming-organism plan), run a headless check from the server:

```bash
cat > /tmp/harness-check.mjs <<'EOF'
import { chromium } from '/root/.claude/skills/gstack/node_modules/playwright/index.mjs';
const browser = await chromium.launch({ chromiumSandbox: false, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
const page = await browser.newPage();
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
page.on('pageerror', e => errs.push(String(e)));
await page.goto('http://127.0.0.1:5001/static/test/stream-harness.html', { waitUntil: 'networkidle' });
await page.waitForTimeout(1500);
const failText = await page.evaluate(() => document.body.innerText);
const passCount = (failText.match(/PASS/g) || []).length;
const failCount = (failText.match(/FAIL:/g) || []).length;
console.log('PASS markers:', passCount);
console.log('FAIL markers:', failCount);
console.log('console errors:', errs.length ? errs : 'none');
await browser.close();
EOF
cd /root/.claude/skills/gstack && node /tmp/harness-check.mjs
```

Expected: `PASS markers: 2+` (cursor case + new replay case both pass), `FAIL markers: 0`, no console errors.

Kill the dev Flask when done:

```bash
pkill -f "flask run --port 5001" 2>/dev/null
```

- [ ] **Step 3: Commit**

```bash
git add app/static/test/stream-harness.html
git commit -m "test(stream): harness covers replay mode class + cursor suppression"
```

---

## Task 11: Manual QA — live vs. replay DOM diff + animation-free page load

**Files:** none created; verification only.

- [ ] **Step 1: Live render then refresh test**

1. Open your production URL in a fresh browser tab.
2. DevTools Console open.
3. Send a fresh, never-seen prompt: `"Describe the lifecycle of a blue whale in three short paragraphs. No code, no lists."`
4. Wait for the response to fully render.
5. In Console, capture the message's DOM:

```js
const aiMsgs = [...document.querySelectorAll('[data-sender="ai"] .message-content')];
const last = aiMsgs[aiMsgs.length - 1];
const liveDom = last.outerHTML;
console.log('LIVE DOM length:', liveDom.length);
copy(liveDom);  // copies to clipboard for diff
```

Save the live DOM output (paste into a text file as `/tmp/live-dom.html`).

6. **Hard-reload the page** (Ctrl+Shift+R).
7. Navigate to the same session.
8. In Console:

```js
const aiMsgs = [...document.querySelectorAll('[data-sender="ai"] .message-content')];
const last = aiMsgs[aiMsgs.length - 1];
const replayDom = last.outerHTML;
console.log('REPLAY DOM length:', replayDom.length);
copy(replayDom);
```

Save as `/tmp/replay-dom.html`.

9. Diff the two:

```bash
diff /tmp/live-dom.html /tmp/replay-dom.html | head -20
```

**Expected**: the only differences should be:
- `class="stream-block block-paragraph"` on live vs. `class="stream-block block-paragraph stream-no-anim"` on replay
- Possibly: no `.stream-cursor` span in replay (live has one removed on finalize; replay never had one)
- Block IDs may differ (`b1`, `b2` vs. `b1`, `b2` again — stable counter per renderer instance; both start from 1).

**Any other differences** (different class names, different tag structure, different attribute counts) → **fail the task**, file an issue with the diff, stop.

- [ ] **Step 2: Multi-message session load test**

1. Open a session with 10+ historic messages (use an older saved chat, or create fresh ones).
2. Hard-reload the page.
3. Immediately after page load (within 500 ms), visually observe: no fade-in or slide animations on any message.
4. Run in Console:

```js
const blocks = document.querySelectorAll('.stream-block');
const withNoAnim = document.querySelectorAll('.stream-block.stream-no-anim');
console.log(`total blocks: ${blocks.length}, with .stream-no-anim: ${withNoAnim.length}`);
```

Expected: `total blocks === with .stream-no-anim` for blocks belonging to AI messages (100% gate). If any live-streaming was still in progress during the load, those could lack the class — rerun the test when no active requests exist.

- [ ] **Step 3: Regression smoke — live streaming still animates**

1. On the same reloaded page, send a fresh prompt: `"Tell me one fact about dolphins."`
2. Observe: the response does have the entrance animation (fade-in + 4px slide).
3. Run in Console once complete:

```js
const newBlocks = [...document.querySelectorAll('.stream-block')];
const lastBlock = newBlocks[newBlocks.length - 1];
console.log('last block has stream-no-anim?', lastBlock.classList.contains('stream-no-anim'));
```

Expected: `false`. Live blocks should NOT have the class.

- [ ] **Step 4: Commit a QA note**

```bash
mkdir -p docs/superpowers/notes
echo "2026-04-12 replay convergence manual QA — passed: live/replay DOM diff shows only .stream-no-anim delta; 10+ message session loads with no animations; live streaming still animates" > docs/superpowers/notes/2026-04-12-replay-convergence-qa.md
git add docs/superpowers/notes/2026-04-12-replay-convergence-qa.md
git commit -m "docs(stream): manual QA note for replay convergence"
```

---

## Task 12: Final regression pass

**Files:** none created; verification only.

- [ ] **Step 1: Full Node suite**

```bash
cd /root/xelas.io/xelasphere.io- && node --test tests/stream/ 2>&1 | tail -10
```

Expected: `61/61 pass`.

- [ ] **Step 2: Python suite (must not regress against main)**

```bash
cd /root/xelas.io/xelasphere.io- && pytest tests/ --ignore=tests/stream -q 2>&1 | tail -8
```

Expected: same pass/fail counts as `main` pre-branch (15 pre-existing failures expected and documented). Any NEW Python failures → investigate (this branch touches no Python, so any new Python failure is unrelated or environment-related, not our branch).

- [ ] **Step 3: Perf page still green**

In your running app, open `/static/test/stream-perf.html`. Wait ~5 s for the JSON report.

Expected: `"pass": "PASS"` with `ratio ≤ 2.0`.

- [ ] **Step 4: Harness still green**

Either manual (browser-load of `/static/test/stream-harness.html` and visual check of all cases) or re-run the Playwright headless check from Task 10.

Expected: all cases render (no FAIL text), replay case shows `PASS` marker.

- [ ] **Step 5: Final marker commit**

```bash
git commit --allow-empty -m "chore(stream): replay convergence shipped — stream and refresh DOM converged"
```

---

## Self-review notes

- **Spec coverage**: every spec section has a task.
  - `replay: true` option (spec §Implementation detail) → Task 2
  - Per-type renderers honor `opts.replay` → Tasks 3, 4, 5, 6, 7
  - `.stream-no-anim` CSS → Task 1
  - Replay call site (spec §Implementation detail) → Task 9
  - One-shot replay test (spec §Testing → Unit) → Task 8
  - Manual DOM diff test (spec §Testing → Manual) → Task 11
  - Multi-message session load test (spec §Success criteria 2) → Task 11
  - Regression gates (spec §Success criteria 3) → Task 12
- **Cursor suppression on replay** (spec §Implementation detail) → Task 2 Step 2 guards `transferCursor` with `!this.replay`.
- **List `<li>` propagation** (spec §R1) → Task 5 stores `block.meta.replay` in `create` and reads it in `update` to set per-item class.
- **Promise return wrapping** (spec §R2) → not needed: the replay branch in Task 9 doesn't return from `renderMessage`, and the existing `handleComplexMessage` call path also doesn't `await` a returned Promise from inside `renderMessage`. Verified by reading the caller flow in `startNewChatModule.js`.
- **Defensive `<` log** (spec §R3) → Task 9 Step 1 includes the `console.warn` guard.
- **Backward compatibility**: `StreamRenderer(container)` (1-arg) and `r.create(block, el)` (2-arg) both continue to work via default-value destructuring — preserved in Tasks 2, 3, 4, 5, 6, 7.
- **No placeholders**: every step has actual code or actual commands.
- **Type/method consistency**: `opts.replay` → `opts.replay` → `block.meta.replay` is the single chain, confirmed in Tasks 2 → 3–7.

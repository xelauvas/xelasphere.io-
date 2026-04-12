# Streaming Rendering Organism — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve XelaSphere's `StreamRenderer` from a whole-buffer re-render into a block-oriented streaming pipeline: L2 block state machine, L3 block AST with frozen/live isolation, scoped L4 per-type renderers (code via Prism, lists with per-item stagger, buffered KaTeX math, buffered Mermaid), content-type pre-warming, and gentle CSS entrance animations.

**Architecture:** Message = ordered array of typed blocks. L2 (char-level state machine) emits `BLOCK_START` / `BLOCK_APPEND` / `BLOCK_END` events from the raw SSE buffer. L3 owns the block array and DOM; frozen blocks are never touched again, only the tail block re-renders per RAF frame. L4 renderers dispatch by block type.

**Tech Stack:** Vanilla JS (ESM `<script type="module">`), markdown-it (via `window.marked.parse` shim), DOMPurify, morphdom, Prism.js (already loaded + autoloader), KaTeX, Mermaid. Node's built-in test runner for unit tests. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-12-streaming-organism-design.md` (latest: commit `ddde356`).

---

## Testing Strategy

- **Pure-logic tests** (L2 state machine, L3 block AST, pre-warm inference): Node's built-in runner via `node --test`. Files under `tests/stream/*.test.mjs`. Zero dependencies.
- **Renderer / DOM / corpus tests**: a manual-load test harness at `app/static/test/stream-harness.html` that loads each renderer against fixtures and prints PASS/FAIL to the page.
- **30-item L2 edge-case corpus**: `tests/stream/corpus/*.md` with matching `*.expected.json` files, driven by the Node test runner. Must pass before L2 is wired into production.

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `app/static/js/stream/blockStateMachine.js` | L2 — char-level block detector, emits events |
| `app/static/js/stream/blockAST.js` | L3 — ordered block array, frozen/live invariants, DOM ownership |
| `app/static/js/stream/renderers/prose.js` | `marked.parse` + DOMPurify + morphdom, scoped to one block |
| `app/static/js/stream/renderers/code.js` | Prism.highlightElement + skeleton + copy button |
| `app/static/js/stream/renderers/list.js` | per-item stagger, sub-frozen items |
| `app/static/js/stream/renderers/math.js` | buffered KaTeX render |
| `app/static/js/stream/renderers/mermaid.js` | buffered mermaid parse + render |
| `app/static/js/stream/preWarm.js` | layout inference + container class mutation |
| `app/static/js/stream/cursor.js` | cursor element helpers — create, transfer, remove |
| `app/static/css/stream-blocks.css` | block classes, entrance animations, skeleton shimmer, layout variants |
| `app/static/test/stream-harness.html` | manual renderer/DOM test page |
| `tests/stream/blockStateMachine.test.mjs` | L2 unit tests |
| `tests/stream/blockStateMachine.corpus.test.mjs` | L2 corpus runner |
| `tests/stream/blockAST.test.mjs` | L3 unit tests |
| `tests/stream/preWarm.test.mjs` | pre-warm inference tests |
| `tests/stream/corpus/*.md` | 30 LLM-output fixtures |
| `tests/stream/corpus/*.expected.json` | expected block AST per fixture |

### Modified files

| File | Change |
|---|---|
| `app/static/js/streamRenderer.js` | Internals rewritten to delegate to the stream modules. Outer class API (`append`/`finalize`/`destroy`) unchanged. Becomes an ES module; sets `window.StreamRenderer` at the end for back-compat with existing call sites. |
| `app/templates/index.html` | Add `<script type="module">` tag for streamRenderer. Add stylesheet link for stream-blocks.css. |

### Untouched

- `app/static/js/codeWindow.js`, `startNewChatModule.js`, `voiceModeModule.js` — callers, contract preserved.
- `app/static/js/progressiveTextDisplay.js` — history replay path is orthogonal.
- Backend (`app/services/stream_normalizer.py`, `app/services/chat_engine.py`) — no changes.

---

## Task 1: Scaffold the test runner and directory structure

**Files:**
- Create: `tests/stream/` directory
- Create: `tests/stream/README.md`
- Create: `app/static/js/stream/` directory
- Create: `app/static/js/stream/renderers/` directory
- Create: `app/static/test/` directory

- [ ] **Step 1: Create directories and placeholder files**

```bash
mkdir -p tests/stream/corpus
mkdir -p app/static/js/stream/renderers
mkdir -p app/static/test
touch tests/stream/README.md
```

- [ ] **Step 2: Populate `tests/stream/README.md`**

```markdown
# Stream module tests

Pure-logic tests (state machine, block AST, pre-warm) run under Node's built-in test runner:

    node --test tests/stream/

DOM / renderer tests live in `app/static/test/stream-harness.html` — open in a browser.

The 30-item corpus lives in `tests/stream/corpus/`. Each fixture is a pair:
- `NNN-short-name.md` — raw LLM output
- `NNN-short-name.expected.json` — the block AST the state machine must produce
```

- [ ] **Step 3: Verify Node version supports `node --test`**

```bash
node --version
# Expected: v18 or later
node --test --help | head -5
# Expected: shows --test flag help
```

- [ ] **Step 4: Commit**

```bash
git add tests/stream/README.md
git commit -m "test: scaffold stream test tree"
```

---

## Task 2: Build the 30-item L2 corpus (fixtures only — no runner yet)

**Files:**
- Create: `tests/stream/corpus/001-simple-prose.md` through `030-*.md`
- Create: matching `*.expected.json` files

The corpus **must** cover these failure modes (required by the build plan carry-forward):
1. Nested list-inside-code-block
2. Math-inside-blockquote
3. List continuation lines (multi-sentence items)
4. Unclosed fence at stream end
5. `$$` appearing inline mid-sentence (`costs $$50`)
6. Mermaid inside a code fence
7. Heading immediately after code fence close
8. Ordered list with gaps (`1.` ... `3.`)
9. Empty code block (open fence, immediate close)
10. Code block with language tag containing dashes (`objective-c`)

Remaining fixtures fill normal-path coverage (pure prose, lone heading, short list, paragraph with inline math, paragraph with inline code, blockquote block, paragraph with link, etc.).

- [ ] **Step 1: Create fixtures 001–010 (normal-path)**

Example for `001-simple-prose.md`:
```markdown
Hello world. This is a single paragraph.
```

Example for `001-simple-prose.expected.json`:
```json
[
  { "type": "paragraph", "text": "Hello world. This is a single paragraph.\n" }
]
```

Create 001 through 010 covering: simple prose (001), two paragraphs (002), `#` heading (003), `##` heading + prose (004), unordered list (005), ordered list (006), blockquote (007), simple code fence (008), inline `$math$` in prose (009), inline `` `code` `` in prose (010).

- [ ] **Step 2: Create fixtures 011–020 (nested + closing rules)**

011 nested list-in-code-block, 012 math-inside-blockquote, 013 list continuation lines, 014 unclosed fence at stream end, 015 `costs $$50` inline false fire, 016 mermaid block, 017 heading immediately after code fence, 018 ordered list with gaps, 019 empty code block, 020 code block with `objective-c` lang tag.

For each, write the markdown file and the expected JSON. Expected output for fixture 015 (`costs $$50`) must show a single paragraph block — NOT a math block.

Example for `015-dollar-signs-inline.md`:
```markdown
The seat costs $$50 per month.
```

Example for `015-dollar-signs-inline.expected.json`:
```json
[
  { "type": "paragraph", "text": "The seat costs $$50 per month.\n" }
]
```

- [ ] **Step 3: Create fixtures 021–030 (tricky prose)**

021 bold mid-buffer (unclosed `**` at split boundary), 022 link mid-buffer (unclosed `[`), 023 `$$ ... $$` math block, 024 mermaid + prose follow-up, 025 nested `> > ` blockquote, 026 list with inline code items, 027 heading 1-6 levels, 028 ordered list with multi-line items, 029 code block ending exactly at buffer end, 030 long prose >5KB with bold/italic mid-stream.

- [ ] **Step 4: Commit the corpus**

```bash
git add tests/stream/corpus/
git commit -m "test: add 30-item L2 state machine corpus"
```

---

## Task 3: L2 state machine — scaffold and failing first test

**Files:**
- Create: `app/static/js/stream/blockStateMachine.js`
- Create: `tests/stream/blockStateMachine.test.mjs`

- [ ] **Step 1: Write the first failing test**

`tests/stream/blockStateMachine.test.mjs`:
```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBlockStateMachine } from '../../app/static/js/stream/blockStateMachine.js';

test('empty input emits no events', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('');
    sm.finalize();
    assert.deepEqual(events, []);
});

test('simple prose emits one paragraph block', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('Hello world.');
    sm.finalize();
    const types = events.map(e => e.type);
    assert.deepEqual(types, ['BLOCK_START', 'BLOCK_APPEND', 'BLOCK_END']);
    assert.equal(events[0].blockType, 'paragraph');
});
```

- [ ] **Step 2: Verify the test fails with import error**

```bash
node --test tests/stream/blockStateMachine.test.mjs
```
Expected: FAIL — cannot find module `blockStateMachine.js`.

- [ ] **Step 3: Write minimal scaffold**

`app/static/js/stream/blockStateMachine.js`:
```javascript
export function createBlockStateMachine(emit) {
    let buffer = '';
    let inBlock = false;
    let blockId = 0;

    function startBlockIfNeeded(type) {
        if (inBlock) return;
        inBlock = true;
        blockId++;
        emit({ type: 'BLOCK_START', id: `b${blockId}`, blockType: type, meta: {} });
    }

    function endBlockIfNeeded(reason) {
        if (!inBlock) return;
        emit({ type: 'BLOCK_END', id: `b${blockId}`, reason });
        inBlock = false;
    }

    return {
        append(text) {
            if (!text) return;
            buffer += text;
            startBlockIfNeeded('paragraph');
            emit({ type: 'BLOCK_APPEND', id: `b${blockId}`, text });
        },
        finalize() {
            endBlockIfNeeded('finalize');
        },
    };
}
```

- [ ] **Step 4: Verify tests pass**

```bash
node --test tests/stream/blockStateMachine.test.mjs
```
Expected: PASS — 2/2.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/blockStateMachine.js tests/stream/blockStateMachine.test.mjs
git commit -m "feat(stream): scaffold L2 block state machine with paragraph path"
```

---

## Task 4: L2 — headings, blockquotes, blank-line paragraph termination

**Files:**
- Modify: `app/static/js/stream/blockStateMachine.js`
- Modify: `tests/stream/blockStateMachine.test.mjs`

- [ ] **Step 1: Add failing tests for headings and blockquotes**

Append to `tests/stream/blockStateMachine.test.mjs`:
```javascript
test('heading detected at line-start', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('## Title\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'heading');
    assert.equal(starts[0].meta.level, 2);
});

test('blockquote detected at line-start', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('> quoted line\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'blockquote');
});

test('blank line terminates paragraph', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('First para.\n\nSecond para.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 2);
});

test('# symbol not at line-start stays in paragraph', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('See issue #42 please.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'paragraph');
});
```

- [ ] **Step 2: Verify new tests fail**

```bash
node --test tests/stream/blockStateMachine.test.mjs
```
Expected: FAIL on the 4 new tests (only paragraph is implemented).

- [ ] **Step 3: Rewrite state machine to be line-oriented**

Replace `app/static/js/stream/blockStateMachine.js` body:
```javascript
export function createBlockStateMachine(emit) {
    let rawBuffer = '';
    let cursor = 0;
    let current = null;
    let blockCounter = 0;

    function openBlock(type, meta = {}) {
        closeBlock('natural');
        blockCounter++;
        current = { id: `b${blockCounter}`, type, meta, text: '' };
        emit({ type: 'BLOCK_START', id: current.id, blockType: type, meta });
    }

    function closeBlock(reason) {
        if (!current) return;
        emit({ type: 'BLOCK_END', id: current.id, reason });
        current = null;
    }

    function appendToBlock(text) {
        if (!current) openBlock('paragraph');
        current.text += text;
        emit({ type: 'BLOCK_APPEND', id: current.id, text });
    }

    function processLine(line, hasTrailingNewline) {
        const full = line + (hasTrailingNewline ? '\n' : '');

        if (line === '' && current) {
            closeBlock('natural');
            return;
        }

        const headingMatch = line.match(/^(#{1,6}) (.*)$/);
        if (headingMatch) {
            openBlock('heading', { level: headingMatch[1].length });
            appendToBlock(full);
            closeBlock('natural');
            return;
        }

        if (line.startsWith('> ')) {
            if (!current || current.type !== 'blockquote') openBlock('blockquote');
            appendToBlock(full);
            return;
        }

        if (!current || current.type !== 'paragraph') openBlock('paragraph');
        appendToBlock(full);
    }

    function drain(final) {
        while (cursor < rawBuffer.length) {
            const nl = rawBuffer.indexOf('\n', cursor);
            if (nl === -1) {
                if (!final) break;
                processLine(rawBuffer.slice(cursor), false);
                cursor = rawBuffer.length;
                break;
            }
            processLine(rawBuffer.slice(cursor, nl), true);
            cursor = nl + 1;
        }
    }

    return {
        append(text) {
            if (!text) return;
            rawBuffer += text;
            drain(false);
        },
        finalize() {
            drain(true);
            closeBlock('finalize');
        },
    };
}
```

- [ ] **Step 4: Verify all tests pass**

```bash
node --test tests/stream/blockStateMachine.test.mjs
```
Expected: PASS — all tests from Tasks 3 and 4.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/blockStateMachine.js tests/stream/blockStateMachine.test.mjs
git commit -m "feat(stream): L2 handles headings, blockquotes, blank-line termination"
```

---

## Task 5: L2 — code fences (open, close, language tag, Mermaid special-case)

**Files:**
- Modify: `app/static/js/stream/blockStateMachine.js`
- Modify: `tests/stream/blockStateMachine.test.mjs`

- [ ] **Step 1: Add failing tests**

```javascript
test('code fence opens with language tag', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```python\nprint("hi")\n```\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'code');
    assert.equal(starts[0].meta.lang, 'python');
});

test('code fence content is emitted as append', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```\nline1\nline2\n```\n');
    sm.finalize();
    const appends = events.filter(e => e.type === 'BLOCK_APPEND').map(e => e.text);
    assert.equal(appends.join(''), 'line1\nline2\n');
});

test('closing fence emits BLOCK_END natural', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```\nbody\n```\nafter\n');
    sm.finalize();
    const ends = events.filter(e => e.type === 'BLOCK_END');
    assert.equal(ends[0].reason, 'natural');
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 2);
    assert.equal(starts[1].blockType, 'paragraph');
});

test('mermaid language routes to mermaid block type', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```mermaid\ngraph TD; A-->B;\n```\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'mermaid');
});

test('unclosed code fence at finalize emits BLOCK_END finalize', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```python\ndef foo():\n    pass\n');
    sm.finalize();
    const ends = events.filter(e => e.type === 'BLOCK_END');
    assert.equal(ends.at(-1).reason, 'finalize');
});

test('objective-c language tag with dash is preserved', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('```objective-c\nNSLog(@"hi");\n```\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].meta.lang, 'objective-c');
});
```

- [ ] **Step 2: Verify tests fail**

Expected: all six code-fence tests FAIL.

- [ ] **Step 3: Add fence logic to `processLine`**

In `app/static/js/stream/blockStateMachine.js`, modify `processLine` to check for code/mermaid state FIRST and handle fence transitions. Replace `processLine` with:
```javascript
function processLine(line, hasTrailingNewline) {
    const full = line + (hasTrailingNewline ? '\n' : '');

    // Inside a code or mermaid block: only a closing fence can exit
    if (current && (current.type === 'code' || current.type === 'mermaid')) {
        if (line === '```') {
            closeBlock('natural');
            return;
        }
        appendToBlock(full);
        return;
    }

    // Opening fence
    const fenceMatch = line.match(/^```([\w.+-]*)$/);
    if (fenceMatch) {
        const lang = fenceMatch[1] || '';
        if (lang === 'mermaid') {
            openBlock('mermaid');
        } else {
            openBlock('code', { lang });
        }
        return;
    }

    if (line === '' && current) {
        closeBlock('natural');
        return;
    }

    const headingMatch = line.match(/^(#{1,6}) (.*)$/);
    if (headingMatch) {
        openBlock('heading', { level: headingMatch[1].length });
        appendToBlock(full);
        closeBlock('natural');
        return;
    }

    if (line.startsWith('> ')) {
        if (!current || current.type !== 'blockquote') openBlock('blockquote');
        appendToBlock(full);
        return;
    }

    if (!current || current.type !== 'paragraph') openBlock('paragraph');
    appendToBlock(full);
}
```

- [ ] **Step 4: Verify all tests pass**

```bash
node --test tests/stream/blockStateMachine.test.mjs
```
Expected: PASS — all tests from Tasks 3, 4, 5.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/blockStateMachine.js tests/stream/blockStateMachine.test.mjs
git commit -m "feat(stream): L2 handles code fences and mermaid special-case"
```

---

## Task 6: L2 — lists (UL/OL, item boundaries, continuation lines)

**Files:**
- Modify: `app/static/js/stream/blockStateMachine.js`
- Modify: `tests/stream/blockStateMachine.test.mjs`

- [ ] **Step 1: Add failing tests**

```javascript
test('unordered list emits a single list-ul block with item boundaries', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('- first\n- second\n- third\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'list-ul');
    const itemStarts = events.filter(e => e.type === 'LIST_ITEM_START');
    assert.equal(itemStarts.length, 3);
});

test('ordered list detects numeric prefix', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('1. first\n2. second\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'list-ol');
});

test('list item continuation line appends to current item', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('- first item\n  continuation line\n- second\n');
    sm.finalize();
    const itemStarts = events.filter(e => e.type === 'LIST_ITEM_START');
    assert.equal(itemStarts.length, 2);
});

test('blank line terminates list', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('- a\n- b\n\nAfter.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 2);
    assert.equal(starts[0].blockType, 'list-ul');
    assert.equal(starts[1].blockType, 'paragraph');
});
```

- [ ] **Step 2: Verify tests fail**

- [ ] **Step 3: Extend `processLine` with list rules**

Add list detection + `LIST_ITEM_START` / `LIST_ITEM_END` emission. In `blockStateMachine.js`, extend `processLine` with two new branches before the blockquote check:
```javascript
// Inside a list: handle item continuation
if (current && (current.type === 'list-ul' || current.type === 'list-ol')) {
    const ulItem = line.match(/^[-+*] (.*)$/);
    const olItem = line.match(/^\d+\. (.*)$/);
    if ((current.type === 'list-ul' && ulItem) || (current.type === 'list-ol' && olItem)) {
        emit({ type: 'LIST_ITEM_END', blockId: current.id, itemIndex: current.meta.itemCount - 1 });
        current.meta.itemCount++;
        emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: current.meta.itemCount - 1 });
        appendToBlock(full);
        return;
    }
    // Continuation line — append to current item
    appendToBlock(full);
    return;
}

// Starting a new list
const ulStart = line.match(/^[-+*] (.*)$/);
const olStart = line.match(/^\d+\. (.*)$/);
if (ulStart) {
    openBlock('list-ul', { itemCount: 1 });
    emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: 0 });
    appendToBlock(full);
    return;
}
if (olStart) {
    openBlock('list-ol', { itemCount: 1 });
    emit({ type: 'LIST_ITEM_START', blockId: current.id, itemIndex: 0 });
    appendToBlock(full);
    return;
}
```

Also: when `closeBlock` fires on a list, emit a final `LIST_ITEM_END`. Modify `closeBlock`:
```javascript
function closeBlock(reason) {
    if (!current) return;
    if (current.type === 'list-ul' || current.type === 'list-ol') {
        emit({ type: 'LIST_ITEM_END', blockId: current.id, itemIndex: current.meta.itemCount - 1 });
    }
    emit({ type: 'BLOCK_END', id: current.id, reason });
    current = null;
}
```

- [ ] **Step 4: Verify tests pass**

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/blockStateMachine.js tests/stream/blockStateMachine.test.mjs
git commit -m "feat(stream): L2 handles lists with item boundaries and continuations"
```

---

## Task 7: L2 — math blocks (`$$` with line-start guard)

**Files:**
- Modify: `app/static/js/stream/blockStateMachine.js`
- Modify: `tests/stream/blockStateMachine.test.mjs`

- [ ] **Step 1: Add failing tests**

```javascript
test('$$ at line-start opens math block', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('$$\nE = mc^2\n$$\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts[0].blockType, 'math');
});

test('inline $$50 does NOT open math block', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('The seat costs $$50 per month.\n');
    sm.finalize();
    const starts = events.filter(e => e.type === 'BLOCK_START');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].blockType, 'paragraph');
});

test('unclosed $$ at finalize emits BLOCK_END finalize', () => {
    const events = [];
    const sm = createBlockStateMachine(e => events.push(e));
    sm.append('$$\nE = mc');
    sm.finalize();
    const ends = events.filter(e => e.type === 'BLOCK_END');
    assert.equal(ends.at(-1).reason, 'finalize');
});
```

- [ ] **Step 2: Verify tests fail**

- [ ] **Step 3: Add math block logic**

Before the code-fence branch in `processLine`, add:
```javascript
// Inside a math block: only $$ at line-start closes
if (current && current.type === 'math') {
    if (line === '$$') {
        closeBlock('natural');
        return;
    }
    appendToBlock(full);
    return;
}

// Opening math block — must be the entire line
if (line === '$$') {
    openBlock('math');
    return;
}
```

- [ ] **Step 4: Verify tests pass**

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/blockStateMachine.js tests/stream/blockStateMachine.test.mjs
git commit -m "feat(stream): L2 handles \$\$ math blocks with line-start guard"
```

---

## Task 8: L2 corpus runner — wire the 30 fixtures

**Files:**
- Create: `tests/stream/blockStateMachine.corpus.test.mjs`

- [ ] **Step 1: Write the corpus runner**

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBlockStateMachine } from '../../app/static/js/stream/blockStateMachine.js';

const corpusDir = fileURLToPath(new URL('./corpus/', import.meta.url));

function runFixture(md) {
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
    sm.append(md);
    sm.finalize();
    return blocks;
}

const fixtures = readdirSync(corpusDir)
    .filter(f => f.endsWith('.md'))
    .sort();

for (const fixture of fixtures) {
    test(`corpus: ${fixture}`, () => {
        const md = readFileSync(join(corpusDir, fixture), 'utf8');
        const expectedPath = join(corpusDir, fixture.replace(/\.md$/, '.expected.json'));
        const expected = JSON.parse(readFileSync(expectedPath, 'utf8'));
        const actual = runFixture(md);
        assert.deepEqual(
            actual.map(b => ({ type: b.type, text: b.text, ...(b.meta ? { meta: b.meta } : {}) })),
            expected,
        );
    });
}
```

- [ ] **Step 2: Run the corpus and fix fixtures or state machine until all 30 pass**

```bash
node --test tests/stream/blockStateMachine.corpus.test.mjs
```

Expected after iteration: PASS 30/30.

When a fixture fails, decide whether the fixture's `expected.json` is wrong or the state machine is wrong. The spec is the tiebreaker. If you find a genuine gap in the state machine not covered by the spec, stop and flag — do not silently extend.

- [ ] **Step 3: Commit**

```bash
git add tests/stream/blockStateMachine.corpus.test.mjs
git commit -m "test: L2 corpus runner — all 30 fixtures pass"
```

---

## Task 9: L3 block AST — schema, frozen/live invariants

**Files:**
- Create: `app/static/js/stream/blockAST.js`
- Create: `tests/stream/blockAST.test.mjs`

- [ ] **Step 1: Write failing tests**

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBlockAST } from '../../app/static/js/stream/blockAST.js';

test('new AST is empty', () => {
    const ast = createBlockAST();
    assert.deepEqual(ast.blocks(), []);
});

test('adding a block appends to the array and marks it live', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    const blocks = ast.blocks();
    assert.equal(blocks.length, 1);
    assert.equal(blocks[0].frozen, false);
});

test('appendToLive mutates only the live block text', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    ast.appendToLive('Hello ');
    ast.appendToLive('world.');
    assert.equal(ast.blocks()[0].text, 'Hello world.');
});

test('endBlock freezes the live block; subsequent appendToLive throws', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    ast.appendToLive('Done.');
    ast.endBlock();
    assert.equal(ast.blocks()[0].frozen, true);
    assert.throws(() => ast.appendToLive('more'), /no live block/i);
});

test('starting a new block after end leaves prior frozen and creates new live', () => {
    const ast = createBlockAST();
    ast.startBlock({ id: 'b1', type: 'paragraph', meta: {} });
    ast.appendToLive('a');
    ast.endBlock();
    ast.startBlock({ id: 'b2', type: 'heading', meta: { level: 1 } });
    ast.appendToLive('# Title');
    const blocks = ast.blocks();
    assert.equal(blocks.length, 2);
    assert.equal(blocks[0].frozen, true);
    assert.equal(blocks[1].frozen, false);
});
```

- [ ] **Step 2: Verify tests fail**

- [ ] **Step 3: Implement `blockAST.js`**

```javascript
export function createBlockAST() {
    const blocks = [];
    let live = null;

    return {
        blocks() {
            return blocks;
        },
        live() {
            return live;
        },
        startBlock({ id, type, meta }) {
            if (live) {
                live.frozen = true;
                live = null;
            }
            live = { id, type, meta: { ...meta }, text: '', frozen: false, el: null };
            blocks.push(live);
            return live;
        },
        appendToLive(text) {
            if (!live) throw new Error('no live block');
            live.text += text;
        },
        endBlock() {
            if (!live) return;
            live.frozen = true;
            live = null;
        },
    };
}
```

- [ ] **Step 4: Verify tests pass**

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/blockAST.js tests/stream/blockAST.test.mjs
git commit -m "feat(stream): L3 block AST with frozen/live invariants"
```

---

## Task 10: Cursor module (create / transfer / remove) — explicit step

**Files:**
- Create: `app/static/js/stream/cursor.js`

This is the explicit cursor-transfer step called out in the build plan handoff: when a new block starts, remove `.stream-cursor` from the previous live block's tail and insert it into the new block's tail. One module, three exports.

- [ ] **Step 1: Write `cursor.js`**

```javascript
const CURSOR_CLASS = 'stream-cursor';

export function createCursorElement() {
    const el = document.createElement('span');
    el.className = CURSOR_CLASS;
    return el;
}

/**
 * Transfer the cursor to `newTailEl`. Removes any existing `.stream-cursor`
 * elements from anywhere in the message container first (defensive — there
 * should only ever be one) and appends a fresh one to newTailEl.
 *
 * Called on every BLOCK_START. Safe to call if no previous cursor exists.
 */
export function transferCursor(messageContainerEl, newTailEl) {
    messageContainerEl.querySelectorAll(`.${CURSOR_CLASS}`).forEach(el => el.remove());
    newTailEl.appendChild(createCursorElement());
}

export function removeCursor(messageContainerEl) {
    messageContainerEl.querySelectorAll(`.${CURSOR_CLASS}`).forEach(el => el.remove());
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/cursor.js
git commit -m "feat(stream): cursor module — create, transfer on BLOCK_START, remove"
```

Note: cursor tests live in the DOM harness (Task 18), not in Node — `document` doesn't exist in Node without jsdom.

---

## Task 11: CSS — block classes, entrance animations, skeleton shimmer, layout variants

**Files:**
- Create: `app/static/css/stream-blocks.css`

- [ ] **Step 1: Write the stylesheet**

```css
/* Block containers — layout + entrance animations */

.stream-block {
    animation: blockIn 120ms ease-out;
}

.block-code        { animation: blockIn  80ms ease-out; }
.block-math        { animation: blockIn 100ms ease-out; }
.block-mermaid     { animation: blockIn 100ms ease-out; }
.block-list-item   {
    animation: blockIn 60ms ease-out;
    animation-delay: calc(var(--i, 0) * 30ms);
    animation-fill-mode: both;
}

@keyframes blockIn {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
    .stream-block,
    .block-code,
    .block-math,
    .block-mermaid,
    .block-list-item {
        animation: none;
    }
}

/* Skeleton shimmer while a code block is streaming */

.block-code.is-streaming {
    position: relative;
}

.block-code.is-streaming::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(
        90deg,
        transparent 0%,
        rgba(255, 255, 255, 0.04) 50%,
        transparent 100%
    );
    background-size: 200% 100%;
    animation: shimmer 1.4s linear infinite;
    pointer-events: none;
}

@keyframes shimmer {
    from { background-position: 200% 0; }
    to   { background-position: -200% 0; }
}

/* Math / mermaid pending placeholder */

.block-math.is-pending,
.block-mermaid.is-pending {
    opacity: 0.6;
    min-height: 1.4em;
}

.pulse-dots {
    display: inline-block;
    animation: pulse-dots 1.2s ease-in-out infinite;
}

@keyframes pulse-dots {
    0%, 100% { opacity: 0.3; }
    50%      { opacity: 1; }
}

/* Cursor — CSS-only blinking line (unchanged from existing spec) */

.stream-cursor {
    display: inline-block;
    width: 2px;
    height: 1.1em;
    background: var(--text-primary, #e2e8f0);
    margin-left: 1px;
    vertical-align: text-bottom;
    animation: cursor-blink 0.8s steps(2) infinite;
}

@keyframes cursor-blink {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0; }
}

/* Pre-warm layout variants */

[data-stream-layout="prose"]      { max-width: 65ch; }
[data-stream-layout="structured"] { max-width: 75ch; }
[data-stream-layout="code-heavy"] { max-width: 85ch; }
[data-stream-layout="enumerated"] { max-width: 70ch; }
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/stream-blocks.css
git commit -m "style(stream): block CSS — entrance animations, shimmer, layout variants"
```

---

## Task 12: Prose renderer

**Files:**
- Create: `app/static/js/stream/renderers/prose.js`

- [ ] **Step 1: Write the prose renderer**

```javascript
/**
 * Prose / heading / blockquote renderer.
 * Input: block object with .text (raw markdown) and .el (owned DOM node).
 * Pipeline: marked.parse → DOMPurify.sanitize → morphdom diff (childrenOnly).
 */

export function createProseRenderer() {
    return {
        create(block, messageContainer) {
            const el = document.createElement('div');
            el.className = `stream-block block-${block.type}`;
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

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/prose.js
git commit -m "feat(stream): prose renderer (marked + DOMPurify + morphdom)"
```

---

## Task 13: Code renderer (Prism)

**Files:**
- Create: `app/static/js/stream/renderers/code.js`

- [ ] **Step 1: Write the code renderer**

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
        create(block, messageContainer) {
            const lang = block.meta.lang || 'plaintext';
            const pre = document.createElement('pre');
            pre.className = 'stream-block block-code is-streaming';
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
                // Re-highlight after a microtask so autoloader has a chance to resolve
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

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/code.js
git commit -m "feat(stream): code renderer (Prism with plaintext fallback)"
```

---

## Task 14: List renderer (per-item stagger, sub-frozen items)

**Files:**
- Create: `app/static/js/stream/renderers/list.js`

- [ ] **Step 1: Write the list renderer**

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
        create(block, messageContainer) {
            const tag = block.type === 'list-ol' ? 'ol' : 'ul';
            const el = document.createElement(tag);
            el.className = `stream-block block-${block.type}`;
            el.dataset.blockId = block.id;
            messageContainer.appendChild(el);
            block.el = el;
            block.meta.renderedItemCount = 0;
            return el;
        },
        update(block) {
            if (!block.el) return;
            const items = parseItems(block);
            const existing = block.meta.renderedItemCount;

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
                li.className = 'block-list-item';
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

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/list.js
git commit -m "feat(stream): list renderer with per-item stagger"
```

---

## Task 15: Math renderer (buffered KaTeX)

**Files:**
- Create: `app/static/js/stream/renderers/math.js`

- [ ] **Step 1: Write the math renderer**

```javascript
/**
 * Math block renderer (display math, $$…$$).
 * Buffered: shows a pending placeholder while streaming, renders with KaTeX on BLOCK_END.
 */

export function createMathRenderer() {
    return {
        create(block, messageContainer) {
            const el = document.createElement('div');
            el.className = 'stream-block block-math is-pending';
            el.dataset.blockId = block.id;
            el.innerHTML = '<span class="pulse-dots">…</span>';
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

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/math.js
git commit -m "feat(stream): math renderer (buffered KaTeX with error fallback)"
```

---

## Task 16: Mermaid renderer (buffered, validate before render)

**Files:**
- Create: `app/static/js/stream/renderers/mermaid.js`

- [ ] **Step 1: Write the mermaid renderer**

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
        create(block, messageContainer) {
            const el = document.createElement('div');
            el.className = 'stream-block block-mermaid is-pending';
            el.dataset.blockId = block.id;
            el.innerHTML = '<span class="pulse-dots">…</span>';
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

- [ ] **Step 2: Commit**

```bash
git add app/static/js/stream/renderers/mermaid.js
git commit -m "feat(stream): mermaid renderer (buffered, validate before render)"
```

---

## Task 17: Pre-warm — layout inference

**Files:**
- Create: `app/static/js/stream/preWarm.js`
- Create: `tests/stream/preWarm.test.mjs`

- [ ] **Step 1: Write failing tests**

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inferLayout } from '../../app/static/js/stream/preWarm.js';

test('code fence in first chars → code-heavy', () => {
    assert.equal(inferLayout('Here is some code:\n```python\n'), 'code-heavy');
});

test('heading at line-start → structured', () => {
    assert.equal(inferLayout('## Overview\n\nHello'), 'structured');
});

test('ordered list at line-start → enumerated', () => {
    assert.equal(inferLayout('1. First\n2. Second'), 'enumerated');
});

test('plain prose → prose', () => {
    assert.equal(inferLayout('Hello world, this is just prose.'), 'prose');
});

test('prose containing # mid-sentence → prose (not structured)', () => {
    assert.equal(inferLayout('Please file issue #42 now.'), 'prose');
});
```

- [ ] **Step 2: Verify tests fail**

- [ ] **Step 3: Implement `preWarm.js`**

```javascript
export function inferLayout(firstChars) {
    if (/```/.test(firstChars)) return 'code-heavy';
    if (/(^|\n)#{1,3} /.test(firstChars)) return 'structured';
    if (/(^|\n)\d+\. /.test(firstChars)) return 'enumerated';
    return 'prose';
}

/**
 * Apply the inferred layout class to the container.
 * Called once per stream, after the first ~100 chars arrive.
 */
export function applyLayout(containerEl, firstChars) {
    const layout = inferLayout(firstChars);
    containerEl.dataset.streamLayout = layout;
    return layout;
}
```

- [ ] **Step 4: Verify tests pass**

- [ ] **Step 5: Commit**

```bash
git add app/static/js/stream/preWarm.js tests/stream/preWarm.test.mjs
git commit -m "feat(stream): pre-warm layout inference"
```

---

## Task 18: Stream harness — DOM-level test page

**Files:**
- Create: `app/static/test/stream-harness.html`

A manually-loadable page that imports each renderer and exercises it against fixtures. Not CI-driven; a human opens it or the `browse` skill drives it.

- [ ] **Step 1: Write the harness page**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Stream Renderer Harness</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<link rel="stylesheet" href="/static/css/stream-blocks.css">
<script src="https://cdn.jsdelivr.net/npm/markdown-it@14.0.0/dist/markdown-it.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.25.0/prism.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.25.0/plugins/autoloader/prism-autoloader.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script src="/static/js/morphdom.min.js"></script>
<script>
    const md = window.markdownit({ html: false, linkify: true });
    window.marked = { parse: (t) => md.render(t || '') };
</script>
<style>
    body { background: #1a1a1a; color: #e2e8f0; font-family: system-ui; padding: 2rem; }
    .harness-case { border: 1px solid #333; padding: 1rem; margin-bottom: 1rem; }
    .harness-case h2 { margin-top: 0; font-size: 1rem; color: #9ca3af; }
    .pass { color: #10b981; }
    .fail { color: #ef4444; }
</style>
</head>
<body>
<h1>Stream renderer harness</h1>
<div id="results"></div>
<script type="module">
import { createProseRenderer } from '/static/js/stream/renderers/prose.js';
import { createCodeRenderer } from '/static/js/stream/renderers/code.js';
import { createListRenderer } from '/static/js/stream/renderers/list.js';
import { createMathRenderer } from '/static/js/stream/renderers/math.js';
import { createMermaidRenderer } from '/static/js/stream/renderers/mermaid.js';
import { transferCursor, removeCursor } from '/static/js/stream/cursor.js';

const results = document.getElementById('results');

function addCase(title, build) {
    const wrapper = document.createElement('div');
    wrapper.className = 'harness-case';
    const h = document.createElement('h2');
    h.textContent = title;
    wrapper.appendChild(h);
    const target = document.createElement('div');
    wrapper.appendChild(target);
    results.appendChild(wrapper);
    try {
        build(target);
    } catch (err) {
        const p = document.createElement('p');
        p.className = 'fail';
        p.textContent = `FAIL: ${err.message}`;
        wrapper.appendChild(p);
    }
}

addCase('prose renderer: bold + italic + link', (target) => {
    const r = createProseRenderer();
    const block = { id: 'b1', type: 'paragraph', meta: {}, text: '', frozen: false, el: null };
    r.create(block, target);
    block.text = 'Hello **bold** and *italic* and [link](https://x.io).';
    r.update(block);
});

addCase('code renderer: python with Prism', (target) => {
    const r = createCodeRenderer();
    const block = { id: 'b2', type: 'code', meta: { lang: 'python' }, text: '', frozen: false, el: null };
    r.create(block, target);
    block.text = 'def foo():\n    return 42';
    r.update(block);
    r.finalize(block);
});

addCase('list renderer: three items with stagger', (target) => {
    const r = createListRenderer();
    const block = { id: 'b3', type: 'list-ul', meta: {}, text: '- one\n- two\n- three\n', frozen: false, el: null };
    r.create(block, target);
    r.update(block);
});

addCase('math renderer: E = mc^2', (target) => {
    const r = createMathRenderer();
    const block = { id: 'b4', type: 'math', meta: {}, text: 'E = mc^2', frozen: false, el: null };
    r.create(block, target);
    r.finalize(block);
});

addCase('mermaid renderer: simple graph', async (target) => {
    const r = createMermaidRenderer();
    const block = { id: 'b5', type: 'mermaid', meta: {}, text: 'graph TD; A-->B;', frozen: false, el: null };
    r.create(block, target);
    await r.finalize(block);
});

addCase('cursor transfer: moves from block A tail to block B tail', (target) => {
    const a = document.createElement('div'); a.textContent = 'Block A'; target.appendChild(a);
    const b = document.createElement('div'); b.textContent = 'Block B'; target.appendChild(b);
    transferCursor(target, a);
    if (target.querySelectorAll('.stream-cursor').length !== 1) throw new Error('expected 1 cursor after first transfer');
    transferCursor(target, b);
    if (target.querySelectorAll('.stream-cursor').length !== 1) throw new Error('expected 1 cursor after second transfer');
    if (!b.contains(target.querySelector('.stream-cursor'))) throw new Error('cursor not inside block B');
    const ok = document.createElement('p');
    ok.className = 'pass';
    ok.textContent = 'PASS';
    target.parentElement.appendChild(ok);
});
</script>
</body>
</html>
```

- [ ] **Step 2: Load the harness in a browser**

Start the Flask dev server and open `/static/test/stream-harness.html`. Every case should render without `FAIL` text. The cursor case should show `PASS`.

- [ ] **Step 3: Commit**

```bash
git add app/static/test/stream-harness.html
git commit -m "test: DOM harness for stream renderers + cursor transfer"
```

---

## Task 19: Rewire `streamRenderer.js` internals

**Files:**
- Modify: `app/static/js/streamRenderer.js`

This is where it all comes together. The outer class API (`append`, `finalize`, `destroy`) is preserved — call sites in `codeWindow.js`, `startNewChatModule.js`, `voiceModeModule.js` are untouched. The internals delegate to the state machine + AST + renderers. The file becomes an ES module but still sets `window.StreamRenderer` at the end.

- [ ] **Step 1: Rewrite `streamRenderer.js`**

```javascript
/**
 * StreamRenderer — block-oriented progressive markdown.
 *
 * SSE tokens → L2 state machine → L3 block AST → per-type renderers → DOM.
 * Frozen blocks never re-render. Cursor transfers on BLOCK_START.
 *
 * Public contract (unchanged from 2026-04-10 spec):
 *   new StreamRenderer(container).append(text).finalize() / .destroy()
 */

import { createBlockStateMachine } from './stream/blockStateMachine.js';
import { createBlockAST } from './stream/blockAST.js';
import { createProseRenderer } from './stream/renderers/prose.js';
import { createCodeRenderer } from './stream/renderers/code.js';
import { createListRenderer } from './stream/renderers/list.js';
import { createMathRenderer } from './stream/renderers/math.js';
import { createMermaidRenderer } from './stream/renderers/mermaid.js';
import { applyLayout } from './stream/preWarm.js';
import { transferCursor, removeCursor } from './stream/cursor.js';

const PREWARM_THRESHOLD = 100; // chars before we fire layout inference

function rendererFor(blockType) {
    switch (blockType) {
        case 'code':       return _renderers.code;
        case 'mermaid':    return _renderers.mermaid;
        case 'math':       return _renderers.math;
        case 'list-ul':
        case 'list-ol':    return _renderers.list;
        case 'paragraph':
        case 'heading':
        case 'blockquote':
        default:           return _renderers.prose;
    }
}

const _renderers = {
    prose: createProseRenderer(),
    code: createCodeRenderer(),
    list: createListRenderer(),
    math: createMathRenderer(),
    mermaid: createMermaidRenderer(),
};

class StreamRenderer {
    constructor(container) {
        this.container = container;
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

    append(text) {
        if (this.destroyed || this.finalized) return;
        this.rawBuffer += text;
        if (!this.prewarmDone && this.rawBuffer.length >= PREWARM_THRESHOLD) {
            applyLayout(this.messageEl, this.rawBuffer.slice(0, PREWARM_THRESHOLD));
            this.prewarmDone = true;
        }
        this.sm.append(text);
        this._scheduleRender();
    }

    finalize() {
        if (this.destroyed || this.finalized) return;
        this.finalized = true;
        if (!this.prewarmDone) {
            applyLayout(this.messageEl, this.rawBuffer);
            this.prewarmDone = true;
        }
        this.sm.finalize();
        const live = this.ast.live();
        if (live) {
            const r = rendererFor(live.type);
            r.finalize?.(live);
        }
        // Finalize any frozen blocks that still have pending async work (mermaid)
        for (const block of this.ast.blocks()) {
            if (block.frozen) {
                const r = rendererFor(block.type);
                if (block.type === 'mermaid') r.finalize?.(block);
            }
        }
        removeCursor(this.messageEl);
    }

    destroy() {
        this.destroyed = true;
        removeCursor(this.messageEl);
    }

    _handleEvent(event) {
        switch (event.type) {
            case 'BLOCK_START': {
                const block = this.ast.startBlock({
                    id: event.id,
                    type: event.blockType,
                    meta: event.meta,
                });
                const r = rendererFor(block.type);
                r.create(block, this.messageEl);
                transferCursor(this.messageEl, block.el);
                break;
            }
            case 'BLOCK_APPEND': {
                this.ast.appendToLive(event.text);
                // Render is batched via RAF — don't render inline here
                break;
            }
            case 'BLOCK_END': {
                const live = this.ast.live();
                if (live) {
                    const r = rendererFor(live.type);
                    r.update(live);
                    r.finalize?.(live);
                }
                this.ast.endBlock();
                break;
            }
            // LIST_ITEM_START / LIST_ITEM_END are advisory — the list renderer
            // parses items out of block.text directly.
        }
    }

    _scheduleRender() {
        if (this.rafScheduled) return;
        this.rafScheduled = true;
        requestAnimationFrame(() => {
            this.rafScheduled = false;
            if (this.destroyed) return;
            const live = this.ast.live();
            if (!live) return;
            const r = rendererFor(live.type);
            performance.mark('stream-render-start');
            r.update(live);
            performance.mark('stream-render-end');
            performance.measure('stream-render', 'stream-render-start', 'stream-render-end');
        });
    }
}

window.StreamRenderer = StreamRenderer;
export { StreamRenderer };
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/streamRenderer.js
git commit -m "feat(stream): rewire StreamRenderer internals — L2+L3+L4 dispatch"
```

---

## Task 20: Wire script tags + stylesheet into `index.html`

**Files:**
- Modify: `app/templates/index.html`

- [ ] **Step 1: Add stylesheet link in `<head>`**

Near the existing CSS links (inspect and match style):
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/stream-blocks.css') }}">
```

- [ ] **Step 2: Convert the StreamRenderer script tag to a module**

Replace the existing line:
```html
<script src="{{ url_for('static', filename='js/streamRenderer.js') }}"></script>
```
with:
```html
<script type="module" src="{{ url_for('static', filename='js/streamRenderer.js') }}"></script>
```

- [ ] **Step 3: Verify the dev server serves the new modules with correct MIME type**

Start Flask dev server, open the app in a browser, check DevTools Network tab:
- `streamRenderer.js` — 200, `Content-Type: text/javascript` (or `application/javascript`)
- `stream/blockStateMachine.js` — 200
- `stream/renderers/*.js` — 200
- `stream-blocks.css` — 200

- [ ] **Step 4: Commit**

```bash
git add app/templates/index.html
git commit -m "chore(stream): load stream modules + block CSS in index.html"
```

---

## Task 21: Manual QA on three canonical prompts

**Files:** none created; this is a verification task.

The success criteria say XelaSphere streaming should feel smoother than ChatGPT/Claude on three canonical prompts. Run them live and record the result.

- [ ] **Step 1: Run the "long code block" prompt**

In the running app, send a prompt that elicits a >200-line Python function. Watch the code block stream. Verify:
- Skeleton `<pre>` appears immediately when the opening fence is seen.
- Syntax highlighting updates as tokens arrive (pre-registered `python` highlights instantly).
- `is-streaming` shimmer visible while streaming.
- Copy button appears only on close.
- No flicker.

- [ ] **Step 2: Run the "nested list" prompt**

Send a prompt that elicits a 10-item list with multi-line items. Verify:
- Each `<li>` appears with staggered entrance animation (60ms, 30ms delay per index).
- Continuation lines append to the current item (not a new one).
- The tail item is the one that re-renders as new tokens arrive.

- [ ] **Step 3: Run the "mixed prose + math" prompt**

Send a prompt that elicits prose followed by a `$$…$$` block followed by more prose. Verify:
- Pending placeholder (`…`) visible while the math block streams.
- KaTeX renders on close.
- No half-rendered LaTeX ever appears.

- [ ] **Step 4: Record a 10-second screen capture of each**

Keep the three clips. They are the success-criteria artifact — if the work needs defending later, these are the evidence.

- [ ] **Step 5: Commit a QA note**

```bash
echo "2026-04-12 manual QA notes — see recordings" > docs/superpowers/notes/2026-04-12-streaming-organism-qa.md
git add docs/superpowers/notes/2026-04-12-streaming-organism-qa.md
git commit -m "docs(stream): manual QA notes for three canonical prompts"
```

---

## Task 22: Performance verification — hard-number success criterion

**Files:**
- Create: `app/static/test/stream-perf.html`

The spec's hard-number success criterion: live-block re-render CPU flat across response length — no more than 2× cost at 2000 words vs 200 words, via `performance.mark`.

- [ ] **Step 1: Write the perf page**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Stream Perf</title>
<link rel="stylesheet" href="/static/css/stream-blocks.css">
<script src="https://cdn.jsdelivr.net/npm/markdown-it@14.0.0/dist/markdown-it.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js"></script>
<script src="/static/js/morphdom.min.js"></script>
<script>
    const md = window.markdownit({ html: false, linkify: true });
    window.marked = { parse: (t) => md.render(t || '') };
</script>
</head>
<body>
<h1>Stream perf</h1>
<pre id="report"></pre>
<div id="stage"></div>
<script type="module">
import { StreamRenderer } from '/static/js/streamRenderer.js';

function wordsOf(n) {
    return Array.from({ length: n }, (_, i) =>
        i % 50 === 49 ? 'word.\n\n' : 'word'
    ).join(' ');
}

async function streamAtSize(wordCount) {
    const stage = document.getElementById('stage');
    stage.innerHTML = '';
    const renderer = new StreamRenderer(stage);
    const text = wordsOf(wordCount);
    const chunks = text.match(/.{1,24}/gs) || [];

    performance.clearMeasures('stream-render');
    for (const chunk of chunks) {
        renderer.append(chunk);
        await new Promise(r => requestAnimationFrame(r));
    }
    renderer.finalize();

    const measures = performance.getEntriesByName('stream-render');
    const total = measures.reduce((a, m) => a + m.duration, 0);
    const avg = total / measures.length;
    return { total, avg, count: measures.length };
}

(async () => {
    const small = await streamAtSize(200);
    const large = await streamAtSize(2000);
    const ratio = large.avg / small.avg;
    document.getElementById('report').textContent = JSON.stringify({
        small_avg_ms: small.avg.toFixed(3),
        large_avg_ms: large.avg.toFixed(3),
        ratio: ratio.toFixed(2),
        pass: ratio <= 2 ? 'PASS' : 'FAIL',
    }, null, 2);
})();
</script>
</body>
</html>
```

- [ ] **Step 2: Open the perf page**

Start Flask dev server and navigate to `/static/test/stream-perf.html`. Wait for the report to populate (~5 seconds).

Expected: `"pass": "PASS"`. If FAIL, investigate — the live-block re-render is touching frozen DOM somewhere, or the list renderer is re-rendering completed items.

- [ ] **Step 3: Commit the perf page**

```bash
git add app/static/test/stream-perf.html
git commit -m "test(stream): perf page verifies 2x ratio at 2000 vs 200 words"
```

---

## Task 23: Final regression check

**Files:** none created; verification only.

- [ ] **Step 1: Run the existing Python test suite**

```bash
pytest tests/ -x
```
Expected: all tests pass — this is a pure client-side change; backend tests must not regress.

- [ ] **Step 2: Run the Node test suite**

```bash
node --test tests/stream/
```
Expected: all tests pass, including the 30-item corpus.

- [ ] **Step 3: Exercise the app end-to-end manually**

- Log in, send a short prose message, verify streaming works.
- Send a message that triggers an artifact (`create_artifact` action) — verify StreamRenderer destroys cleanly and the artifact path takes over.
- Open the app in a second tab, verify background-tab session still works (ProgressiveTextDisplay path, untouched).
- Open the history panel and reopen an old chat, verify history replay is unchanged (ProgressiveTextDisplay path, untouched).

- [ ] **Step 4: Commit a final marker**

```bash
git commit --allow-empty -m "chore(stream): verification complete — L2+L3+scoped L4+pre-warm shipped"
```

---

## Self-review notes

- Spec coverage: every In-Scope item in the spec maps to a task above. L2 → Tasks 3–8, L3 → Task 9, scoped L4 → Tasks 12–16, pre-warm → Task 17, entrance animations → Task 11, cursor transfer on BLOCK_START → Task 10 + Task 19 `_handleEvent`. 30-item corpus → Tasks 2 + 8. Hard-number success criterion → Task 22. Three-canonical-prompt success criterion → Task 21.
- No placeholders: every code block is complete runnable code.
- Type consistency: `blockType` vs `type` — event objects use `blockType` (the block's markdown type), AST nodes use `type`. Be deliberate about this when reading. The StreamRenderer's `_handleEvent` translates between them.
- Module vs IIFE: `streamRenderer.js` becomes an ES module; it still sets `window.StreamRenderer` so existing call sites (`new window.StreamRenderer(...)`) keep working.

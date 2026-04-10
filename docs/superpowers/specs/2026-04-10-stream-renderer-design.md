# StreamRenderer: Progressive Markdown Streaming

**Date**: 2026-04-10
**Status**: Approved
**Scope**: Replace the re-animate-at-done pattern with direct SSE-to-DOM rendering

## Problem

SSE tokens arrive from the API in real time, but the current architecture throws them away. Content chunks accumulate in a buffer, and when the `done` event fires, `displayMessage` re-creates the message from scratch and `ProgressiveTextDisplay` re-animates it with fake 100-200ms delays and artificial 30-80 character chunks. The result feels disconnected from the model's actual output speed.

## Architecture

```
Current:  SSE chunks → buffer[] → done → displayMessage → ProgressiveTextDisplay → fake animation
New:      SSE chunks → StreamRenderer.append(token) → incremental DOM update → done → finalize()
```

### StreamRenderer Class

New file: `static/js/streamRenderer.js` (~200 lines)

```
StreamRenderer {
  constructor(containerEl)    // the .message-content div
  append(text)                // called per SSE content chunk
  finalize()                  // called on done — flush, remove cursor, final transforms
  destroy()                   // abort — remove cursor, clear state (for artifact escape)
}
```

Internal state:
- `rawBuffer` — all text received (string)
- `renderedLength` — how much has been rendered to DOM
- A single `requestAnimationFrame` loop processes the delta between `rawBuffer` and `renderedLength`

RAF batching is the correct middle ground: tokens arrive in bursts (fast models send 5-10 per SSE chunk), and batching into animation frames avoids layout thrash while maintaining 60fps rendering.

### ProgressiveTextDisplay Stays

ProgressiveTextDisplay continues handling:
- History replay animation
- Background session buffering (inactive tab blue dot)
- `hasActiveDisplay()` / `complete()` / `remove()` API

StreamRenderer handles all live SSE streams. The two classes coexist.

## Progressive Markdown: Safe Boundary Scanner

### The Problem

Naive delta rendering breaks markdown mid-construct:

```
rawBuffer:      "Here is **bold"
renderedLength: 14
delta:          "**bold"  ← orphaned ** renders as literal asterisks
```

### Solution: Two-Stage Pipeline

```
Stage 1: Safe Boundary Scanner (per RAF frame, <1ms)
  rawBuffer → find last safe render point → safeBuffer

Stage 2: Incremental HTML
  safeBuffer → marked.parse() → morphdom(container, newHTML) → DOM patched
```

### Safe Render Point Rules

A position where all markdown constructs to the left are closed or complete:

1. Unclosed `**` or `*` or `__` or `_` → back up to before the opener
2. Unclosed `` ` `` → back up to before the backtick
3. Unclosed ` ``` ` → render nothing past the fence (500-char fallback below)
4. Unclosed `[` or `](` → back up to before the `[`
5. Incomplete line (no trailing `\n`) → back up to last `\n`
6. Everything else → safe at current position

Rule 5 gives per-item list animation for free: each `<li>` appears when the next `\n- ` arrives.

### State Machine

```
State: { openEmphasis: 0, openStrong: 0, openCode: false, openFence: false, openLink: 0 }
```

Scan forward through `rawBuffer`, toggling state on marker characters. Safe point = last position where all counters are zero.

**Nested constructs**: `"**bold with `code` inside**"` — backtick opens and closes within strong emphasis without affecting `openStrong`. The state machine tracks each construct independently.

**Malformed markdown fallback**: If scanner advances 200+ characters without finding a safe point, fall back to the last `\n`. This caps worst-case cost and handles all malformed input gracefully.

**Cost**: O(n) on the full buffer per frame, but buffers are typically <5KB. Well under 1ms at 16KB.

### Code Fence Exception

While `openFence` is true, the scanner marks the entire block as unsafe. Code blocks appear all at once when the closing ` ``` ` arrives.

**Long code blocks**: If the open fence buffer exceeds 500 characters, render the partial content inside `<pre><code>` with a visual streaming indicator. Content re-renders cleanly when the fence closes.

## DOM Reconciliation: morphdom

### Why Not innerHTML

When a closing `**` or `` ` `` arrives, `marked.parse()` retroactively changes earlier HTML — `**bold` becomes `<strong>bold</strong>`. A simple `innerHTML` swap would nuke event listeners on existing interactive elements (citation pills, source buttons).

### morphdom (1.5KB)

```javascript
morphdom(container, '<div>' + newHTML + '</div>', {
    childrenOnly: true,
    onBeforeElUpdated: (from, to) => {
        if (from.classList?.contains('citation-pill')) return false;
        if (from.classList?.contains('sources-btn')) return false;
        return true;
    }
});
```

morphdom diffs actual DOM nodes — only touches what changed, preserves event listeners on unchanged nodes, handles retroactive markdown re-renders correctly.

## Content-Aware Speed

No artificial delays. The model's natural token rate IS the speed.

| Content | Render Strategy |
|---------|----------------|
| Prose | Flush every RAF frame |
| Code fence | Buffer until close (or 500-char fallback) |
| Inline code | Scanner holds until backtick closes |
| Bold/italic | Scanner holds until construct closes |

Removing the 100-200ms fake delays from ProgressiveTextDisplay IS the feature.

## Per-Item List Animation

Handled naturally by safe boundary Rule 5 (incomplete line → back up to last `\n`):

```
Frame 1: "- First item\n- Second"  → safe at \n → renders "First item" as <li>
Frame 2: "- First item\n- Second item\n" → safe at end → patches in "Second item" as <li>
```

Last list item renders when a non-list line follows or `finalize()` is called.

## Cursor Design

CSS-only inline blinking line. No JS positioning.

```css
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
    50% { opacity: 0; }
}
```

The cursor is a `<span class="stream-cursor">` appended to the HTML string before morphdom runs. morphdom preserves it across patches. It flows inline with the last word — no absolute positioning, no layout reads.

**Safari fallback**: If `steps(2)` renders incorrectly, use `steps(1, end)`.

### Cursor Lifecycle

| Event | Cursor State |
|-------|-------------|
| First SSE content chunk | Cursor appears |
| Subsequent chunks | Advances with text (morphdom repositions) |
| Token gap > 500ms | Keeps blinking (CSS animation) |
| `done` event | `finalize()` removes cursor |
| History render | No cursor (ProgressiveTextDisplay path) |

## Migration Strategy

### Phase 1: Add StreamRenderer (zero risk)

Create `static/js/streamRenderer.js` with StreamRenderer class, safe boundary scanner, morphdom integration, cursor injection. Load morphdom.

Nothing references it. Console-testable.

### Phase 2: Wire into SSE content chunks

In sendMessage() streaming loop, on first content chunk:

```javascript
const targetEl = document.getElementById(searchMsgId || placeholderId)
    ?.querySelector('.message-content');
if (!targetEl) {
    console.warn('[StreamRenderer] No target element — falling back');
    _streamRenderer = null;
} else {
    _streamRenderer = new StreamRenderer(targetEl);
}
```

On subsequent content chunks:
```javascript
if (_streamRenderer) {
    _streamRenderer.append(chunkText);
}
```

`_contentChunks` array still accumulates for backward compatibility.

### Phase 3: Finalize instead of re-render

In `done` handler:

```javascript
if (_streamRenderer) {
    _streamRenderer.finalize();
    const messageContainer = document.getElementById(aiMsgId);
    // Skip displayMessage — content already in DOM
} else {
    // Non-streaming fallback — keep existing displayMessage path
    const messageContainer = displayMessage(displayContent, 'ai', ...);
}
```

### Phase 4: Edge cases

**Artifact escape hatch** (keyed off `parsed_action.action`, not a streaming variable):
```javascript
if (_streamRenderer && parsed_action?.action === 'create_artifact') {
    _streamRenderer.destroy();
    _streamRenderer = null;
    // fall through to existing artifact path
}
```

**Background sessions**: If `!isSessionActive()`, don't create StreamRenderer. Existing ProgressiveTextDisplay background path handles it.

**Stream error**: `_streamRenderer.finalize()` renders whatever was received.

### Phase 5: Cleanup

1. **Audit IntersectionObserver**: `grep -n "destroyObserver\|observeCursor\|scrollObserver"` — extract non-cursor logic before deleting
2. Remove ProgressiveTextDisplay from streaming path
3. Remove `_contentChunks` accumulation (keep backend `content_buffer` for `chat_summary`)
4. Remove fake chunking: `createChunks()`, `getRandomDelay()`, `MIN/MAX_CHUNK_SIZE`, `MIN/MAX_DELAY`
5. CSS: delete `.streaming-cursor` pulse, add `.stream-cursor` blink

### Rollback

The `_streamRenderer` null check at Phase 3 IS the feature flag. Set `_streamRenderer = null` unconditionally to revert. No code deletion needed until Phase 5.

## Files

### Created
| File | Purpose |
|------|---------|
| `static/js/streamRenderer.js` | StreamRenderer class + safe boundary scanner |
| `static/css/stream-cursor.css` | Blinking line cursor |
| morphdom (vendored or CDN) | DOM reconciliation |

### Modified
| File | Changes |
|------|---------|
| `codeWindow.js` sendMessage() | Wire StreamRenderer into content + done handlers |
| `debug.css` | Remove `.streaming-cursor` pulse styles |
| Base template | Add script tags for new files |

### Deleted (Phase 5 only)
| Code | Reason |
|------|--------|
| `chatgpt-pulse` keyframe | Replaced by `cursor-blink` |
| `.streaming-cursor` styles | Replaced by `.stream-cursor` |
| `.stream-fade-in` animation | morphdom patches incrementally |
| `createChunks()` | No fake chunking |
| `getRandomDelay()` | No artificial delays |
| Cursor positioning JS in ProgressiveTextDisplay | CSS-only cursor |

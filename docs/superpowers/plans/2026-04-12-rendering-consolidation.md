# Rendering Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse message rendering to one path per sender — StreamRenderer for every AI message (live + history + non-streamed), markdown-it inline for every user message — by deleting `ProgressiveTextDisplay`, the AI branch of `handleComplexMessage` / `appendTextMessage`, and `typeTextWorker.js`.

**Architecture:** After the 2026-04-12 replay-convergence work, `StreamRenderer` is already the canonical AI renderer for both live SSE and history replay (`startNewChatModule.js:4225-4243`). This plan removes the second, legacy path. User messages become a single trivial helper (`renderUserMessage`) that runs `md.render()` + `DOMPurify.sanitize()` into a `.text-message` element with no animation. AI text that arrives non-streamed (error fallbacks, certain displayMessage paths, history that misses the `sender==='ai' && isHistory` branch) is funneled into `new StreamRenderer(container, {replay: true}).append(text).finalize()`. The recipe/tree special-case detectors are preserved as a preprocessing gate *before* StreamRenderer. `typing-complete` is dispatched explicitly after finalize so `searchResults.js` keeps working. The final code path count drops from three (StreamRenderer + ProgressiveTextDisplay + typeTextWorker) to one per sender.

**Tech Stack:** Vanilla JS, markdown-it 14 (`window.md`), DOMPurify, existing `StreamRenderer` class at `app/static/js/streamRenderer.js`. No new dependencies. Verification is manual browser QA (no JS unit test harness in this repo); the existing `app/static/test/stream-harness.html` and `stream-perf.html` remain the StreamRenderer sanity checks.

---

## Pre-flight Context (read before starting)

**Call-sites that currently hit the legacy paths:**

| Site | Today | After |
|------|-------|-------|
| `codeWindow.js:2369` — `appendTextMessage(container, message, true)` | User-message path inside `appendTextMessage` | `renderUserMessage(container, message, messageId)` |
| `codeWindow.js:2502` — `handleComplexMessage(...)` in `displayMessage` default | AI fallback through ProgressiveTextDisplay | `renderAiTextImmediate(messageContent, safeMessage, messageId, showButtons)` |
| `codeWindow.js:4125` — `ProgressiveTextDisplay.start(..., null, ...)` | Background-session streaming simulation | Delete. `BackgroundRequestManager.completeRequest` already stores completion data; drop the fake-stream delay and mark the blue dot immediately. |
| `codeWindow.js:5195` — `ProgressiveTextDisplay.start(sessionId, html, messageElement, ...)` | AI branch of `appendTextMessage` | Deleted with the branch. |
| `startNewChatModule.js:4245` — `handleComplexMessage(...)` fallthrough | History non-AI + long-form | For `sender==='ai'`: route to StreamRenderer replay. For `sender==='user'`: route to `renderUserMessage`. |
| `startNewChatModule.js:1353-1376` — `ProgressiveTextDisplay` guards for session switch | Prevents duplicate rendering | Delete the checks; StreamRenderer lifecycle is guarded by `window._activeStreamRenderer`. |
| `script.js:1493-1497` — clear ProgressiveTextDisplay on session change | Cleanup | Delete. |
| `codeWindow.js:1311-1321, 3194-3208, 4563-4581` — various `ProgressiveTextDisplay` cleanup + cursor removal | Legacy cleanup | Delete (keep the `_activeStreamRenderer` branches in the same blocks). |
| `templates/index.html:1012` — `<script src=".../typeTextWorker.js">` | Worker script loaded at boot | Remove script tag. |

**Preserved behaviors (must not regress):**

1. Tree-structure detection (`isTreeStructure` → `createTreeView`) for AI text.
2. Recipe detection (`window.RecipeDetector.isRecipe` → `createRecipeCard`) for AI text.
3. Long-user-message → `createDocumentCard` (already handled before `appendTextMessage`/`handleComplexMessage`; untouched).
4. `typing-complete` event on the nearest `.message` ancestor after AI text finishes (consumed by `searchResults.js:1235`).
5. Model attribution (`window.addModelAttribution`) applied after AI finalize.
6. Math rendering in AI messages (StreamRenderer's math renderer already handles this inline — verify at runtime).
7. Background-session completion signaling (blue dot via `BackgroundRequestManager.updateSessionNotificationDot(sid, true)`).

**Out of scope (do not touch):**

- `ghostModel.js` ghost-suggestion typing (separate typing system).
- `dictationModule.js` / voice input.
- Artifact streaming (`artifactPanel.js`).
- Code-window rendering (`createAndAppendCodeSection` stays — it was only *called from* the AI branch; it stays as a utility for artifact/code rendering elsewhere if still used, or gets pruned in Task 10 if not).

---

## Task 1: Create `renderUserMessage` helper

**Files:**
- Modify: `app/static/js/codeWindow.js` (add helper near line 5115, before the deleted `appendTextMessage`)

- [ ] **Step 1: Add the helper function**

Insert immediately before the current `function appendTextMessage` declaration at `codeWindow.js:5119`:

```javascript
/**
 * Render a user message: markdown-it inline → DOMPurify → innerHTML.
 * No streaming, no code-window chrome, no animation.
 * User code blocks render as <pre><code> via markdown-it's default rules —
 * intentionally plain, matching the text bubble styling.
 */
function renderUserMessage(container, text, messageId) {
    if (container && container.dataset.emailComposer === 'true') {
        return;
    }
    const div = document.createElement('div');
    div.classList.add('text-message-container');
    if (messageId) {
        div.id = `message-${messageId}`;
    }
    container.appendChild(div);

    const p = document.createElement('p');
    p.classList.add('text-message');
    div.appendChild(p);

    const html = window.md ? window.md.render(text || '') : (window.marked?.parse(text || '') || '');
    p.innerHTML = DOMPurify.sanitize(html);
}
```

- [ ] **Step 2: Verify function parses and the file still loads**

Run: `node --check app/static/js/codeWindow.js`
Expected: no output (syntax clean).

- [ ] **Step 3: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(rendering): add renderUserMessage helper (user-path stub)"
```

---

## Task 2: Create `renderAiTextImmediate` helper

**Files:**
- Modify: `app/static/js/codeWindow.js` (add helper directly after `renderUserMessage`)

- [ ] **Step 1: Add the helper**

Insert after `renderUserMessage` (added in Task 1):

```javascript
/**
 * Render an AI text message that did NOT come from the live SSE stream.
 * Routes through StreamRenderer (replay mode) so history, fallbacks,
 * and non-streamed responses produce identical DOM to the live stream.
 * Preserves tree-view and recipe special cases.
 *
 * `container` is the .message-content element; this function appends to it.
 */
function renderAiTextImmediate(container, text, messageId, showButtons) {
    if (container && container.dataset.emailComposer === 'true') {
        return;
    }
    if (!text || typeof text !== 'string') {
        return;
    }

    // Special widget: tree view
    if (typeof isTreeStructure === 'function' && isTreeStructure(text, null)) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('text-message-container');
        wrapper.appendChild(createTreeView(text));
        container.appendChild(wrapper);
        if (showButtons && typeof appendActionButtons === 'function') {
            appendActionButtons(container, messageId);
        }
        return;
    }

    // Special widget: recipe card
    if (window.RecipeDetector && window.RecipeDetector.isRecipe(text, null)) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('text-message-container');
        wrapper.appendChild(window.RecipeDetector.createRecipeCard(text));
        container.appendChild(wrapper);
        if (showButtons && typeof appendActionButtons === 'function') {
            appendActionButtons(container, messageId);
        }
        return;
    }

    // Default: StreamRenderer in replay mode (no typing animation, DOM matches live)
    const renderer = new StreamRenderer(container, { replay: true });
    renderer.append(text);
    renderer.finalize();

    // Model attribution (live stream applies this at finalize; replay needs it explicit)
    const msgDiv = container.closest('.message');
    if (msgDiv && msgDiv._modelMeta && typeof window.addModelAttribution === 'function') {
        window.addModelAttribution(msgDiv, msgDiv._modelMeta);
        delete msgDiv._modelMeta;
    }

    // typing-complete event — preserves searchResults.js:1235 contract
    if (msgDiv) {
        msgDiv.dispatchEvent(new CustomEvent('typing-complete', { bubbles: false }));
    }

    if (showButtons && typeof appendActionButtons === 'function') {
        appendActionButtons(container, messageId);
    }
}
```

- [ ] **Step 2: Verify**

Run: `node --check app/static/js/codeWindow.js`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(rendering): add renderAiTextImmediate helper (AI replay-path stub)"
```

---

## Task 3: Redirect user-message call site to `renderUserMessage`

**Files:**
- Modify: `app/static/js/codeWindow.js:2369`

- [ ] **Step 1: Read the surrounding block**

Read `app/static/js/codeWindow.js` lines 2355-2375 to confirm the call pattern.

- [ ] **Step 2: Replace the call**

Change:
```javascript
    } else {
        appendTextMessage(container, message, true);
    }
```
to:
```javascript
    } else {
        renderUserMessage(container, message, messageId);
    }
```

- [ ] **Step 3: Browser verification**

Start the dev server (`python run.py` or whatever the repo's standard is — check `README.md`). Load the app, send a user message with markdown (e.g., `**bold** and \`code\` and a list:\n- one\n- two`). Confirm:
- Rendered with bold + inline code + bullet list
- No typing animation (instant)
- No console errors

- [ ] **Step 4: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "refactor(rendering): route user messages through renderUserMessage"
```

---

## Task 4: Redirect AI displayMessage default to `renderAiTextImmediate`

**Files:**
- Modify: `app/static/js/codeWindow.js:2502`

- [ ] **Step 1: Replace the switch default**

Change:
```javascript
            default:
                handleComplexMessage(messageContent, safeMessage, messageType, messageId, showButtons);
```
to:
```javascript
            default:
                renderAiTextImmediate(messageContent, safeMessage, messageId, showButtons);
```

- [ ] **Step 2: Browser verification**

Reload the app. Open an existing chat that has AI messages not from live streaming (e.g., a re-displayed error fallback, or trigger one by forcing a non-streamed code path). Confirm the AI message renders via StreamRenderer (check DOM: should contain `.text-message-container > .text-message` with block-level children matching other StreamRenderer output). Confirm no `ProgressiveTextDisplay` cursor appears.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "refactor(rendering): route displayMessage AI default through StreamRenderer"
```

---

## Task 5: Redirect `startNewChatModule.js` history fallthrough

**Files:**
- Modify: `app/static/js/startNewChatModule.js:4244-4246`

- [ ] **Step 1: Replace the fallthrough**

At the `} else {` branch currently holding `handleComplexMessage(messageContent, safeContent, messageType, id, showButtons, isHistory);`, change to:

```javascript
            } else if (sender === 'ai') {
                // Non-history AI or AI path that missed the streaming branch
                renderAiTextImmediate(messageContent, safeContent, id, showButtons);
            } else {
                // User message (history or live re-render)
                renderUserMessage(messageContent, safeContent, id);
                if (showButtons && typeof appendActionButtons === 'function') {
                    appendActionButtons(messageContent, id);
                }
            }
```

- [ ] **Step 2: Browser verification**

Reload a chat from history. Confirm:
- AI messages render identically to live stream (same classes, same code windows for fenced blocks — via StreamRenderer's code renderer)
- User messages render inline with markdown, no animation
- Action buttons appear under messages where expected
- No `handleComplexMessage` console.log lines appear

- [ ] **Step 3: Commit**

```bash
git add app/static/js/startNewChatModule.js
git commit -m "refactor(rendering): remove handleComplexMessage fallthrough in history path"
```

---

## Task 6: Eliminate background-session ProgressiveTextDisplay at `codeWindow.js:4125`

**Files:**
- Modify: `app/static/js/codeWindow.js:4095-4134`

- [ ] **Step 1: Read the block**

Read `app/static/js/codeWindow.js` lines 4090-4140 to confirm structure.

- [ ] **Step 2: Replace the fake-stream block**

Replace the block starting at `// Start background streaming with null container` (around line 4120) through the end of the `ProgressiveTextDisplay.start(...)` call (around line 4132) with:

```javascript
            // Session switched away — no fake-stream delay needed.
            // BackgroundRequestManager.completeRequest already stored completion data;
            // flip the blue-dot notification immediately.
            console.log('Background response captured for session:', requestSessionId);
            if (typeof BackgroundRequestManager !== 'undefined') {
                BackgroundRequestManager.updateSessionNotificationDot(requestSessionId, true);
            }

            return null;
```

(Remove the surrounding `const htmlContent = marked.parse(...)` and `const sanitizedHtmlContent = DOMPurify.sanitize(...)` lines that only fed ProgressiveTextDisplay.)

- [ ] **Step 3: Browser verification**

Start a long AI response in session A, immediately switch to session B before it finishes. When it completes, confirm:
- Session A sidebar entry shows the blue-dot notification
- Switching back to session A shows the full message rendered via StreamRenderer (not a partial ProgressiveTextDisplay snapshot)
- No console errors

Note: the full-message render on switch-back comes from the history replay path already exercised in Task 5 — verify it triggers on session re-activation. If switch-back does NOT re-render the message (regression), the completion data path in `startNewChatModule.js` around line 1353-1376 may depend on ProgressiveTextDisplay's `hasCompletedDisplay`. In that case, proceed — Task 7 rewrites that block to read from `BackgroundRequestManager` directly.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "refactor(rendering): drop fake-stream for background sessions, signal completion directly"
```

---

## Task 7: Replace ProgressiveTextDisplay checks in session-switch handler

**Files:**
- Modify: `app/static/js/startNewChatModule.js:1350-1380`

- [ ] **Step 1: Confirm the real BackgroundRequestManager API — BEFORE writing any code**

**This step is mandatory and must happen before Step 3. Do not skip or reorder.**

Run:
```bash
grep -n "BackgroundRequestManager\." app/static/js/codeWindow.js
grep -n "BackgroundRequestManager\s*=\|BackgroundRequestManager\s*=>" app/static/js/codeWindow.js
```

Read the full `BackgroundRequestManager` definition block in `codeWindow.js` (near the top of the file around where it's declared, and every call site) to discover the actual method names for:
- reading completion data for a session
- clearing completion data for a session
- flipping the notification-dot state

Write the real names down before touching Step 3. If the method names in the plan (`getCompletedRequest`, `clearCompleted`) do not exist, substitute the real ones. Do not invent methods.

- [ ] **Step 2: Read the ProgressiveTextDisplay block**

Read `app/static/js/startNewChatModule.js` lines 1340-1390 to confirm what these checks gate.

- [ ] **Step 3: Replace the ProgressiveTextDisplay checks**

Any branch that reads `progressiveDisplay.hasActiveDisplay(sessionId)` or `progressiveDisplay.hasCompletedDisplay(sessionId)` should be replaced to read from `BackgroundRequestManager`:

```javascript
            // Was: ProgressiveTextDisplay.hasCompletedDisplay(sessionId)
            const bgComplete = typeof BackgroundRequestManager !== 'undefined'
                && BackgroundRequestManager.getCompletedRequest
                && BackgroundRequestManager.getCompletedRequest(sessionId);
```

Remove any `progressiveDisplay.complete(sessionId)` / `.remove(sessionId)` calls — the data lives in `BackgroundRequestManager` now.

If the existing block dispatches a render using `progressiveDisplay.resume(sessionId, container)`, replace with: pull the stored display content from `BackgroundRequestManager` (via whatever accessor exists), call `renderAiTextImmediate(container, displayContent, messageId, showButtons)`, then clear the completion via whatever clear method exists. Use the names confirmed in Step 1.

- [ ] **Step 4: Browser verification**

Repeat the background-session test from Task 6. Switch away, wait for completion, switch back. Message should render. No console errors.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/startNewChatModule.js
git commit -m "refactor(rendering): read background completion from BackgroundRequestManager, not ProgressiveTextDisplay"
```

---

## Task 8: Delete stale ProgressiveTextDisplay cleanup sites

**Files:**
- Modify: `app/static/js/codeWindow.js:1311-1325, 3194-3210, 4560-4585`
- Modify: `app/static/script.js:1490-1500`

- [ ] **Step 1: Remove guard at `codeWindow.js:1311-1325`**

Read lines 1305-1330. The block currently checks `ProgressiveTextDisplay?.hasActiveDisplay?.(currentSessionId)` as part of a streaming-active guard. If the guard is `window._activeStreamRenderer || ProgressiveTextDisplay?.hasActiveDisplay(...)`, shrink it to just the `_activeStreamRenderer` half. Remove the PTD line.

- [ ] **Step 2: Remove cursor-kill at `codeWindow.js:3194-3210`**

Read lines 3190-3215. Delete the lines that reference `ProgressiveTextDisplay.complete(sid)` and `ProgressiveTextDisplay.remove(sid)` and the comments above them. Keep the StreamRenderer setup below.

- [ ] **Step 3: Remove cleanup at `codeWindow.js:4560-4585`**

Read lines 4555-4590. Delete the block `if (currentSessionId && ProgressiveTextDisplay) { ProgressiveTextDisplay.complete(...); ProgressiveTextDisplay.remove(...); }`. Keep the surrounding StreamRenderer cleanup and cursor-removal (modify the comment that says "both StreamRenderer and ProgressiveTextDisplay" to just "StreamRenderer").

- [ ] **Step 4: Remove cleanup at `script.js:1490-1500`**

Read lines 1485-1505. Delete:
```javascript
    // Clear ProgressiveTextDisplay for the current session to prevent duplicates
    if (currentSessionId && window.ProgressiveTextDisplay) {
        window.ProgressiveTextDisplay.complete(currentSessionId);
        window.ProgressiveTextDisplay.remove(currentSessionId);
    }
```
and the comment directly above it.

- [ ] **Step 5: Syntax check**

Run:
```bash
node --check app/static/js/codeWindow.js && node --check app/static/script.js && node --check app/static/js/startNewChatModule.js
```
Expected: clean.

- [ ] **Step 6: Browser verification**

Full smoke pass:
1. New chat — send 3 messages, confirm AI streams + user renders inline.
2. Switch sessions mid-stream — confirm no broken cursors left behind.
3. Reload — history renders correctly.
4. Open DevTools Console — search for "ProgressiveTextDisplay". No references should fire.

- [ ] **Step 7: Commit**

```bash
git add app/static/js/codeWindow.js app/static/script.js
git commit -m "refactor(rendering): delete ProgressiveTextDisplay cleanup sites"
```

---

## Task 9: Delete `ProgressiveTextDisplay` IIFE and export

**Files:**
- Modify: `app/static/js/codeWindow.js:413-791`

- [ ] **Step 1: Confirm no remaining call sites**

Run:
```bash
grep -rn "ProgressiveTextDisplay" app/static/js app/static/script.js app/templates
```
Expected: zero live-code matches. Comments and the definition itself are the only hits.

If any call site remains, go back and handle it before proceeding.

- [ ] **Step 2: Delete the IIFE**

Delete everything from the comment `// ==================== Progressive Text Display ====================` (line 413) through and including `window.ProgressiveTextDisplay = ProgressiveTextDisplay;` (line 791).

- [ ] **Step 3: Syntax check**

Run: `node --check app/static/js/codeWindow.js`
Expected: clean.

- [ ] **Step 4: Browser verification**

Reload the app. Send a message. Open Console. No "ProgressiveTextDisplay is not defined" errors. All rendering behaves as in Task 8.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "refactor(rendering): delete ProgressiveTextDisplay module"
```

---

## Task 10: Delete `handleComplexMessage` and `appendTextMessage`

**Files:**
- Modify: `app/static/js/codeWindow.js:5119-5265`

- [ ] **Step 1: Confirm no remaining call sites**

Run:
```bash
grep -rn "handleComplexMessage\|appendTextMessage" app/static/js app/static/script.js app/templates
```
Expected: zero live-code matches (comments/CSS references are fine; they don't import the symbols).

- [ ] **Step 2: Delete both functions**

Delete the entire function `function appendTextMessage(container, text, isUserMessage, messageId, isHistory = false) { ... }` and the entire function `function handleComplexMessage(container, message, messageType, messageId, showButtons, isHistory = false) { ... }`. That is roughly lines 5119 through 5265 — the two function blocks back-to-back. Do NOT delete `createAndAppendCodeSection` above or `resetAndFocusInput` below.

- [ ] **Step 3: Check whether `createAndAppendCodeSection` has any remaining callers**

Run:
```bash
grep -rn "createAndAppendCodeSection" app/static/js
```
If zero hits (only the definition), delete that function too in the same commit. If it has live callers, leave it.

- [ ] **Step 4: Syntax check + browser smoke**

Run: `node --check app/static/js/codeWindow.js`
Reload, send messages, reload history. No errors.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "refactor(rendering): delete handleComplexMessage and appendTextMessage"
```

---

## Task 11: Delete `typeTextWorker.js`

**Files:**
- Delete: `app/static/js/typeTextWorker.js`
- Modify: `app/templates/index.html:1012` (remove script tag)

- [ ] **Step 1: Confirm zero runtime references**

Run:
```bash
grep -rn "typeTextWorker\|TypeTextWorker" app/static/js app/static/script.js app/templates
```
Expected: only the `index.html:1012` script tag (docs don't count).

- [ ] **Step 2: Remove the script tag**

In `app/templates/index.html`, delete the line:
```html
    <script src="{{ url_for('static', filename='js/typeTextWorker.js') }}"></script>
```

- [ ] **Step 3: Delete the file**

```bash
git rm app/static/js/typeTextWorker.js
```

- [ ] **Step 4: Browser verification**

Hard-reload (clear cache). Send messages. Confirm:
- No 404 for `typeTextWorker.js` in Network tab
- No "TypeTextWorker is not defined" errors
- Messages render correctly

- [ ] **Step 5: Commit**

```bash
git add app/templates/index.html
git commit -m "chore(rendering): remove typeTextWorker.js — unused after consolidation"
```

---

## Task 12: Update docs

**Files:**
- Modify: `docs/ARCHITECTURE_MAP.md:72, 290, 324`
- Modify: `docs/DEPENDENCIES.md:297`
- Modify: `docs/CHAT_FLOW.md:738`

- [ ] **Step 1: Remove `typeTextWorker.js` line from ARCHITECTURE_MAP.md tree (line 72)**

- [ ] **Step 2: Remove/rewrite the numbered item "6. typeTextWorker.js" at ARCHITECTURE_MAP.md:290**

Replace with a short note under rendering: "AI text renders through `StreamRenderer` (live + replay). User text renders through `renderUserMessage` (markdown-it + DOMPurify, no animation)."

- [ ] **Step 3: Update the flow at ARCHITECTURE_MAP.md:324**

Change "Stream AI response with typeTextWorker" to "Stream AI response with StreamRenderer".

- [ ] **Step 4: Remove the typeTextWorker row from DEPENDENCIES.md:297**

- [ ] **Step 5: Update CHAT_FLOW.md:738**

Replace the `await typeTextWorker.streamText(...)` snippet with the StreamRenderer equivalent:
```javascript
const renderer = new StreamRenderer(contentEl);
// SSE loop: renderer.append(chunk)
renderer.finalize();
```

- [ ] **Step 6: Commit**

```bash
git add docs/ARCHITECTURE_MAP.md docs/DEPENDENCIES.md docs/CHAT_FLOW.md
git commit -m "docs: reflect rendering consolidation (StreamRenderer + renderUserMessage)"
```

---

## Task 13: Final end-to-end verification

- [ ] **Step 1: Cold-start browser check**

Kill the dev server, restart, hard-reload. Open a fresh session.

- [ ] **Step 2: Golden-path matrix**

Run through each scenario; confirm no console errors and correct rendering:

| Scenario | Expected |
|----------|----------|
| User sends short message | Markdown inline, instant, no cursor |
| User sends long message (>triggers doc card) | Document card appears |
| User sends message with code fence | Inline `<pre><code>` via markdown-it |
| AI replies with streaming text | StreamRenderer animates token-by-token |
| AI replies with fenced code | Code block renders with StreamRenderer's code renderer |
| AI replies with math | KaTeX renders |
| AI replies with a recipe | Recipe card (not streamed) |
| AI replies with tree-like text | Tree view (not streamed) |
| Reload chat history | All historical AI messages render identically to live (StreamRenderer replay); user messages inline |
| Switch session mid-stream, wait, switch back | Blue dot on sidebar, full message shown on return |
| `searchResults.js` web-search response | `typing-complete` event fires (check DevTools event listener or add a temporary `console.log` in the listener) |

- [ ] **Step 3: Line-count sanity**

Run:
```bash
wc -l app/static/js/codeWindow.js
```
Should be roughly 500 lines smaller than starting 5324 (PTD ~377 lines + two functions ~150 lines).

Also:
```bash
ls app/static/js/typeTextWorker.js 2>&1
```
Expected: "No such file or directory".

- [ ] **Step 4: Grep for ghosts**

Run:
```bash
grep -rn "ProgressiveTextDisplay\|handleComplexMessage\|appendTextMessage\|typeTextWorker" app/static/js app/static/script.js app/templates
```
Expected: zero matches (or only in comments explaining *why* something was removed, if any were kept — should not be).

- [ ] **Step 5: If all green, tag the work**

```bash
git log --oneline -15
```
Confirm the 12 commits read as a coherent migration story.

---

## Rollback Strategy

Each task is its own commit. If a post-migration regression surfaces in production:

1. Identify the failing scenario from the Task 13 matrix.
2. `git revert <commit-sha>` for the narrowest task that introduced the regression — most likely Task 6 (background session) or Task 7 (session-switch handler), since those touch the most ambient behavior.
3. Task 11 (typeTextWorker delete) and Task 9 (PTD delete) are independently revertable because nothing else references them after Task 10.

---

## Non-Goals Reiterated

- No StreamRenderer changes. It's frozen as the canonical AI path; if it has bugs, file them separately.
- No new markdown-it plugins. The existing config (`html: false, linkify: true, typographer: true, breaks: false` + task-lists) is the user-message config.
- No test harness introduction. This repo verifies frontend changes in the browser; attempting to bolt on Jest/vitest for one migration is scope creep.
- No styling changes to user messages. They continue to inherit `.text-message` / `.text-message-container` styling.

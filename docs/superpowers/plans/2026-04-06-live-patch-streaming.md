# Live Patch Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream artifact patch replacements live during AI generation — each old→new swap animated in the code tab as the JSON arrives.

**Architecture:** Frontend-only change. Detect `artifact_patch` fence in SSE content chunks, incrementally parse complete patch objects from the stream buffer, animate each replacement in the code tab (red highlight old → green highlight new), then finalize with the server-patched artifact on Preview.

**Tech Stack:** Vanilla JS (codeWindow.js, artifactPanel.js), CSS animations (artifact-panel.css)

**Spec:** `docs/superpowers/specs/2026-04-06-live-patch-streaming-design.md`

---

### Task 1: Add CSS classes for patch-removing and patch-added

**Files:**
- Modify: `app/static/css/artifact-panel.css:2802` (after existing `patch-pulse` keyframes)

- [ ] **Step 1: Add `.patch-removing` and `.patch-added` styles**

At the end of `artifact-panel.css`, after the closing `}` of `@keyframes patch-pulse` (line 2802), add:

```css
.patch-removing {
  background: rgba(255, 70, 70, 0.25);
  border-left: 3px solid #ff4646;
  padding: 2px 4px;
  margin-left: -4px;
  display: inline;
  text-decoration: line-through;
  text-decoration-color: rgba(255, 70, 70, 0.6);
  animation: patch-remove-pulse 0.8s ease-in-out;
}

.patch-added {
  background: rgba(56, 229, 77, 0.25);
  border-left: 3px solid #38e54d;
  padding: 2px 4px;
  margin-left: -4px;
  display: inline;
  animation: patch-add-pulse 0.6s ease-out;
}

@keyframes patch-remove-pulse {
  0%   { background: rgba(255, 70, 70, 0.45); }
  100% { background: rgba(255, 70, 70, 0.25); }
}

@keyframes patch-add-pulse {
  0%   { background: rgba(56, 229, 77, 0.5); }
  100% { background: rgba(56, 229, 77, 0.25); }
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "feat(artifacts): add CSS for live patch remove/add highlights"
```

---

### Task 2: Update `_highlightTextInElement` to accept a custom class name

**Files:**
- Modify: `app/static/js/artifactPanel.js:1683`

The current signature is `_highlightTextInElement(root, searchStr)` and it hardcodes `'patch-highlight'` as the class on mark elements (lines 1726, 1744). Update it to accept an optional third parameter.

- [ ] **Step 1: Change the signature and all `className` references**

Change line 1683 from:
```javascript
_highlightTextInElement(root, searchStr) {
```
to:
```javascript
_highlightTextInElement(root, searchStr, className = 'patch-highlight') {
```

Change line 1726 from:
```javascript
mark.className = 'patch-highlight';
```
to:
```javascript
mark.className = className;
```

Change line 1744 from:
```javascript
m.className = 'patch-highlight';
```
to:
```javascript
m.className = className;
```

- [ ] **Step 2: Verify existing callers still work**

The existing callers in `applyPatchesVisually` (line ~1647) call `this._highlightTextInElement(this.codeBlock, patch.new_string)` with no third argument, so they'll get the default `'patch-highlight'`. No changes needed to callers.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): make _highlightTextInElement accept custom class"
```

---

### Task 3: Add `openPatchStreaming()` method to artifactPanel

**Files:**
- Modify: `app/static/js/artifactPanel.js` — add method after `applyPatchesVisually` (after line ~1677)

- [ ] **Step 1: Add `isPatchStreaming` and `_patchQueue` properties to constructor**

In the constructor (around line 711-803), after the existing streaming state properties (`this.isStreaming`, `this._streamBuffer`, etc.), add:

```javascript
this.isPatchStreaming = false;
this._patchQueue = Promise.resolve();
```

Find the exact location by searching for `this.isStreaming = false` in the constructor and adding after it.

- [ ] **Step 2: Add `openPatchStreaming()` method**

After the `finalizePatchStreaming` method placeholder location (after `applyPatchesVisually`'s closing `}` and before `_highlightTextInElement`), add:

```javascript
openPatchStreaming() {
    if (!this.currentVersion || !this.codeBlock) return;

    this.isPatchStreaming = true;
    this._patchQueue = Promise.resolve();

    // Switch to code tab showing current (pre-patch) code
    this.activeTab = 'code';
    this.switchTab('code');

    if (this.titleEl) {
        this.titleEl.textContent = 'Updating...';
    }

    // Ensure panel is open
    if (!this.panel.classList.contains('is-open')) {
        this.panel.classList.add('is-open');
        this.panel.setAttribute('aria-hidden', 'false');
        document.body.classList.add('artifact-panel-open');
        this.loadSavedSplit();
        this.updateOffset(false);
        this.adjustComposerWidth(true);
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): add openPatchStreaming() method"
```

---

### Task 4: Add `applyLivePatch()` and `_animatePatch()` methods

**Files:**
- Modify: `app/static/js/artifactPanel.js` — add after `openPatchStreaming()`

- [ ] **Step 1: Add `applyLivePatch()` — the public queuing method**

```javascript
applyLivePatch(file, oldStr, newStr) {
    if (!this.isPatchStreaming) return;
    this._patchQueue = this._patchQueue.then(() => this._animatePatch(file, oldStr, newStr));
}
```

- [ ] **Step 2: Add `_animatePatch()` — the async animation worker**

```javascript
async _animatePatch(file, oldStr, newStr) {
    if (!this.codeBlock) return;

    // Switch to target file if needed
    if (this.currentVersion?.files && this.currentFile?.path !== file) {
        const targetFile = this.currentVersion.files.find(f => f.path === file);
        if (targetFile) {
            this.renderCodeViewer(this.currentVersion, targetFile);
            if (this.fileSelect) {
                const idx = this.currentVersion.files.indexOf(targetFile);
                if (idx >= 0) this.fileSelect.value = String(idx);
            }
        }
    }

    // Wait a frame for render to settle
    await new Promise(r => requestAnimationFrame(r));

    const codeText = this.codeBlock.textContent || '';

    // Normalize for matching (JSON may have literal \n)
    let searchOld = oldStr;
    if (!codeText.includes(searchOld)) {
        searchOld = searchOld.replace(/\\n/g, '\n').replace(/\\t/g, '\t');
    }
    if (!codeText.includes(searchOld)) return; // can't find it, skip

    // Highlight old code in red
    this._highlightTextInElement(this.codeBlock, searchOld, 'patch-removing');
    const removeMark = this.codeBlock.querySelector('.patch-removing');
    if (removeMark) this._scrollToElement(removeMark);

    // Pause so user sees the red highlight
    await new Promise(r => setTimeout(r, 400));

    // Replace with new code, switch to green
    if (removeMark) {
        removeMark.textContent = newStr;
        removeMark.className = 'patch-added';
    }

    // Update in-memory file content so subsequent patches match correctly
    if (this.currentFile) {
        this.currentFile.content = (this.currentFile.content || '').replace(oldStr, newStr);
    }

    // Brief pause to show green before next patch
    await new Promise(r => setTimeout(r, 300));
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): add applyLivePatch() with sequential animation"
```

---

### Task 5: Add `finalizePatchStreaming()` method

**Files:**
- Modify: `app/static/js/artifactPanel.js` — add after `_animatePatch()`

- [ ] **Step 1: Add the method**

```javascript
finalizePatchStreaming(artifact, { sessionId = null, reference = null } = {}) {
    if (!this.isPatchStreaming) return;

    // Wait for animation queue to drain, then switch to final artifact
    this._patchQueue.then(() => {
        this.isPatchStreaming = false;

        // Brief pause to show last green highlight
        setTimeout(() => {
            // Remove all patch marks
            if (this.codeBlock) {
                this.codeBlock.querySelectorAll('.patch-removing, .patch-added').forEach(el => {
                    const parent = el.parentNode;
                    while (el.firstChild) parent.insertBefore(el.firstChild, el);
                    parent.removeChild(el);
                });
            }

            // Open the server-patched artifact (authoritative) on Preview
            if (artifact) {
                this.openArtifact(artifact, { sessionId, reference });
            }
        }, 500);
    });
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): add finalizePatchStreaming() method"
```

---

### Task 6: Add incremental patch parser to codeWindow.js

**Files:**
- Modify: `app/static/js/codeWindow.js` — add helper functions near the top, after the `typewriteTitle` function (around line 19)

- [ ] **Step 1: Add `scanClosingQuote` helper**

After the `typewriteTitle` function (around line 19), add:

```javascript
// Incremental JSON parser helpers for live patch streaming
function scanClosingQuote(str, from) {
    for (let i = from; i < str.length; i++) {
        if (str[i] === '\\') { i++; continue; }
        if (str[i] === '"') return i;
    }
    return -1;
}
```

- [ ] **Step 2: Add `unescapePatchJsonString` helper**

```javascript
function unescapePatchJsonString(str) {
    return str.replace(/\\(n|t|r|"|\\|\/)/g, (_, ch) => {
        switch (ch) {
            case 'n': return '\n';
            case 't': return '\t';
            case 'r': return '\r';
            case '"': return '"';
            case '\\': return '\\';
            case '/': return '/';
            default: return _;
        }
    });
}
```

- [ ] **Step 3: Add `extractCompletePatchesFromBuffer` function**

```javascript
function extractCompletePatchesFromBuffer(buffer, skip) {
    const patches = [];
    let searchFrom = 0;

    while (searchFrom < buffer.length) {
        const fileIdx = buffer.indexOf('"file"', searchFrom);
        if (fileIdx === -1) break;

        const fileMatch = buffer.slice(fileIdx).match(/"file"\s*:\s*"([^"]+)"/);
        if (!fileMatch) break;

        const oldIdx = buffer.indexOf('"old_string"', fileIdx);
        if (oldIdx === -1) break;

        const oldColonQuote = buffer.indexOf('"', oldIdx + '"old_string"'.length + 1);
        if (oldColonQuote === -1) break;
        const oldEnd = scanClosingQuote(buffer, oldColonQuote + 1);
        if (oldEnd === -1) break;

        const newIdx = buffer.indexOf('"new_string"', oldEnd);
        if (newIdx === -1) break;

        const newColonQuote = buffer.indexOf('"', newIdx + '"new_string"'.length + 1);
        if (newColonQuote === -1) break;
        const newEnd = scanClosingQuote(buffer, newColonQuote + 1);
        if (newEnd === -1) break;

        patches.push({
            file: fileMatch[1],
            old_string: unescapePatchJsonString(buffer.slice(oldColonQuote + 1, oldEnd)),
            new_string: unescapePatchJsonString(buffer.slice(newColonQuote + 1, newEnd)),
        });

        searchFrom = newEnd + 1;
    }

    return patches.slice(skip);
}
```

- [ ] **Step 4: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(artifacts): add incremental patch JSON parser"
```

---

### Task 7: Wire up patch streaming detection in codeWindow.js

**Files:**
- Modify: `app/static/js/codeWindow.js:2793` (variable declarations)
- Modify: `app/static/js/codeWindow.js:2855-2858` (content chunk handler, after artifact stream block)

- [ ] **Step 1: Declare patch streaming variables**

After line 2793 (`let streamArtifactPatches = null;`), add:

```javascript
let patchStreamActive = false;
let patchStreamBuffer = '';
let patchParsedPatches = [];
```

- [ ] **Step 2: Add patch fence detection in content chunk handler**

After line 2858 (the closing `}` of `} else if (artifactStreamActive && window.artifactPanel) {` block), add a new `else if` block:

```javascript
                            // --- Live patch streaming detection ---
                            if (!patchStreamActive && !artifactStreamActive) {
                                const patchFenceMatch = streamedContent.match(/```artifact_patch/i);
                                if (patchFenceMatch && window.artifactPanel?.currentArtifact) {
                                    patchStreamActive = true;
                                    patchStreamBuffer = streamedContent.slice(patchFenceMatch.index);
                                    patchParsedPatches = [];
                                    window.artifactPanel.openPatchStreaming();
                                }
                            } else if (patchStreamActive) {
                                patchStreamBuffer += chunk.text || '';
                                const newPatches = extractCompletePatchesFromBuffer(patchStreamBuffer, patchParsedPatches.length);
                                for (const patch of newPatches) {
                                    patchParsedPatches.push(patch);
                                    window.artifactPanel.applyLivePatch(patch.file, patch.old_string, patch.new_string);
                                }
                            }
```

This goes right after the existing artifact streaming `else if` block and before the `} else if (chunk.type === 'done')` block.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(artifacts): detect artifact_patch fence and stream patches live"
```

---

### Task 8: Wire up done handler to finalize patch streaming

**Files:**
- Modify: `app/static/js/codeWindow.js:3113-3146` (artifact panel update section after stream completes)

- [ ] **Step 1: Add patchStreamActive branch before existing artifact handling**

Replace the block at lines 3113-3146 (the artifact panel update section) with:

```javascript
                // Handle artifact panel update
                if (patchStreamActive && streamArtifact && window.artifactPanel) {
                    // Live patch streaming was active — finalize with server result
                    patchStreamActive = false;
                    const artifactRef = streamArtifactRef || {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        summary: displayContent
                    };
                    window.artifactPanel.finalizePatchStreaming(streamArtifact, {
                        sessionId: requestSessionId,
                        reference: artifactRef
                    });
                } else if (streamArtifact && window.artifactPanel) {
                    const artifactRef = streamArtifactRef || {
                        artifactId: streamArtifact.artifactId,
                        title: streamArtifact.title,
                        type: streamArtifact.type,
                        primaryLanguage: streamArtifact.primaryLanguage,
                        summary: displayContent
                    };

                    if (streamArtifactPatches && streamArtifactPatches.patches && window.artifactPanel.applyPatchesVisually) {
                        // Fallback: patches arrived but live streaming didn't activate
                        window.artifactPanel.applyPatchesVisually(
                            streamArtifactPatches.patches,
                            streamArtifactPatches.patch_results,
                            streamArtifact,
                            { sessionId: requestSessionId, reference: artifactRef }
                        );
                    } else if (artifactStreamActive) {
                        // New artifact was streaming — finalize and show preview
                        window.artifactPanel.finalizeStreaming(streamArtifact, {
                            sessionId: requestSessionId,
                            reference: artifactRef
                        });
                    } else {
                        // Non-streaming — just open the artifact
                        window.artifactPanel.openArtifact(streamArtifact, {
                            sessionId: requestSessionId,
                            reference: artifactRef
                        });
                    }
                }
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(artifacts): finalize live patch streaming on done chunk"
```

---

### Task 9: Clean up debug logging

**Files:**
- Modify: `app/static/js/codeWindow.js` — remove `[Title]` and `[Patch]` debug console.logs added during investigation

- [ ] **Step 1: Remove debug logs**

Remove these debug console.log lines that were added during investigation (search for `[Title]` and `[Patch]`):

- Line with `console.log('[Title] Done chunk session_name:'...`
- Line with `console.log('[Title] mgr:'...`
- Line with `console.log('[Title] current:'...`
- Line with `console.log('[Title] tab element found:'...`
- Line with `console.log('[Title] Fallback: renderChatTabs')`
- Line with `console.log('[Patch] streamArtifact:'...`
- Line with `console.log('[Patch] Calling applyPatchesVisually'...`

Also remove from `artifactPanel.js`:
- Line with `console.log('[PatchVisual] applyPatchesVisually called:'...`

- [ ] **Step 2: Commit**

```bash
git add app/static/js/codeWindow.js app/static/js/artifactPanel.js
git commit -m "chore: remove debug logging from patch and title code"
```

---

### Task 10: Restart and manual test

- [ ] **Step 1: Restart the service**

```bash
sudo systemctl restart xelasphere
```

- [ ] **Step 2: Test new artifact creation**

Create a brand new chat, ask "build me a counter app in React". Verify:
- Artifact streams live in the panel (existing behavior preserved)
- Preview renders correctly after finalization

- [ ] **Step 3: Test patch update**

In the same chat, ask "change the background to dark theme". Verify:
- Panel stays on Code tab showing current code
- Title shows "Updating..."
- As the AI streams the patch JSON, the old CSS code highlights red with strikethrough
- After 400ms, it swaps to the new CSS code with green highlight
- After streaming completes, panel switches to Preview with the final result

- [ ] **Step 4: Test fallback (non-streaming endpoint)**

If available, test with a model that uses the non-streaming `/chat` endpoint. Verify `applyPatchesVisually` still works as before.

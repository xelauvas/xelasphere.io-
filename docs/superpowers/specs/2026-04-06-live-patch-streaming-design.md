# Live Patch Streaming — Design Spec

**Date:** 2026-04-06  
**Status:** Approved for implementation  
**Principle:** "Show each replacement live as the AI writes it"

## Problem

When the AI updates an existing artifact via `artifact_patch`, the user sees nothing during streaming. The patch JSON accumulates silently, then the result appears all at once on the `done` chunk. New artifacts stream live — patch updates should too.

## Solution

Detect the `artifact_patch` fence during streaming, open the panel showing the current (pre-patch) code, incrementally parse each patch object from the stream, and animate each old→new replacement live in the code tab.

## Data Flow

```
AI streams content chunks via SSE
        ↓
codeWindow.js processLine() accumulates streamedContent
        ↓
Detect ```artifact_patch fence in streamedContent
        ↓
artifactPanel.openPatchStreaming()
  → load current artifact from panel cache (already open) or fetch from store
  → show on Code tab with title "Updating..."
  → set patchStreamActive = true
        ↓
Subsequent content chunks route to patchStreamBuffer
        ↓
Incremental parser scans buffer for complete {file, old_string, new_string} objects
        ↓
For each complete patch:
  → artifactPanel.applyLivePatch(file, oldStr, newStr)
  → switch to target file if not already shown
  → find oldStr in code block text nodes
  → wrap in <mark class="patch-removing"> (red highlight)
  → after 400ms, replace text content with newStr
  → re-wrap in <mark class="patch-added"> (green highlight)
  → scroll to the change
        ↓
On done chunk:
  → server sends final patched artifact + patch results
  → artifactPanel.finalizePatchStreaming(finalArtifact, opts)
  → remove all highlights
  → open final artifact on Preview tab (server-applied version is authoritative)
```

## Frontend Changes

### codeWindow.js — Streaming detection

**In `processLine()`, inside `chunk.type === 'content'` handler:**

After the existing `artifactStreamActive` / new-artifact detection block, add a parallel block for patch detection:

```javascript
// --- Live patch streaming detection ---
if (!patchStreamActive && !artifactStreamActive && window.artifactPanel) {
    const patchFenceMatch = streamedContent.match(/```artifact_patch/i);
    if (patchFenceMatch) {
        patchStreamActive = true;
        patchStreamBuffer = streamedContent.slice(patchFenceMatch.index);
        patchParsedPatches = [];
        window.artifactPanel.openPatchStreaming();
    }
} else if (patchStreamActive) {
    patchStreamBuffer += chunk.text || '';
    // Try to extract complete patches
    const newPatches = extractCompletePatchesFromBuffer(patchStreamBuffer, patchParsedPatches.length);
    for (const patch of newPatches) {
        patchParsedPatches.push(patch);
        window.artifactPanel.applyLivePatch(patch.file, patch.old_string, patch.new_string);
    }
}
```

**New variables** declared alongside existing stream vars (~line 2790):

```javascript
let patchStreamActive = false;
let patchStreamBuffer = '';
let patchParsedPatches = [];
```

**New function `extractCompletePatchesFromBuffer(buffer, alreadyParsed)`:**

Scans the JSON buffer for complete patch objects. A patch is complete when we can find `"old_string":"<escaped value>"` and `"new_string":"<escaped value>"` with properly terminated quotes. Uses the same escaped-quote scanning technique as `_parseAndDisplayStreamingFiles()`.

Returns only patches at index >= `alreadyParsed` (avoids re-processing).

```javascript
function extractCompletePatchesFromBuffer(buffer, skip) {
    const patches = [];
    let searchFrom = 0;

    // Find each "file":"..." + "old_string":"..." + "new_string":"..." triple
    while (searchFrom < buffer.length) {
        const fileIdx = buffer.indexOf('"file"', searchFrom);
        if (fileIdx === -1) break;

        const fileMatch = buffer.slice(fileIdx).match(/"file"\s*:\s*"([^"]+)"/);
        if (!fileMatch) break;

        const oldIdx = buffer.indexOf('"old_string"', fileIdx);
        if (oldIdx === -1) break;

        const oldStart = buffer.indexOf('"', oldIdx + '"old_string"'.length + 1);
        if (oldStart === -1) break;
        const oldEnd = scanClosingQuote(buffer, oldStart + 1);
        if (oldEnd === -1) break;

        const newIdx = buffer.indexOf('"new_string"', oldEnd);
        if (newIdx === -1) break;

        const newStart = buffer.indexOf('"', newIdx + '"new_string"'.length + 1);
        if (newStart === -1) break;
        const newEnd = scanClosingQuote(buffer, newStart + 1);
        if (newEnd === -1) break;

        patches.push({
            file: fileMatch[1],
            old_string: unescapeJsonString(buffer.slice(oldStart + 1, oldEnd)),
            new_string: unescapeJsonString(buffer.slice(newStart + 1, newEnd)),
        });

        searchFrom = newEnd + 1;
    }

    return patches.slice(skip);
}

function scanClosingQuote(str, from) {
    for (let i = from; i < str.length; i++) {
        if (str[i] === '\\') { i++; continue; }
        if (str[i] === '"') return i;
    }
    return -1; // not yet complete
}

function unescapeJsonString(str) {
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

**On `done` chunk:** If `patchStreamActive`, skip the `applyPatchesVisually` path. Instead call `finalizePatchStreaming`:

```javascript
if (patchStreamActive && streamArtifact && window.artifactPanel) {
    patchStreamActive = false;
    window.artifactPanel.finalizePatchStreaming(streamArtifact, {
        sessionId: requestSessionId,
        reference: artifactRef
    });
} else if (streamArtifactPatches && ...) {
    // existing applyPatchesVisually fallback
}
```

### artifactPanel.js — Three new methods

#### `openPatchStreaming()`

Opens the panel on Code tab showing the current artifact code (before any patches). Uses whatever is currently in `this.currentArtifact` / `this.currentVersion`.

```javascript
openPatchStreaming() {
    if (!this.currentVersion || !this.codeBlock) return;

    this.isPatchStreaming = true;
    this._patchQueue = Promise.resolve(); // sequential animation chain

    // Switch to code tab, show current code
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

#### `applyLivePatch(file, oldStr, newStr)`

Queues an animated replacement. Each patch waits for the previous one to finish (sequential via promise chain).

```javascript
applyLivePatch(file, oldStr, newStr) {
    if (!this.isPatchStreaming) return;

    this._patchQueue = this._patchQueue.then(() => this._animatePatch(file, oldStr, newStr));
}

async _animatePatch(file, oldStr, newStr) {
    // Switch file if needed
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

    // Wait a frame for render
    await new Promise(r => requestAnimationFrame(r));

    // Find old_string in code and highlight red
    const codeText = this.codeBlock.textContent || '';

    // Normalize for matching
    let searchOld = oldStr;
    if (!codeText.includes(searchOld)) {
        searchOld = searchOld.replace(/\\n/g, '\n').replace(/\\t/g, '\t');
    }
    if (!codeText.includes(searchOld)) return; // can't find it

    this._highlightTextInElement(this.codeBlock, searchOld, 'patch-removing');
    const removeMark = this.codeBlock.querySelector('.patch-removing');
    if (removeMark) this._scrollToElement(removeMark);

    // Wait 400ms for user to see the old code highlighted
    await new Promise(r => setTimeout(r, 400));

    // Replace text content with new_string
    if (removeMark) {
        removeMark.textContent = newStr;
        removeMark.className = 'patch-added';
    }

    // Update the in-memory file content so subsequent patches match
    if (this.currentFile) {
        this.currentFile.content = this.currentFile.content.replace(oldStr, newStr);
    }

    // Wait 300ms to show the green before next patch
    await new Promise(r => setTimeout(r, 300));
}
```

Note: `_highlightTextInElement` needs a second parameter for the class name (currently hardcoded to `'patch-highlight'`). Change it to accept an optional `className` parameter.

#### `finalizePatchStreaming(artifact, opts)`

Cleans up and opens the final server-patched artifact.

```javascript
finalizePatchStreaming(artifact, { sessionId = null, reference = null } = {}) {
    if (!this.isPatchStreaming) return;

    // Wait for animation queue to drain
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
            this.openArtifact(artifact, { sessionId, reference });
        }, 500);
    });
}
```

### CSS — Two new highlight classes

```css
.patch-removing {
    background: rgba(255, 70, 70, 0.25);
    border-left: 3px solid #ff4646;
    padding: 2px 4px;
    margin-left: -4px;
    text-decoration: line-through;
    text-decoration-color: rgba(255, 70, 70, 0.6);
    animation: patch-remove-pulse 0.8s ease-in-out;
}

.patch-added {
    background: rgba(56, 229, 77, 0.25);
    border-left: 3px solid #38e54d;
    padding: 2px 4px;
    margin-left: -4px;
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

## Backend Changes

None. The backend already streams content chunks and sends the final patched artifact in the `done` payload. The incremental parsing happens entirely on the frontend.

## Existing code preserved

- `applyPatchesVisually()` stays as fallback for non-streaming responses
- `openStreaming()` / `appendStreamingCode()` / `finalizeStreaming()` untouched (new artifacts)
- Server-side patch application remains authoritative (frontend animation is cosmetic)

## Edge cases

1. **Panel not open** — `openPatchStreaming()` opens it. If no `currentVersion`, fall back to `applyPatchesVisually` on done.
2. **old_string not found in code** — skip that patch silently, server result will be correct anyway.
3. **User switches sessions during streaming** — `patchStreamActive` is scoped to the sendMessage closure; background request manager handles buffering.
4. **All patches fail server-side** — `done` payload has `render_mode: 'inline'`, no artifact. Frontend animations were cosmetic only.
5. **Streaming model not used** — Regular `/chat` endpoint returns final result, `applyPatchesVisually` handles it.

## Files to modify

1. `app/static/js/codeWindow.js` — patch fence detection, buffer, incremental parser, done handler
2. `app/static/js/artifactPanel.js` — `openPatchStreaming()`, `applyLivePatch()`, `finalizePatchStreaming()`, update `_highlightTextInElement` to accept class name
3. `app/static/css/artifact-panel.css` — `.patch-removing`, `.patch-added` classes

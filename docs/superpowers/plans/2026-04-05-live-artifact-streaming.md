# Live Artifact Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the artifact panel as soon as ` ```artifact` is detected in the stream and show code streaming live, then auto-swap to rendered preview when done.

**Architecture:** Frontend-only changes. The content stream handler in `codeWindow.js` watches accumulated text for the artifact fence marker. Once detected, it opens the panel via new `artifactPanel` methods and routes subsequent chunks into a live code view. The existing `done` handler still performs full artifact parsing — the panel swaps from streaming code to rendered preview at that point.

**Tech Stack:** Vanilla JS (existing stack), CSS

---

### Task 1: Add streaming state and DOM elements to ArtifactPanel

**Files:**
- Modify: `app/static/js/artifactPanel.js:730-778` (constructor properties)

- [ ] **Step 1: Add streaming state properties to the constructor**

After line 778 (`this.activeTab = 'preview';`), add:

```javascript
// Live streaming state
this.isStreaming = false;
this.streamingCodeContainer = null;
this.streamingAutoScroll = true;
this.streamingContentExtractor = null; // tracks position in artifact JSON
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): add streaming state properties to ArtifactPanel constructor"
```

---

### Task 2: Implement `openStreaming()` method on ArtifactPanel

**Files:**
- Modify: `app/static/js/artifactPanel.js` (add method after `close()` at ~line 1364)

- [ ] **Step 1: Add `openStreaming()` method**

Insert after the `close()` method (after line 1364):

```javascript
openStreaming() {
    // Reset streaming state
    this.isStreaming = true;
    this.streamingAutoScroll = true;
    this.streamingContentExtractor = { started: false, quoteChar: null, depth: 0 };

    // Set header to generic streaming title
    if (this.titleEl) {
        this.titleEl.textContent = 'Generating...';
    }
    if (this.languageBadge) {
        this.languageBadge.textContent = '';
    }

    // Clear version select
    if (this.versionSelect) {
        this.versionSelect.innerHTML = '';
        this.versionSelect.disabled = true;
    }

    // Create or reuse streaming code container in the preview panel section
    const previewSection = document.getElementById('artifact-preview-panel');
    if (previewSection) {
        // Hide the iframe during streaming
        const iframeWrapper = previewSection.querySelector('.artifact-preview-frame');
        if (iframeWrapper) iframeWrapper.style.display = 'none';

        // Create streaming container if not present
        let container = previewSection.querySelector('.artifact-streaming-code');
        if (!container) {
            container = document.createElement('div');
            container.className = 'artifact-streaming-code';
            const pre = document.createElement('pre');
            const code = document.createElement('code');
            pre.appendChild(code);
            container.appendChild(pre);
            previewSection.appendChild(container);
        } else {
            container.querySelector('code').textContent = '';
        }
        container.style.display = '';
        this.streamingCodeContainer = container.querySelector('code');

        // Bind scroll detection for auto-scroll pause
        container.addEventListener('scroll', () => {
            if (!this.isStreaming) return;
            const pre = container.querySelector('pre');
            if (!pre) return;
            const atBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 30;
            this.streamingAutoScroll = atBottom;
        });
    }

    // Open the panel
    this.panel.classList.add('is-open', 'is-streaming');
    this.panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('artifact-panel-open');
    this.activeTab = 'preview';
    this.switchTab('preview');
    this.loadSavedSplit();
    this.updateOffset(false);
    this.adjustComposerWidth(true);
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): implement openStreaming() method for live code view"
```

---

### Task 3: Implement `appendStreamingCode()` method

**Files:**
- Modify: `app/static/js/artifactPanel.js` (add method after `openStreaming()`)

- [ ] **Step 1: Add `appendStreamingCode()` method**

Insert immediately after `openStreaming()`:

```javascript
appendStreamingCode(text) {
    if (!this.isStreaming || !this.streamingCodeContainer) return;

    // Try to extract just the file content from artifact JSON
    // The stream looks like: {"title":"...","files":[{"path":"...","content":"<ACTUAL CODE>...
    // We want to display from the first "content":" value onward
    const ext = this.streamingContentExtractor;
    if (!ext.started) {
        // Accumulate in a buffer to detect "content":" or "content": "
        if (!ext.buffer) ext.buffer = '';
        ext.buffer += text;
        // Look for first "content" key followed by string value
        const contentMatch = ext.buffer.match(/"content"\s*:\s*"/);
        if (contentMatch) {
            ext.started = true;
            // Extract everything after the match
            const afterMatch = ext.buffer.slice(contentMatch.index + contentMatch[0].length);
            // Unescape JSON string escapes for display
            const displayText = this._unescapeJsonString(afterMatch);
            this.streamingCodeContainer.textContent += displayText;
        }
        // Don't display anything until we find content
        return;
    }

    // We're inside the content string — unescape and display
    const displayText = this._unescapeJsonString(text);
    this.streamingCodeContainer.textContent += displayText;

    // Auto-scroll to bottom
    if (this.streamingAutoScroll) {
        const scrollParent = this.streamingCodeContainer.closest('.artifact-streaming-code');
        if (scrollParent) {
            scrollParent.scrollTop = scrollParent.scrollHeight;
        }
    }
}

_unescapeJsonString(str) {
    // Unescape common JSON string escapes for display
    return str
        .replace(/\\n/g, '\n')
        .replace(/\\t/g, '\t')
        .replace(/\\r/g, '\r')
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, '\\');
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): implement appendStreamingCode() with JSON content extraction"
```

---

### Task 4: Implement `finalizeStreaming()` method

**Files:**
- Modify: `app/static/js/artifactPanel.js` (add method after `appendStreamingCode()`)

- [ ] **Step 1: Add `finalizeStreaming()` method**

Insert immediately after `_unescapeJsonString()`:

```javascript
finalizeStreaming(artifactData, { sessionId = null, reference = null } = {}) {
    this.isStreaming = false;
    this.panel.classList.remove('is-streaming');
    this.streamingContentExtractor = null;

    // Hide streaming container, show iframe
    const previewSection = document.getElementById('artifact-preview-panel');
    if (previewSection) {
        const streamingContainer = previewSection.querySelector('.artifact-streaming-code');
        if (streamingContainer) streamingContainer.style.display = 'none';
        const iframeWrapper = previewSection.querySelector('.artifact-preview-frame');
        if (iframeWrapper) iframeWrapper.style.display = '';
    }

    if (artifactData) {
        // Use the normal openArtifact flow which handles rendering
        this.openArtifact(artifactData, { sessionId, reference });
    } else {
        // Artifact parsing failed — keep the streaming code visible as fallback
        if (previewSection) {
            const streamingContainer = previewSection.querySelector('.artifact-streaming-code');
            if (streamingContainer) streamingContainer.style.display = '';
            const iframeWrapper = previewSection.querySelector('.artifact-preview-frame');
            if (iframeWrapper) iframeWrapper.style.display = 'none';
        }
        if (this.titleEl) {
            this.titleEl.textContent = 'Artifact (raw)';
        }
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(artifacts): implement finalizeStreaming() to swap code view for rendered preview"
```

---

### Task 5: Add CSS for streaming code view

**Files:**
- Modify: `app/static/css/artifact-panel.css` (add at end of file)

- [ ] **Step 1: Add streaming code view styles**

Append to end of `artifact-panel.css`:

```css
/* ==================== */
/* STREAMING CODE VIEW */
/* ==================== */

.artifact-streaming-code {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  overflow-x: hidden;
  background: #1a1a2e;
  padding: 0;
  display: none;
}

.artifact-streaming-code pre {
  margin: 0;
  padding: 1rem 1.25rem;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
  font-size: 0.8125rem;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.85);
  white-space: pre-wrap;
  word-wrap: break-word;
  min-height: 100%;
}

.artifact-streaming-code code {
  font-family: inherit;
  color: inherit;
}

/* Subtle pulsing border-left while streaming */
.artifact-panel.is-streaming .artifact-streaming-code {
  border-left: 2px solid var(--artifact-accent, #7c6fe0);
  animation: streaming-pulse 2s ease-in-out infinite;
}

@keyframes streaming-pulse {
  0%, 100% { border-left-color: var(--artifact-accent, #7c6fe0); }
  50% { border-left-color: rgba(124, 111, 224, 0.3); }
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/artifact-panel.css
git commit -m "feat(artifacts): add CSS for streaming code view in artifact panel"
```

---

### Task 6: Add artifact fence detection to the stream content handler

**Files:**
- Modify: `app/static/js/codeWindow.js:2760-2805` (stream handler variables and content chunk processing)

- [ ] **Step 1: Add streaming detection variables**

After line 2771 (`let streamDreamNotification = null;`), add:

```javascript
let artifactStreamActive = false;
let artifactFencePosition = -1;
```

- [ ] **Step 2: Add fence detection in the content chunk handler**

Replace the content chunk handler (lines 2791-2805):

```javascript
} else if (chunk.type === 'content') {
    // ChatGPT-style: on first content chunk, just enable scrolling
    // Don't remove spacer yet - let it stay to prevent jumps
    if (isFirstContentChunk) {
        isFirstContentChunk = false;
        // Signal that real content has arrived
        window.scrollState?.contentArrived?.();
    }
    streamedContent += chunk.text || '';
    // Buffer in BackgroundRequestManager
    if (bgRequest) {
        BackgroundRequestManager.appendContent(requestSessionId, chunk.text || '');
    }
    // ALWAYS try to update
    ThinkingIndicator.setContentMode();

    // --- Live artifact streaming detection ---
    if (!artifactStreamActive && window.artifactPanel) {
        // Check if accumulated content contains ```artifact fence
        const fenceIdx = streamedContent.search(/```artifact\b/i);
        if (fenceIdx !== -1) {
            artifactStreamActive = true;
            artifactFencePosition = fenceIdx;
            window.artifactPanel.openStreaming();
            // Send the portion after the fence marker to the panel
            const fenceEnd = streamedContent.indexOf('\n', fenceIdx);
            if (fenceEnd !== -1) {
                const afterFence = streamedContent.slice(fenceEnd + 1);
                if (afterFence) {
                    window.artifactPanel.appendStreamingCode(afterFence);
                }
            }
        }
    } else if (artifactStreamActive && window.artifactPanel) {
        // Route new chunks to the artifact panel
        window.artifactPanel.appendStreamingCode(chunk.text || '');
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(artifacts): detect artifact fence during stream and route to panel"
```

---

### Task 7: Wire up `finalizeStreaming()` in the done handler

**Files:**
- Modify: `app/static/js/codeWindow.js:3027-3041` (artifact preview attachment after done)

- [ ] **Step 1: Add finalize call before existing artifact attachment logic**

Find the block starting at line 3027 (`// Attach artifact preview if present`). Replace it with:

```javascript
// Finalize streaming artifact panel if it was active
if (artifactStreamActive && window.artifactPanel) {
    const artifactRef = streamArtifactRef || (streamArtifact ? {
        artifactId: streamArtifact.artifactId,
        title: streamArtifact.title,
        type: streamArtifact.type,
        primaryLanguage: streamArtifact.primaryLanguage,
        summary: displayContent
    } : null);
    window.artifactPanel.finalizeStreaming(streamArtifact, {
        sessionId: requestSessionId,
        reference: artifactRef
    });
}

// Attach artifact preview pill if present (for re-opening later)
console.log('Stream artifact data:', { streamArtifact, streamRenderMode, hasPanel: !!window.artifactPanel });
if (streamArtifact && messageContainer && window.artifactPanel) {
    console.log('Attaching artifact preview:', streamArtifact);
    const artifactRef = streamArtifactRef || {
        artifactId: streamArtifact.artifactId,
        title: streamArtifact.title,
        type: streamArtifact.type,
        primaryLanguage: streamArtifact.primaryLanguage,
        summary: displayContent
    };
    window.artifactPanel.attachPreview(messageContainer, artifactRef, {
        payload: streamArtifact
    });
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat(artifacts): call finalizeStreaming() on done to swap to rendered preview"
```

---

### Task 8: Handle edge case — panel cleanup when no artifact detected

**Files:**
- Modify: `app/static/js/codeWindow.js` (in the done handler, after the finalize block from Task 7)

- [ ] **Step 1: Handle false-open cleanup (edge case: fence detected but parsing fails)**

This is already handled by `finalizeStreaming()` — when `artifactData` is null, it shows the raw code as fallback. No additional code needed. But we do need to handle the case where streaming was active but the panel needs to respect a failed parse.

In the existing error toast block around line 3315-3318 in `codeWindow.js`:

```javascript
// Show toast if artifact parsing failed
if (data.artifact_error && window.Toast) {
    window.Toast.warning('Artifact update failed: ' + data.artifact_error + '. Try asking again.', 6000);
}
```

This already handles the toast. `finalizeStreaming(null)` will show the raw code fallback. No changes needed for this task.

- [ ] **Step 2: Verify the streaming container is cleaned up on panel close**

In `artifactPanel.js`, modify the existing `close()` method (line 1355-1364). Replace it with:

```javascript
close() {
    if (!this.panel) return;

    // Clean up streaming state if active
    if (this.isStreaming) {
        this.isStreaming = false;
        this.streamingContentExtractor = null;
        this.panel.classList.remove('is-streaming');
    }

    // Hide streaming container and restore iframe visibility
    const previewSection = document.getElementById('artifact-preview-panel');
    if (previewSection) {
        const streamingContainer = previewSection.querySelector('.artifact-streaming-code');
        if (streamingContainer) streamingContainer.style.display = 'none';
        const iframeWrapper = previewSection.querySelector('.artifact-preview-frame');
        if (iframeWrapper) iframeWrapper.style.display = '';
    }

    this.panel.classList.remove('is-open');
    this.panel.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('artifact-panel-open', 'chat-area-wide');
    this.updateOffset(false);
    this.adjustComposerWidth(false);
    this.clearActivePills();
    this.activeTab = 'preview';
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/artifactPanel.js app/static/js/codeWindow.js
git commit -m "feat(artifacts): clean up streaming state on panel close and handle parse failures"
```

---

### Task 9: Manual integration test

**Files:** None (testing only)

- [ ] **Step 1: Start the dev server**

```bash
# Use whatever the project's start command is
sudo systemctl restart gunicorn
```

- [ ] **Step 2: Test artifact streaming**

1. Open XelaSphere in a browser
2. Ask the AI to "create an HTML page with a counter button" (or similar artifact-generating prompt)
3. Verify:
   - The artifact panel opens as soon as ` ```artifact` appears in the stream
   - Code streams in line by line in the panel
   - Chat area shows the summary text simultaneously
   - When the stream finishes, the panel swaps from code view to rendered preview
   - The pill appears on the chat message
   - Clicking the pill re-opens the artifact as before

- [ ] **Step 3: Test non-artifact responses**

1. Ask a normal question (e.g., "what is 2+2")
2. Verify the artifact panel does NOT open

- [ ] **Step 4: Test panel close during streaming**

1. Ask for an artifact
2. While it's streaming, click the close button
3. Verify the panel closes cleanly and doesn't reopen

- [ ] **Step 5: Commit any fixes if needed**

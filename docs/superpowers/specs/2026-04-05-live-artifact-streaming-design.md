# Live Artifact Streaming Design

**Date:** 2026-04-05
**Status:** Draft
**Branch:** feat/dream-system

## Problem

When the AI generates an artifact (HTML page, React app, etc.), the user sees nothing in the artifact panel until the entire response finishes streaming. The panel only opens after the `done` chunk arrives with the fully-parsed artifact payload. This makes the experience feel sluggish — the user stares at chat text with no visual feedback that an artifact is being built.

## Goal

Open the artifact panel as soon as artifact content is detected in the stream, and show the code streaming in live. When the stream completes, automatically swap from the code view to the rendered preview. Both the chat summary and the artifact panel update simultaneously.

## Design Decisions

| Question | Decision |
|----------|----------|
| When does the panel open? | When ` ```artifact` fence is detected in the stream |
| What shows during streaming? | Code streaming line by line in the panel |
| What shows in chat during streaming? | Summary text streams normally (everything outside the artifact block) |
| Visual indicator while streaming? | Just the code appearing — no extra spinners or labels |
| What happens when done? | Auto-switch from code view to rendered preview |
| False open handling? | Panel only opens on fence detection, not optimistically |
| Backend changes? | None — all frontend |

## Architecture: Hybrid Frontend Detection + Backend Confirmation

The backend streaming remains unchanged. The frontend detects the artifact fence in real-time and opens the panel optimistically. The backend's `done` handler still performs full artifact parsing and sends the final payload, which the frontend uses to swap from code view to rendered preview.

### Stream Splitting — Frontend Artifact Fence Detection

As content chunks arrive in `codeWindow.js`, the accumulated `streamedContent` string is watched for the ` ```artifact` marker.

**Detection logic:**
- After each `content` chunk appends to `streamedContent`, check if it contains ` ```artifact` (case-insensitive)
- Once detected, set a flag `artifactStreamActive = true` and record the position where the artifact block starts
- From that point, all subsequent content chunks are dual-routed: appended to `streamedContent` as normal (so the `done` handler still works), and pushed into the artifact panel's live code view
- Chat display continues rendering only the text before the artifact fence

**Edge cases:**
- The ` ```artifact` marker might arrive split across two chunks. Solution: check the tail of accumulated content after each append, not just the new chunk.
- If the stream ends without a closing ` ``` `, the `done` handler's existing unclosed-fence fallback handles it.

### Panel Opening & Live Code View

Once the ` ```artifact` fence is detected:

- Call `window.artifactPanel.openStreaming()` — opens the panel in code-streaming mode
- Panel header shows "Generating..." (title unavailable until JSON is fully parsed)
- As content chunks arrive, append raw text into the code view
- Chat area skips rendering the artifact block portion, only showing summary text before it

**When `done` arrives:**
- Backend sends fully parsed `artifact_data` as today
- Panel calls `finalizeStreaming(artifactData, reference)` — swaps from code view to rendered preview (HTML, React, Mermaid, etc.)
- Panel header updates with real title, language badge, version number
- Pill attaches to chat message as today (for re-opening later)

**If artifact parsing fails on `done`:**
- Panel stays showing the raw code
- Toast warning appears as today

### Code Display During Streaming

**Content filtering:**
- Raw stream after ` ```artifact` starts with JSON structure containing title, files, etc.
- Heuristic: once `"content":` followed by a quote is seen, display from there. Everything before is JSON metadata — skip it visually.
- If heuristic fails (complex multi-file artifact), fall back to showing raw text.

**Syntax highlighting:**
- Apply lightweight highlighting during streaming
- Batch re-highlighting (every 500ms or every 20 chunks) to avoid jank

**Scrolling:**
- Auto-scroll to bottom as new code appears
- If user manually scrolls up, pause auto-scroll
- Resume auto-scroll when user scrolls back to bottom

## Files Changed

### `app/static/js/codeWindow.js`
- In `processLine` handler for `content` chunks (~line 2791-2803): add fence detection on `streamedContent` after each append
- When fence detected: call `window.artifactPanel.openStreaming()` and start routing content
- Split display: filter artifact block text out of chat bubble rendering
- On `done`: pass final artifact data to panel, triggering swap from code view to rendered preview

### `app/static/js/artifactPanel.js`
New methods:
- `openStreaming()` — opens panel in code-streaming mode with minimal chrome
- `appendStreamingCode(text)` — appends text to live code view, handles auto-scroll
- `finalizeStreaming(artifactData, reference)` — swaps from code view to rendered preview using existing renderer logic

Existing `openArtifact()` stays untouched — still used for re-opening saved artifacts from pills.

### `app/static/css/artifact-panel.css`
- Styles for streaming code view state (`.artifact-panel.is-streaming`)
- Code container styling within the panel

### No backend changes
`chat_routes.py` and `artifacts.py` remain as-is.

# Artifact Patch Mode Design

**Date:** 2026-04-05
**Status:** Draft
**Branch:** feat/dream-system

## Problem

When a user asks to fix or update an existing artifact, the AI regenerates the **entire** artifact — all files, all code — even if only one line changed. This wastes tokens, takes longer to stream, and produces a worse UX (the user sees the full code again instead of just the fix).

## Goal

Introduce an `artifact_patch` format that allows the AI to output surgical `old_string` → `new_string` replacements instead of full code regeneration. Inspired by Claude Code's Edit tool pattern.

## Design Decisions

| Question | Decision |
|----------|----------|
| Patch format | New `` ```artifact_patch`` fence with JSON `old_string`/`new_string` pairs |
| When to use patches? | All artifact updates — never regenerate full code for updates |
| What shows in chat? | Brief text summary of what changed (no code in bubble) |
| What if a patch fails? | Apply patches that succeed, skip failures, show warning toast |
| Streaming behavior | No streaming code view for patches — they're small. Apply on `done`. |
| Error auto-injection | Inject last render error into prompt context for fix requests |

## The `artifact_patch` Format

The AI outputs a `` ```artifact_patch`` fenced block:

```json
{
  "patches": [
    {
      "file": "App.jsx",
      "old_string": "the exact text to find in the file",
      "new_string": "the replacement text"
    },
    {
      "file": "style.css",
      "old_string": ".header { color: red; }",
      "new_string": ".header { color: blue; }"
    }
  ]
}
```

Rules:
- `old_string` must be an exact match in the target file (include 2-4 surrounding lines for uniqueness)
- `new_string` is the replacement — can be empty string for deletions
- Multiple patches can target the same file — applied sequentially
- The AI uses this format for **all** artifact updates — never regenerates full code
- The chat text before the fence is a brief summary of what changed

## System Prompt Changes

**File:** `app/services/chat_engine.py`

The existing system prompt tells the AI to "Output the COMPLETE updated artifact." Change this for update scenarios:

When `detect_artifact_update_intent()` returns True and current artifact code is injected:
- Replace the "output complete artifact" instruction with the `artifact_patch` format instructions
- Tell the AI to use `old_string`/`new_string` pairs
- Include 2-4 surrounding lines in `old_string` for uniqueness
- Multiple patches per file allowed
- Empty `new_string` for deletions
- NEVER regenerate the full artifact for updates

The `detect_artifact_update_intent()` function already determines update vs create. The same flag controls which instructions the AI receives.

## Backend: Parsing — `app/artifacts.py`

### New function: `extract_artifact_patch_block(text)`

Alongside existing `extract_artifact_block()`:
- Looks for `` ```artifact_patch`` fence (case-insensitive)
- Parses JSON, validates structure
- Must have `patches` array
- Each patch must have `file`, `old_string`, `new_string`
- `old_string` cannot be empty
- Returns structured patch data or None

### Routing changes in `ArtifactRouter.route()`

1. First check for `` ```artifact_patch`` fence (new)
2. If found, return render mode: `artifact_patch`
3. If not found, fall back to existing `` ```artifact`` extraction (unchanged)

## Backend: Patch Application — `app/artifacts.py`

### New function: `apply_patches(current_files, patches)`

```
Input:
  current_files: [{"path": "App.jsx", "content": "...", "language": "jsx"}, ...]
  patches: [{"file": "App.jsx", "old_string": "...", "new_string": "..."}, ...]

Output:
  patched_files: updated file list with patches applied
  results: [{"file": "App.jsx", "status": "applied"}, ...]
```

Per-patch logic:
1. Find file by `patch.file` matching `file.path`
2. If file not found → `failed`, "file not found"
3. If `old_string` not in content → `failed`, "old_string not found"
4. If `old_string` matches multiple times → `failed`, "ambiguous match"
5. If exactly one match → `content.replace(old_string, new_string)`, `applied`

Patches for same file applied sequentially — each sees result of previous.

### Persistence

No new persistence logic. After patches are applied:
1. Build a complete artifact payload from patched files
2. Use existing `_append_version()` to create new version
3. Return normal artifact structure to frontend

## Backend: Chat Routes Integration

**Files:** `app/blueprints/chat_routes.py`

In both streaming (`chat_stream`) and non-streaming (`chat`) endpoints:

When `render_mode` is `artifact_patch`:
1. Load current artifact files from the active artifact in the session
2. Call `apply_patches(current_files, patch_data.patches)`
3. Build artifact payload from patched files (same structure as `extract_artifact_block` returns)
4. Persist as new version via `_append_version()`
5. Return artifact payload to frontend (same as today)
6. Include `patch_results` in response for frontend to show warnings

## Frontend Changes

**Chat bubble:** No changes. Summary text renders normally. The `` ```artifact_patch`` fence is stripped (same as `` ```artifact``).

**Artifact panel:** No changes. Backend returns normal artifact payload after applying patches.

**Pill:** Attaches as normal with new version reference.

**Failed patch warnings:** When `patch_results` contains failures, show toast: "1 of 3 patches failed: old_string not found in style.css"

**Error auto-injection:** Enhance `load_current_artifact_context()` to also include the last render error message when available. Store the last error in the artifact panel state and pass it through the composer or chat prompt context.

**Streaming:** No streaming code view for patches. Patches are small — let chat stream normally and apply on `done`.

## Files Changed

### `app/artifacts.py`
- New: `extract_artifact_patch_block(text)` — parse `` ```artifact_patch`` fence
- New: `apply_patches(current_files, patches)` — apply old_string/new_string replacements
- New: `ARTIFACT_PATCH_FENCE_PATTERN` regex
- Modify: `ArtifactRouter.route()` — check for patch fence before artifact fence

### `app/blueprints/chat_routes.py`
- Modify: streaming `done` handler — handle `artifact_patch` render mode
- Modify: non-streaming response handler — handle `artifact_patch` render mode
- Both: load current files, apply patches, persist as new version

### `app/services/chat_engine.py`
- Modify: system prompt — add `artifact_patch` format instructions for update scenarios
- Modify: `load_current_artifact_context()` — include last render error when available

### `app/static/js/codeWindow.js`
- Modify: streaming fence detection — detect `` ```artifact_patch`` fence and skip streaming code view (patches are small)
- Modify: non-streaming response — handle `patch_results` warnings

### `app/static/js/artifactPanel.js`
- Modify: store last render error for context injection
- Minor: show toast for failed patches

### No new files needed.

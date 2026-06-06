# Reviewable Inline Patch Diff — Design Spec

Date: 2026-06-06
Status: Approved (brainstorming) — pending implementation plan
Area: Artifact panel (`app/static/js/artifactPanel.js`), live patch flow (`app/static/js/codeWindow.js`)

## Overview

When the AI patches an artifact (`ACTION: patch_artifact`), the Code tab should show a
**real, reviewable unified diff** of what changed — removed lines in red, added lines in
green, in place — and keep it visible until the user dismisses it. This replaces the
current behavior, which highlights only the *added* text for ~3 seconds and then auto-fades
and switches to the preview, never showing what was removed and never letting the user
review at their own pace.

This addresses standing feedback: *patches must show live replacements in the Code tab,
not apply silently.*

## Goals

- Show a unified (single-column, git-style) line diff per changed file in the Code tab.
- Removed lines red, added lines green, surrounding context plain.
- Diff stays until the user acts — no timed auto-switch to preview.
- Two actions: **Done** (dismiss) and **Revert** (restore the previous version).
- Robust to multi-file patches and to the patch→full-regenerate fallback.

## Non-Goals (YAGNI)

- Side-by-side diff layout.
- Diffs for manual (user) edits in the code editor.
- A standalone "compare any two versions" tool (tracked separately).
- Word/character-level intra-line diffing (line-level is sufficient).

## Current State (as built)

- Live patch: `codeWindow.js` receives an `artifact_patches` stream chunk and calls
  `window.artifactPanel.applyPatchesVisually(patches, patchResults, finalArtifact, …)`.
- `applyPatchesVisually` (artifactPanel.js): opens the patched artifact on the Code tab,
  highlights `patch.new_string` occurrences (additions only) via `_highlightTextInElement`,
  scrolls to the first change, then after 3s fades the highlights and switches to the
  preview tab.
- A separate version-diff viewer exists in the DOM (`#artifact-diff-from/-to/-viewer`) but
  is out of scope here.
- The patched version is **already persisted server-side** as a new version by the time the
  frontend visualizes it. This feature is visualization + revert, not an apply-gate.

## Design

### Data flow

1. `codeWindow.js` calls a new `enterPatchDiffMode(patches, prevFiles, newArtifact)`.
   `applyPatchesVisually` is **kept as a thin alias** that captures `prevFiles` and forwards
   to `enterPatchDiffMode`, so the existing call site (`codeWindow.js:3565`) keeps working
   with a one-line change; the old additions-only highlight + 3s auto-switch body is deleted.
2. `prevFiles` = the file contents of the version currently shown **before** the panel swaps
   to `newArtifact` — captured from `this.currentVersion?.files`. This gives the "old" side
   of the diff. The "new" side is `newArtifact`'s active version files.
3. Changed files = files whose content differs between `prevFiles` and the new files (derived
   by content comparison, not by `patch.file`, so whole-file regenerations diff correctly).

### Components (isolated, independently testable)

- `computeUnifiedDiff(oldText, newText) -> hunks[]`
  Pure function. Line-level LCS/Myers diff. Returns an ordered list of rows:
  `{ type: 'context' | 'add' | 'del', text, oldNo, newNo }`, grouped with a few lines of
  context around changes (collapse large unchanged gaps with a "⋯ N unchanged lines" row).
  ~60 lines, no external dependency.
- `renderDiffView(container, file, hunks)`
  Renders the unified diff DOM into the code area: a `+`/`-`/` ` gutter, old/new line
  numbers, and red/green row backgrounds. Monospace; syntax highlighting not required for v1.
- `enterPatchDiffMode(patches, prevFiles, newArtifact)`
  Orchestration: open `newArtifact` on the Code tab, compute diffs for changed files, mark
  changed files in the file dropdown, render the banner, render the diff for the
  first/selected changed file, wire Done/Revert. No auto-switch timer.
- `exitPatchDiffMode()`
  Restores the normal Code view (current version) and clears diff DOM + banner.
- `revertToPreviousVersion(prevFiles)`
  Restores the previous version's files as a **new** version (history preserved) by POSTing
  them to the existing `edit_artifact` endpoint, then re-renders.

### UX

- **Banner** above the diff: `✦ N changes · M file{s}`. If the change was a regenerate (not a
  clean patch), append `· regenerated`. Buttons: **Done**, **Revert**.
- **Multi-file:** the existing file dropdown gains a "changed" dot on affected files; the
  banner shows the file count; selecting a changed file shows that file's diff; unchanged
  files render as normal code.
- **Done:** `exitPatchDiffMode()` — shows the new current code normally; the user switches to
  Preview when ready (no automatic switch).
- **Revert:** confirm-free; calls `revertToPreviousVersion(prevFiles)`; on success re-renders
  to that restored version and exits diff mode. A toast confirms ("Reverted to previous
  version").

### Error / edge handling

- Some patches failed (`patchResults`) → the content diff still shows what actually changed;
  banner notes `· regenerated`.
- `prevFiles` missing (first version / not available) → skip diff, render the file normally
  (no banner). Never throw.
- Binary or unchanged file selected → render normally, no diff rows.
- `edit_artifact` revert request fails → toast error, stay in diff mode, leave the new
  version active.

## Testing

- **Unit** (`computeUnifiedDiff`): add-only, remove-only, modify, multi-hunk, no-change,
  whole-file replacement. Assert row types and line numbers.
- **Headless render test** (same harness style used during the preview rework): feed
  `prevFiles` + `newArtifact`, assert the diff DOM contains red `del` rows and green `add`
  rows, the banner with the correct counts, and Done/Revert buttons; assert Revert issues a
  POST of the previous files.

## Files Touched

- `app/static/js/artifactPanel.js` — new diff functions, banner, dropdown markers, revert;
  remove the timed auto-switch.
- `app/static/js/codeWindow.js` — call `enterPatchDiffMode` (capture `prevFiles` first).
- `app/static/css/artifact-panel.css` (or inline) — diff row / banner styles.
- `app/templates/index.html` — bump `artifactPanel.js` cache version.
- No backend change (reuses `edit_artifact` for revert).

## Rollout

Build + verify headless, deploy to `/opt/xelasphere` with a cache-version bump and graceful
reload, sync to the dev repo, then confirm on a real logged-in patch.

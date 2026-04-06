# Incremental Patching UX — Reference Architecture

**Date:** 2026-04-06
**Status:** Reference spec for implementation
**Source:** Detailed analysis of artifact patching systems

## Current State (XelaSphere)

**What works (Level 1-2):**
- Backend patch application (extract, validate, apply, persist)
- Version management (append version, version history)
- Frontend receives patch data in done payload
- Artifact opens with patched code

**What's missing (Level 3-4):**
- Changed-region highlighting
- Scroll anchoring
- Patch summary display
- Smooth transition from code to preview

## Implementation Layers

### Layer 1: Model Output (DONE)
- `artifact_patch` fence format
- `old_string`/`new_string` pairs
- System prompt instructs AI to use patches

### Layer 2: Patch Planner/Normalizer (DONE)
- `extract_artifact_patch_block()` — parse and validate
- `apply_patches()` — apply with exact + fuzzy matching
- Fallback to full replace if all patches fail

### Layer 3: State Manager (DONE)
- Current artifact version in `artifactPanel.currentVersion`
- Version cache (`versionCache`)
- Patch results tracking

### Layer 4: Renderer (PARTIALLY DONE)
- `openArtifact()` re-renders everything
- Need: render only changed regions
- Need: preserve scroll position

### Layer 5: UX Feedback Layer (TODO)
This is the core work remaining.

## MVP Implementation Plan (Priority Order)

### 1. Changed-region highlighting
- After `openArtifact` renders the patched code in the code tab
- Find each `new_string` in the rendered code
- Wrap in `<mark class="patch-highlight">` with a subtle pulse
- Key challenge: `openArtifact` switches to Preview tab; need to show Code tab first

### 2. Scroll anchoring
- Use `_scrollToElement` (already built) to scroll to first change
- Must scroll within code section only, not the panel

### 3. Patch summary
- Title shows "Updated (3 changes)" — already built
- Could add toast with file-level summary

### 4. Auto-transition to preview
- After 2-3s on Code tab with highlights, switch to Preview
- Preview should already be rendered (openArtifact handles this)

## Key Architectural Insight

The problem with `applyPatchesVisually` is that `openArtifact()` does too much:
- Sets artifact state
- Renders preview
- Renders code
- Switches to Preview tab
- Fetches version history

For patching, we need to:
1. Set artifact state (need this)
2. Render preview in background (need this)
3. Render code (need this)
4. Switch to CODE tab first (not Preview)
5. Add highlights to code
6. Then switch to Preview after delay

**Solution:** Don't call `openArtifact`. Instead, manually set state + render,
but control the tab switching ourselves. OR: add an option to `openArtifact`
like `{ initialTab: 'code' }` that overrides the default Preview switch.

## CSS for Highlights

```css
.patch-highlight {
  background: rgba(124, 111, 224, 0.2);
  border-left: 2px solid var(--artifact-accent, #7c6fe0);
  padding: 1px 0;
  animation: patch-pulse 1.5s ease-in-out;
}

@keyframes patch-pulse {
  0% { background: rgba(124, 111, 224, 0.4); }
  50% { background: rgba(124, 111, 224, 0.15); }
  100% { background: rgba(124, 111, 224, 0.2); }
}
```

## Principle
"Make change visible, not theatrical."
- Animate only: changed area, status transition, layout displacement
- Don't animate: entire panel, full preview crossfade, constant indicators

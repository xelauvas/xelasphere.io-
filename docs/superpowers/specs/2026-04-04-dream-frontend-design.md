# Dream System Frontend UI — Design Spec

**Date:** 2026-04-04
**Scope:** Memory activity log + dream toggle inside existing settings memory modal

## Overview

Add two frontend pieces to complete the dream consolidation feature:
1. A dream on/off toggle in the memory management modal
2. A dream activity log tab showing consolidation history with undo

## Placement

Inside the existing memory management modal (Settings > Data Controls > Memory tile). No new pages or routes.

## 1. Dream Settings Toggle

- Position: Top of memory modal, above the memory list/tabs
- UI: Single toggle switch labeled "Memory Consolidation"
- Subtitle: "Automatically organize, merge duplicates, and remove stale memories"
- Persists `dream_enabled` (boolean) to Firestore user preferences via existing user prefs API
- Default: enabled (matches `DreamConfig.dream_enabled = True`)

## 2. Memory Modal Tab Toggle

Add a pill-style tab toggle at the top of the modal body:
- **Memories** (default active) — existing memory list
- **Activity** — new dream activity log

Follows the same pill-toggle pattern used in the artifact panel (`artifact-panel__tab-toggle`).

## 3. Dream Activity Log

### Data Source

`GET /user/memory-activity` returns last 20 dream logs with:
- `id`, `started_at`, `completed_at`
- `phase1_actions` and `phase2_actions` arrays (each with `action_type`, `memory_id`, `reason`)
- `summary` dict: `{pruned, merged, superseded, promoted, synthesized, total_actions}`
- `undone` boolean

### Display Format

Chronological list, newest first. Each entry:

```
[timestamp — e.g. "Apr 4, 2026 — 3:12 AM"]
  Merged 2 duplicate memories
  Pruned 1 expired memory
  New insight: "Prefers iterative design"
  [Undo button]
```

Action type mapping:
- `prune` → "Pruned N memories"
- `merge` → "Merged N duplicate memories"
- `supersede` → "Resolved N conflicts"
- `promote` → "Promoted N memories"
- `synthesize` → "New insight: [reason]"

### States

- **Loading:** Spinner centered in tab panel
- **Empty:** "No consolidation activity yet" centered text
- **Error:** "Failed to load activity" with retry link
- **Undone entry:** Greyed out with "Undone" badge, no undo button

### Undo Flow

1. Click "Undo" button on an entry
2. Button shows spinner, disables
3. POST `/user/memory-activity/{log_id}/undo`
4. On success: show success toast, mark entry as undone in UI
5. On error: show error toast, re-enable button
6. Undo button hidden if entry is older than 7 days or already undone

## 4. Implementation Details

### Files Modified

- `app/templates/settings_api_keys.html` — add toggle, tab UI, activity log HTML, inline JS
- No new CSS files (use existing patterns from `api-keys.css`)
- No new JS files (inline in template, matching existing modal JS pattern)

### API Calls

- `GET /user/memory-activity` — fetch activity log (on Activity tab click)
- `POST /user/memory-activity/{log_id}/undo` — undo a consolidation
- User prefs update for `dream_enabled` toggle — use existing user preferences persistence pattern from the settings page

### Styling

Match existing settings modal patterns:
- Background: `rgba(20, 20, 28, 0.95)` cards
- Text: `#f5f6fb` primary, `#b4b6c4` secondary
- Borders: `rgba(255, 255, 255, 0.1)`
- Buttons: ghost style for undo, same rounded/padding as existing
- Toggle: match existing toggle pattern in the settings page
- Tab pills: 2-up pill toggle, active state with subtle highlight

## 5. Edge Cases

- User with no dream logs: empty state
- User disables dream: toggle persists, no consolidations run, activity log still viewable
- Undo after 7 days: button not shown (enforced frontend + backend)
- Concurrent undo: backend returns error, frontend shows toast
- Large action counts: summary line per action type, not per individual action

# Spheres — session grouping (v1)

**Date:** 2026-05-22
**Status:** Approved design, pending spec review then implementation plan
**Working name:** Spheres (can repaint; decoupled from the build)

## Summary

A flat, one-level grouping layer above chat sessions. Each session optionally belongs to one Sphere. Spheres are manual-only in v1 — the user creates them, names them, picks an emoji and color, and drags sessions in. Entering a Sphere filters the sidebar and shows a context bar at the top of the chat area. Grouped sessions are hidden from the top-level time buckets so the sidebar stays clean.

This is *visual grouping only*. A Sphere does not carry a shared system prompt, shared files, or shared memory. Each session inside a Sphere keeps its own independent context. That keeps v1 shippable in a small number of milestones.

## Why

The user has many topical clusters of chats (e.g. multiple Facebook-post chats all for their hotel — Sunday roast, Father's Day, Mother's Day, takeaway). The recency-based sidebar (Pinned / Today / Yesterday / Older) makes these scatter. A user-controlled grouping layer lets them collapse a topic into a single sidebar row and enter it on demand.

The bar set during brainstorming was "worthy of ChatGPT" — meaning the experience should feel premium, not utilitarian. Dedicated context bar, smooth transitions, inline create/edit, drag-and-drop, no modal ceremony.

## Decisions locked in brainstorm

| Question | Decision |
|---|---|
| Group behavior | Visual grouping only — no shared system prompt / files / memory |
| Grouping source | Manual only in v1; AI suggestions deferred to v2 |
| Click behavior | Hybrid — sidebar filters to the Sphere AND a context bar appears at the top of the chat area |
| Top-level visibility of grouped sessions | Hidden from Today/Yesterday/Older, with a quiet `+ N more in spheres` hint |
| Nesting | None — flat, one level |
| Naming/metaphor | "Spheres" as working name (matches XelaSphere brand); decoupled from the build |

## Data model

One new Firestore collection per user, plus one field added to existing sessions.

### New: `users/{uid}/spheres/{sphereId}`

| Field | Type | Notes |
|---|---|---|
| `name` | string | e.g. "The Hotel" |
| `emoji` | string | Single emoji, e.g. "🏨" |
| `color` | string | Hex accent for the sidebar dot and context bar tint |
| `position` | int | Manual ordering of Sphere rows in the sidebar (drag to reorder) |
| `created_at` | timestamp | Server timestamp |
| `updated_at` | timestamp | Server timestamp |

### Added to existing `users/{uid}/sessions/{sessionId}`

| Field | Type | Notes |
|---|---|---|
| `sphere_id` | string \| null | `null` = ungrouped (default). Non-null = belongs to that Sphere. |

### Why this shape

- Single-field join (`sphere_id`) means existing session queries keep working. The top-level sidebar query adds `where('sphere_id', '==', null)`; entering a Sphere uses `where('sphere_id', '==', X)`.
- No migration needed — absent field is treated as `null`.
- `position` lives on the Sphere, not on the session, so reordering Spheres doesn't rewrite N session docs.

## Surfaces

### Sidebar — top level (not inside a Sphere)

```
[ + New chat ]   🔍 Search

▾ SPHERES                          [+]
  ● 🏨 The Hotel              · 12
  ● 🎂 Birthdays              ·  4
  ● 📸 Content ideas          ·  7

▾ PINNED
  ★ Site redesign notes

▾ TODAY
  Greek restaurant ideas
  CV rewrite

▾ YESTERDAY
  Weather check

▾ PREVIOUS 7 DAYS
  Tax allowance question

⌄ + 23 more in spheres
```

- The `●` is the Sphere's accent color dot.
- Grouped sessions do NOT appear in Today/Yesterday/Older.
- The `+ N more in spheres` hint at the bottom is shown as a quiet text row. Whether it is clickable (opening a flat "All grouped chats" view) is deferred to the implementation plan — see Open questions.

### Sidebar — inside a Sphere

```
🏨 The Hotel                  [Exit]

▾ PINNED IN SPHERE
  ★ Brand guidelines

▾ TODAY
  Sunday roast post
  Father's day post

▾ YESTERDAY
  Takeaway menu copy

▾ PREVIOUS 7 DAYS
  Mother's day post
  Easter post recap
```

- Same time-bucketing logic as today, filtered to the current Sphere.
- A new "Pinned in sphere" bucket appears so pinned chats inside a Sphere don't get lost. The global Pinned bucket at the top level only shows ungrouped pinned chats.
- The global `[+ New chat]` and `Search` controls are replaced by the Sphere header and Exit button while inside a Sphere.

### Context bar (chat area, only when inside a Sphere)

```
🏨  The Hotel    [+ New chat in this sphere]              [Exit sphere]
```

- Sits at the top of the chat area.
- Subtle background tint (~6–8% opacity) in the Sphere's accent color along the top edge.
- The `[+ New chat in this sphere]` button creates a session with `sphere_id` pre-set to this Sphere.
- The global `[+ New chat]` in the sidebar (still present) always creates an ungrouped session — predictable "fresh start" button.

### Drag-and-drop visuals

- Dragging a session: Sphere rows pulse in their accent color as drop targets.
- Drop: target row scales down briefly; the dragged session animates into it.
- Dragging a Sphere row: reorders within the Spheres list. Only Sphere rows are valid drop targets when dragging another Sphere.
- Inside a Sphere, a "Remove from sphere" drop zone appears at the top of the sidebar while dragging a session — drop sets `sphere_id = null`.

## Flows

### Create a Sphere

1. Click `[+]` next to the Spheres header.
2. A new row slides in: `[😀▾] [name input…]`.
3. Emoji picker is a compact popover (recent + search).
4. Type name → Enter to save. Escape to cancel.
5. Optional color chip in the row picks the accent (8 presets). Default is auto-derived from the emoji.

No modal at any point.

### Edit a Sphere

- Hover a Sphere row → `⋯` menu reveals: Rename, Change emoji, Change color.
- Each action edits inline in the row. Escape cancels, Enter commits.

### Delete a Sphere

- `⋯` → Delete → confirm dialog asks one question:
  - *What about the 12 chats inside?*
    - **Keep them** (move back to ungrouped — they reappear in Today/Yesterday by `last_updated`) — default
    - **Delete them too** (destructive, separate confirm)

### Add a session to a Sphere

- Drag the session row onto the Sphere row in the sidebar.
- Or: session `⋯` → "Move to…" → Sphere picker.

### Remove a session from a Sphere

- Inside the Sphere, drag the session onto the "Remove from sphere" drop zone that appears while dragging.
- Or: session `⋯` → "Remove from sphere" (sets `sphere_id = null`).

### Move a session between Spheres

- Same as add — drag onto the target Sphere, or `⋯` → "Move to…" → pick new Sphere.

### Start a new chat inside a Sphere

- Click `[+ New chat in this sphere]` in the context bar. Session is created with `sphere_id` already set.

### Reorder Spheres

- Drag a Sphere row up/down in the Spheres section. `position` field is rewritten on drop.

## Edge cases

- **Max Spheres per user:** 50 (soft). Returns a friendly "You've hit 50 spheres — try renaming or deleting one" toast.
- **Max sessions per Sphere:** no hard cap. The filtered sidebar uses the same rendering path as the time-bucket view, so list-length performance behaves identically to today.
- **Renaming to duplicate name:** allowed. Spheres are identified by ID. Sidebar shows duplicates side by side.
- **Unread badges:** existing per-session unread system continues. A Sphere row shows the sum of its children's unread counts as a small dot/number. Clicking the Sphere does not auto-mark-read.
- **Search:** existing search continues to work across all sessions including ones in Spheres. Hits inside a Sphere display a small Sphere chip next to the result.
- **Pinned sessions inside a Sphere:** shown in "Pinned in sphere" bucket when inside the Sphere. The global Pinned bucket at the top level only shows ungrouped pinned chats. This avoids one chat appearing in two buckets.
- **Mobile:** Spheres section behaves the same; context bar sticks under the page header.
- **Session ordering inside a Sphere:** sort by `last_updated` desc (same logic as time buckets). Manual ordering within a Sphere deferred to v2.

## Out of scope for v1

These are explicitly excluded; each can become its own brainstorm later:

- Nested Spheres (no sub-Spheres).
- Shared system prompt / shared files / shared memory inside a Sphere.
- AI-driven auto-grouping or suggestions.
- Sharing a Sphere with another user.
- Sphere-level analytics ("how many chats this month").
- Manual ordering of sessions inside a Sphere.

## Forward compatibility for v2 (AI suggestions)

Schema is forward-compatible. When v2 ships, suggestions live in a separate collection (`users/{uid}/sphere_suggestions/{id}`) with a `status` field (`pending`, `accepted`, `dismissed`). No migration of Spheres or sessions needed. The v1 build does not need to anticipate v2 in code — just don't take design decisions that paint us into a corner (e.g. don't make `sphere_id` an array; keep one-Sphere-per-session in v1).

## Backend touchpoints

(Concrete routes/files to be enumerated in the implementation plan.)

- `app/blueprints/session_routes.py` — sidebar query, session PATCH (to set/clear `sphere_id`), bulk-delete (cascades unchanged).
- New `app/blueprints/sphere_routes.py` — CRUD for spheres (`POST`, `GET`, `PATCH`, `DELETE`), reorder endpoint.
- Firestore security rules — same per-user scoping as `sessions`.
- Existing categorize/list logic in `session_routes.py` extended to support `sphere_id` filter parameter.

## Frontend touchpoints

(Concrete files to be enumerated in the implementation plan.)

- Sidebar component — new Spheres section above Pinned bucket, conditional rendering when inside a Sphere, drag-and-drop wiring.
- New context bar component for the chat area.
- New Sphere create/edit row component (inline, no modal).
- Routing — entering a Sphere updates URL (e.g. `/?sphere=<id>`) so it's bookmarkable and survives refresh.
- Existing session list virtualization confirmed to handle the new filtered queries.

## Open questions for the implementation plan

- Exact endpoint shape for the reorder operation (single PATCH per sphere vs. one bulk endpoint).
- Whether the `+ N more in spheres` hint is clickable in v1 or v2.
- Whether the global search results need any UI treatment beyond the small Sphere chip (e.g. "show results inside this sphere only" toggle).
- Animation timing/easing values for the drop animations (design call during implementation).

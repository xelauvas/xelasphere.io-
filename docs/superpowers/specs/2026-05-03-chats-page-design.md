# Chats Page — Full-feature browser at `/chats`

**Status:** approved (architecture + backend); frontend filled with judgment defaults
**Date:** 2026-05-03
**Reference:** mirrors what `claude.ai/chats` does — full-page chat list with search, filters, sort, star/pin, bulk operations.

---

## Goal

Build a dedicated `/chats` page where users can browse, search, filter, sort, star, and bulk-manage every chat session they've created. Replaces the implicit "scroll the sidebar" UX with a first-class browser.

## User-facing summary

- Sidebar `Chats` button → navigates to `/chats`
- Page shows full chat list (title, last activity, model, message count, indicators for has-artifact / has-attachment)
- Search box filters live as user types
- Filter chips: starred-only, model-used dropdown, has-artifact, has-attachment, date range
- Sort: Recent / Oldest / A-Z / Most messages / Starred-first
- Star icon per row (click to toggle)
- Hover-reveal checkbox per row → bulk toolbar appears once anything selected → Delete · Star · (mobile: long-press to enter select mode)
- Click row title → opens that chat in the existing main view (`/c/<session_id>`)
- Mobile: same page, responsive — narrow widths drop the model and message-count columns; row tap = open chat; long-press = enter select mode

---

## Architecture

### Routing

- `GET /chats` — added to existing `pages_bp`. Renders new `chats.html` template.
- Sidebar's existing `#action-chats` button (already in `index.html`) wires to `window.location.href = '/chats'`.
- The current sidebar's chat list (Today / Yesterday / Last 7 Days / Older) stays untouched as a quick-access strip on every page.

### Data flow

1. On page load, frontend calls `GET /chat/list-metadata` once.
2. Server returns flat array of session metadata for the current user. No message bodies. Approx 50KB for 500 chats.
3. Frontend caches in `sessionStorage` keyed by `user_id` and renders.
4. All filter / search / sort happens client-side against the cached array — instant feedback.
5. Mutations (star, pin, rename, delete, bulk-delete) hit the server; on success, frontend updates the cache + re-renders.
6. On revisit within the same browser session, page renders instantly from cache, then silently refreshes in the background.

### File additions

- `app/templates/chats.html` — page template
- `app/static/css/chats.css` — page styles
- `app/static/js/chatsPage.js` — frontend module (state, fetch, filter/sort/render, mutations)
- `app/blueprints/pages_routes.py` — add the `/chats` route
- `app/blueprints/session_routes.py` — add 3 new endpoints (see Backend section)

---

## Backend changes

### Firestore schema additions (additive, no migration)

Each session document gets these fields. Missing field reads as falsy.

| Field | Type | Default | Source |
|---|---|---|---|
| `starred` | bool | false | toggled via `PATCH /chat/<session_id>` |
| `pinned` | bool | false | toggled via `PATCH /chat/<session_id>` |
| `tags` | array<string> | [] | edited via `PATCH /chat/<session_id>` |
| `model` | string | "" | written by chat completion path (verify in impl — likely already stored) |
| `msg_count` | int | 0 | incremented when a message is saved to the session |
| `has_artifact` | bool | false | flipped true on first artifact creation in the session |
| `has_attachment` | bool | false | flipped true on first attachment upload |

**Backfill:** lazy. The list endpoint detects missing denormalized fields (`msg_count`, `has_artifact`, `has_attachment`) on read and writes them back computed-from-source. Older sessions self-heal as they are listed.

### New endpoints (`session_bp`)

```
GET  /chat/list-metadata
PATCH /chat/<session_id>
POST /chat/bulk-delete
```

**`GET /chat/list-metadata`**
- Returns `{ sessions: [{ session_id, title, last_updated, model, msg_count, starred, pinned, has_artifact, has_attachment, tags }, ...] }`
- No pagination — flat array
- Streams from Firestore `where(user_id) order_by(last_updated desc)`
- Cache-Control: `no-store` (always fresh)

**`PATCH /chat/<session_id>`**
- Body: any subset of `{starred, pinned, title, tags}`. Single-field updates allowed.
- Returns `204 No Content` on success.
- Title rename also goes through this (existing `/chat/rename` kept for backwards compat).

**`POST /chat/bulk-delete`**
- Body: `{session_ids: [...]}`
- Single Firestore batched delete (atomic).
- Returns `{deleted: <count>}`.

### Reused endpoints (no change)

- `POST /chat/select` → click-to-open (existing JS path)
- `POST /chat/delete` → single delete (kept for sidebar)
- `GET /chat/session/<id>` → fetch one
- `GET /chat/unread-counts` → unread badges
- `POST /chat/session/<id>/mark-read` → unread sync

---

## Frontend (page structure)

### DOM layout

```
.chats-page
  .chats-page__header
    .chats-page__title         "Chats"
    .chats-page__search        <input type="search">
  .chats-page__toolbar
    .chats-page__filters       chips: Starred · Model ▼ · Has artifact · Has attachment · Date ▼
    .chats-page__sort          dropdown: Recent ▼
  .chats-page__bulk-toolbar    (hidden until selection)
    "N selected" · Delete · Star · Cancel
  .chats-page__list
    .chats-row[data-session-id]
      .chats-row__check        (hidden, visible on hover or in select-mode)
      .chats-row__star         click toggles starred
      .chats-row__title        click opens chat
      .chats-row__meta         model · msg_count · indicators
      .chats-row__time         "2h ago" / "Mar 12"
      .chats-row__actions      (hover-reveal: rename, delete kebab)
  .chats-page__empty           "No chats yet" / "No matches"
```

### State (in-memory, browser)

```js
state = {
  sessions: [...],          // full metadata array from server
  search: "",               // search query
  filters: {
    starred: false,
    model: null,            // null = all
    hasArtifact: false,
    hasAttachment: false,
    dateRange: null,        // { from, to } or null
  },
  sort: "recent",           // recent | oldest | alpha | msg_count | starred_first
  selected: Set<session_id>,
  loading: false,
  error: null,
}
```

### Interactions

| User action | Behavior |
|---|---|
| Type in search | filter array client-side, re-render |
| Toggle filter chip | filter array client-side, re-render |
| Change sort | sort array client-side, re-render |
| Click row title | navigate to `/c/<session_id>` |
| Click star icon | optimistic update + `PATCH /chat/<session_id>` |
| Hover row | reveal checkbox + actions kebab |
| Check checkbox | add to `selected` set; show bulk toolbar if any selected |
| Long-press row (touch) | enter select-mode (checkboxes always visible) |
| Bulk delete | confirm modal → `POST /chat/bulk-delete` → remove from cache + re-render |
| Bulk star | for each selected: `PATCH /chat/<session_id>` (parallel) |
| Rename (kebab) | inline editable title input, blur or Enter → `PATCH /chat/<session_id>` |
| Delete (kebab) | confirm modal → existing `POST /chat/delete` |

### Optimistic updates

All mutations apply to the client cache immediately, then call the server. On error, the cache is reverted and a toast shows the failure.

---

## Mobile adaptation

- Same page, responsive (no separate mobile template)
- Below 768px:
  - Drop the model column and message count column from rows; keep title + time + indicators
  - Filters collapse into a single "Filter" button → opens a bottom-sheet modal
  - Bulk toolbar slides up from bottom of viewport
  - Long-press a row to enter select-mode (no hover on touch); tap-and-hold for ~500ms
  - Star icon stays visible on touch (no hover-reveal)
- Below 480px:
  - Search bar takes full width
  - Title row uses 2 lines if needed (no truncation)

---

## Empty states

- **No chats at all (new user):** centered illustration + "Start a new chat" button → home
- **No matches for search/filter:** "No chats match your filters" + "Clear filters" link
- **Loading initial data:** skeleton rows (10) for ~200ms, then real data

---

## Error handling

- Initial load fails → page shows retry button + helpful message; sidebar still works
- Mutation fails → optimistic UI reverts + toast: "Couldn't [action]. Try again."
- Network offline → all mutations queue locally and retry when back online (Phase 2 — not blocking ship)
- Stale cache after another tab modifies → SSE push or 30s background refresh keeps cache fresh

---

## Testing strategy

- **Unit:** filter/sort logic in `chatsPage.js` (pure functions)
- **Integration:** new endpoints have route tests covering happy path + auth + 404
- **Manual smoke:** load page, search, filter, sort, star, bulk-delete, mobile responsive
- **No E2E required** for first ship — the routes are CRUD-shaped and well-isolated

---

## Out of scope (not built in this iteration)

- Folders / Projects (separate planned feature, has its own sidebar placeholder)
- Tags-as-filters UI (schema includes `tags` for forward-compat, but the chip dropdown isn't wired in v1)
- Export to ZIP / download (`Export` removed from bulk toolbar — Phase 2)
- Cross-device offline mutation queue (Phase 2)
- Real-time SSE push for cache-coherence across tabs (Phase 2; 30s background poll is enough for v1)
- Bulk rename (one chat at a time)

---

## Acceptance criteria

1. `/chats` route renders the page in <500ms with chat data populated
2. Search updates results in <50ms (client-side, no network)
3. Filter / sort changes update results in <50ms
4. Star/pin/rename mutations succeed and persist across page reload
5. Bulk-delete of 50 sessions completes in <2s and removes them from the list
6. Mobile (375px wide) is fully usable: search, filter, select, delete
7. Sidebar `Chats` button reliably navigates to `/chats`

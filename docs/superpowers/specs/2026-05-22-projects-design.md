# Projects — ChatGPT-equivalent feature for XelaSphere

**Date:** 2026-05-22
**Status:** Approved scope, pending review and implementation plan
**Supersedes:** `2026-05-22-spheres-session-grouping-design.md` (Spheres was visual-only folders; Projects is the full ChatGPT clone)

## Summary

A faithful port of ChatGPT's Projects feature to XelaSphere. A Project is a named, color-coded container that bundles:

1. **Custom instructions** — a per-Project system prompt that every chat inside the Project automatically uses.
2. **Files** — uploaded documents (PDFs, docs, images, text) whose extracted text is injected into the system message for every chat in the Project.
3. **Chats** — chat sessions that belong to the Project. Chats inside a Project are filed away from the global Today/Yesterday view.

The existing **"Projects · Coming soon"** placeholder button in the sidebar (`app/templates/index.html:489`) and the **"Add to project"** menu item (`app/templates/index.html:1490`) get wired up by this work — they were always intended for this feature.

## Why

The user runs multiple ongoing topical streams of work (e.g. "The Hotel" — Sunday roast posts, Father's Day, takeaway menu copy) where every chat shares the same context: brand voice, audience, previous decisions. Without Projects, that context has to be re-typed at the start of every new chat. Projects let the user set the context once (instructions + files) and reuse it across an arbitrary number of chats.

The bar is **ChatGPT-equivalent functionality and look**. Not a re-invention.

## Scope decisions

| Decision | Choice |
|---|---|
| Feature parity target | ChatGPT Projects |
| Per-Project custom instructions | Yes (system prompt for every chat in the Project) |
| Per-Project file uploads | Yes (extracted text injected into system message) |
| File retrieval (RAG) | No in v1 — concatenation with token budget; RAG deferred to v1.1 if needed |
| Dedicated Project landing page | Yes |
| Sidebar Projects section | Yes (above the chat list, replacing the existing "Coming soon" button when populated) |
| Create flow | Modal — exact ChatGPT copy (see § Create modal) |
| Move chat in/out of a Project | Yes — wires up the existing "Add to project" menu item |
| Color | Palette of 10 (matching ChatGPT's range) |
| Limits | 20 files / Project, 10 MB / file, no cap on Projects or chats per Project |

## Data model

### New collection: `users/{uid}/projects/{projectId}`

| Field | Type | Notes |
|---|---|---|
| `name` | string | e.g. "Copenhagen Trip" |
| `color` | string | Hex from the 10-color palette (see § Colors) |
| `instructions` | string | The custom instructions / system prompt. Plain text, up to 8 000 chars. |
| `files` | array of objects | See file schema below. Inline (denormalized) for cheap reads — files don't change often. |
| `position` | int | Manual ordering of Projects in the sidebar (drag to reorder, v1.1) |
| `created_at` | timestamp | Server timestamp |
| `updated_at` | timestamp | Server timestamp |

### File metadata (inline on the Project doc, `files` array)

| Field | Type | Notes |
|---|---|---|
| `file_id` | string | UUID assigned at upload time |
| `filename` | string | Original filename, sanitized |
| `mime_type` | string | e.g. `application/pdf` |
| `size_bytes` | int | Stored size on disk |
| `storage_path` | string | Relative to `UPLOAD_FOLDER`; reuses existing upload pipeline |
| `text_char_count` | int | Length of extracted text (for budget calculations without re-reading) |
| `uploaded_at` | timestamp | When the file was added to the Project |

### File text storage (subcollection)

Extracted text lives in a separate subcollection to keep Project docs comfortably under Firestore's 1 MiB doc limit (20 files × 200 K chars = up to 4 MB of text alone):

**`users/{uid}/projects/{projectId}/files/{fileId}`** — one doc per file:

| Field | Type | Notes |
|---|---|---|
| `extracted_text` | string | Output of `TextExtractorUtility.extract_text()`, truncated at **200 000 chars** (storage cap) |

Read lazily when building the system prompt for a chat in the Project.

### Added to existing `users/{uid}/chat_sessions/{sessionId}`

| Field | Type | Notes |
|---|---|---|
| `project_id` | string \| null | `null` = ungrouped (default). Non-null = belongs to that Project. |

### Why this shape

- **Files split across two locations** — small metadata stays inline so the sidebar / project page can render a file list with a single read; the heavyweight extracted text lives in the per-file subcollection so we never hit the Firestore doc cap and don't pay to fetch text when we only need metadata.
- **Single foreign-key `project_id` on session** — existing session queries keep working. Top-level sidebar adds `where('project_id', '==', null)`; inside a Project uses `where('project_id', '==', X)`.
- **No migration needed** for existing sessions (absent = `null` = unchanged behavior).

## Surfaces

### Sidebar — Projects section

```
[ + New chat ]   🔍 Search

▾ PROJECTS                         [+]
  ▢ The Hotel
  ▢ Copenhagen Trip
  ▢ CV revamp

▾ PINNED
  ★ Site redesign notes

▾ TODAY
  Greek restaurant ideas
  CV rewrite

▾ YESTERDAY
  Weather check

▾ PREVIOUS 7 DAYS
  Tax allowance question

⌄ + 8 more in projects
```

- `▢` is a small colored folder icon (Lucide `FolderClosed`, same SVG already used for the placeholder button at `index.html:489-495`). Color comes from the Project's `color` field.
- `[+]` next to the Projects header opens the **Create project modal** (see below).
- Click a row → navigates to the Project landing page.
- Hover a row → `⋯` menu appears: Rename, Change color, Delete.
- Chats with `project_id` set are hidden from Today/Yesterday/Older. The quiet `+ N more in projects` hint sits at the bottom.

The existing `#action-projects` placeholder button (with "Coming soon" tooltip) is **removed**. The Projects section *is* the new control.

### Create project modal

Exact replica of ChatGPT's modal copy and shape:

```
┌──────────────────────────────────────────────┐
│  Create project                              │
│                                              │
│  Project name                                │
│  ┌────────────────────────────────────────┐  │
│  │ Copenhagen Trip            (placeholder)│  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Projects keep chats, files, and custom      │
│  instructions in one place. Use them for     │
│  ongoing work, or just to keep things tidy.  │
│                                              │
│              [Cancel]   [Create project]     │
└──────────────────────────────────────────────┘
```

- Single text input.
- Helper paragraph below the input (exact copy from ChatGPT, locked above).
- `Create project` is disabled until the name input has at least one non-whitespace character.
- On Create → POST creates the Project, modal closes, the user is taken to the new Project's landing page.
- Color is auto-assigned (cycled through the palette by `position` % 10). User can change it later from the Project page.
- Escape / clicking outside cancels.

### Project landing page

A full page (modal-style overlay or a routed page — see § Routing) with three regions:

```
┌─────────────────────────────────────────────────────────────────┐
│  ▢ Copenhagen Trip                          [⋯]                 │
│                                                                 │
│  [ + New chat in this project ]                                 │
│                                                                 │
│  ┌──────────── Instructions ──────────────┐                     │
│  │ How should ChatGPT respond in this     │                     │
│  │ project?                               │                     │
│  │ (textarea, up to 8000 chars)           │                     │
│  └────────────────────────────────────────┘                     │
│                                                                 │
│  ┌──────────── Files ─────────────────────┐                     │
│  │ [+ Add files]                          │                     │
│  │                                        │                     │
│  │  📄 itinerary.pdf       1.2 MB   [×]   │                     │
│  │  📄 hotel-bookings.pdf  340 KB   [×]   │                     │
│  │  📷 map.png             280 KB   [×]   │                     │
│  └────────────────────────────────────────┘                     │
│                                                                 │
│  ┌──────────── Chats ─────────────────────┐                     │
│  │ ⌘ Itinerary brainstorm    2h ago        │                    │
│  │ ⌘ Restaurant recommendations  yesterday │                    │
│  │ ⌘ Packing list  3 days ago              │                    │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

- **Title row:** folder icon (Project color), name (editable inline on click), `⋯` menu (Rename, Change color, Delete).
- **+ New chat in this project:** prominent CTA. Creates a session with `project_id` already set and navigates to it.
- **Instructions:** text area, autosaves on blur or 1.5s debounce. Character count shown in the corner. Empty is fine (no system prompt added).
- **Files:** drop zone + file picker. List shows filename, size, remove-X. Click on a file row to preview (uses existing `/uploads/<path>` route).
- **Chats:** list scoped to this Project, sorted by `last_updated` desc. Click a chat → opens it (still inside the Project context).

### Chat behavior inside a Project

When the user is in a chat whose session has `project_id` set:

1. **System prompt injection:** before the chat's first model call, the system message is built as:
   ```
   <user's existing global system prompt, if any>

   --- Project instructions ---
   <project.instructions>

   --- Project knowledge ---
   <file 1 filename>:
   <file 1 extracted_text — truncated per § Token budget>

   <file 2 filename>:
   <file 2 extracted_text>
   ...
   ```
   Done in the same place as the existing system-prompt assembly in `app/blueprints/chat_routes.py` (around line 1208, the `system_prompt=base_system` call site).

2. **Visible "in: <Project>" tag** at the top of the chat area, similar to ChatGPT. Clicking it navigates back to the Project landing page.

3. **Chat row in the sidebar** (when not inside the Project) is hidden — it appears only in the Project's chat list.

### "Add to project" menu item (sidebar `⋯` on a chat row)

Wires up `#action-add-to-project` at `app/templates/index.html:1490`. Click it on a chat row → submenu lists all Projects → click one → session gets `project_id = <that>` via the existing PATCH route (allowlist is extended in this work). The chat disappears from the top-level list and appears in the chosen Project.

A second item, **"Remove from project"**, appears in the menu only when the chat already has a `project_id`. Clicking sets `project_id = null`.

## Flows

### Create a Project

1. Click `[+]` in the Projects section header.
2. **Create project** modal opens (exact copy above).
3. User types a name → clicks **Create project**.
4. POST `/projects` → returns the new Project doc.
5. Modal closes, sidebar refreshes, user is navigated to the Project landing page.

### Add files to a Project

1. On the Project page, click **Add files** (or drag onto the Files region).
2. The standard file picker / drop handler from the existing upload pipeline runs.
3. POST `/projects/<id>/files` per file. Server: saves to disk via existing upload helper, calls `TextExtractorUtility.extract_text()`, writes the file doc to `users/{uid}/projects/{id}/files/{fileId}` with `extracted_text`, appends metadata to the Project doc's `files` array.
4. UI shows the file in the list with extraction status (✓ Ready or ⚠ Could not extract).

### Edit instructions

1. User edits the text area on the Project page.
2. After 1.5s of inactivity OR on blur → PATCH `/projects/<id>` with `{instructions: "..."}`.
3. Inline "Saved" toast for 1.2s.

### Start a chat in a Project

1. Click **+ New chat in this project** on the Project page.
2. POST `/chat/new` (or the existing new-chat handler) with `project_id` in the body.
3. New session is created with `project_id` set.
4. First model call assembles the system prompt with Project context (see § Chat behavior).

### Add an existing chat to a Project

1. Hover a chat row in the sidebar → click `⋯` → **Add to project** → submenu lists Projects → click target.
2. PATCH `/chat/<id>` with `{project_id: "<target>"}`. (Existing route — see § Backend changes.)
3. Sidebar re-renders; chat moves to the Project.

### Remove a chat from a Project

1. In the Project's chat list (or the chat's own `⋯` menu) → **Remove from project**.
2. PATCH `/chat/<id>` with `{project_id: null}`.
3. Chat returns to the top-level Today/Yesterday view.

### Delete a Project

1. Project page `⋯` → **Delete project** → confirm dialog asks what to do with the contents:
   - **Keep chats** (move to top-level by clearing `project_id`) — default.
   - **Delete chats** (cascade — destructive, separate confirm).
2. Files attached to the Project are deleted from disk and Firestore in both cases (a file only existed because of the Project).
3. After delete → user returns to the main chat view.

## Colors

10-color palette matching the visual range ChatGPT uses (warm to cool, plus neutral):

| # | Hex | Suggested association |
|---|---|---|
| 1 | `#d97706` | Amber |
| 2 | `#dc2626` | Red |
| 3 | `#db2777` | Pink |
| 4 | `#7c3aed` | Purple |
| 5 | `#2563eb` | Blue |
| 6 | `#0891b2` | Teal |
| 7 | `#16a34a` | Green |
| 8 | `#ca8a04` | Yellow |
| 9 | `#475569` | Slate |
| 10 | `#737373` | Stone |

Default at create time: cycle through by `position % 10`. User can change from the Project page `⋯` menu (color picker popover).

## Routing

The Project landing page is reachable at `/projects/<id>`. Server returns the standard index template with a flag indicating the active Project; the frontend renders the landing page instead of the chat view. Deep links work, refresh works.

Inside a Project chat, the URL is `/?session=<id>&project=<projectId>` (or similar — match existing chat URL pattern with an added query param).

## Limits

Mirror ChatGPT's:

- **Files per Project:** 20.
- **Per-file size:** 10 MB.
- **Per-file extracted-text storage cap:** 200 000 chars (~50 K tokens). Longer extractions are truncated at write time with a trailing "[truncated]" note.
- **Per-chat injection budget** (the total extracted text concatenated into the system prompt): 80 000 chars (~20 K tokens). When the sum of all files exceeds this, files are included whole oldest-first until the budget is hit; the next file is partially included, the rest are dropped, and the system message ends with a "Some project files were truncated to fit in context" line.
- **Instructions:** 8 000 chars.
- **Project name:** 120 chars.
- **Projects per user:** no hard cap.

## Backend changes

(Concrete files to be enumerated in the implementation plan.)

- **New:** `app/blueprints/project_routes.py` — CRUD for Projects (`POST /projects`, `GET /projects`, `GET /projects/<id>`, `PATCH /projects/<id>`, `DELETE /projects/<id>`), file routes (`POST /projects/<id>/files`, `DELETE /projects/<id>/files/<fileId>`).
- **New:** `app/project_service.py` — data-layer module (mirrors `chat_session.py` pattern). Handles the metadata + files-subcollection split.
- **Modified:** `app/chat_session.py:17` — add `project_id` to `ALLOWED_PATCH_FIELDS` so the existing PATCH route works for Add/Remove from Project.
- **Modified:** `app/chat_session.py:get_history()` — accept `project_id` and `ungrouped` filter args.
- **Modified:** `app/blueprints/session_routes.py:450` — accept `?project_id=...` and `?ungrouped=1` query params; return `grouped_count` for the "+ N more in projects" hint.
- **Modified:** `app/blueprints/chat_routes.py` (around line 1208 / 3159, the system-prompt assembly sites) — when the session has `project_id`, fetch the Project's instructions + files and prepend them to the system prompt.
- **Modified:** `firestore.rules` — add owner-only rules for `users/{uid}/projects/**`.
- **Reused:** the existing upload pipeline in `app/blueprints/upload_routes.py` and `TextExtractorUtility` for file storage + text extraction.

## Frontend changes

(Concrete files in the implementation plan.)

- **New:** `app/static/js/projectsModule.js` — Project state, sidebar render, create modal, drag-drop, Add-to-project menu wiring.
- **New:** `app/static/js/projectsPage.js` — Project landing page logic (instructions editor, files list, chats list, color picker).
- **New:** `app/static/css/projects.css` — sidebar section, modal, landing page, "in: project" tag, file rows.
- **New:** `app/templates/projects_page.html` — the Project landing page template (rendered by `GET /projects/<id>`).
- **Modified:** `app/templates/index.html` — add Projects section markup above the chat list; replace the `#action-projects` "Coming soon" button; mount the create modal; wire the existing `#action-add-to-project` menu item.
- **Modified:** `app/static/js/startNewChatModule.js` — query `/chat/history` with `ungrouped=1` at top level (or `project_id=X` when inside a Project), hide chats with `project_id`, render the "+ N more in projects" hint.

## Out of scope for v1

These are explicitly excluded — each can become its own brainstorm later:

- **Proper retrieval (RAG)** over Project files. v1 concatenates text into the system message. If users hit the token budget often, v1.1 adds embeddings + top-K retrieval.
- **Sharing a Project** with another user.
- **Per-Project default model / parameter overrides** (temperature, model selection per Project).
- **Project templates** ("Trip planner template").
- **Project archiving** (separate from delete).
- **Per-Project usage analytics.**
- **Sphere reorder via drag in v1** — drag-reorder of Projects in the sidebar can come in v1.1.

## Naming & copy

- Feature is called **Projects** throughout the UI. No "Sphere" terminology.
- Create modal helper copy is the literal ChatGPT string:
  > "Projects keep chats, files, and custom instructions in one place. Use them for ongoing work, or just to keep things tidy."
- Empty Projects section in the sidebar: no upsell, no placeholder text. Just the header and the `[+]` button.
- Empty Files / Chats list on the Project page: a short hint like "No files yet — drop a PDF or click Add files." and "No chats yet — start one above."

## Open questions for the implementation plan

- **System-prompt assembly point** — there are two sites in `chat_routes.py` (line 1208 and 3159). Confirm both paths inject Project context, or unify into a single helper.
- **Token budget enforcement** — measure char counts only in v1 (cheap, no tokenizer). If users hit the 80 K cap often, switch to real tokenization in v1.1.
- **File preview behavior** in the Files list — open in new tab via existing `/uploads/<path>` route is probably sufficient.
- **Concurrent edits** to instructions from two tabs — last-write-wins is acceptable for v1 (no operational-transform).
- **Mobile** — the landing page needs a single-column responsive layout. Confirm with the existing responsive-foundation patterns.

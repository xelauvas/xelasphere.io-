# Mempalace trust contract — Week 1 design

**Date:** 2026-04-29
**Status:** Design — pending plan + implementation
**Decomposition:** Week 1 of a 3-4 week effort to bring XelaSphere's memory UX to ChatGPT parity. Weeks 2 (polish — inline notifications, search, ON/OFF toggle) and 3-4 (categorization, confidence + provenance display) are deferred and will get their own spec→plan→implementation cycles.

## Why

Mempalace entered P3 ACTIVE on 2026-04-29 12:19 UTC. Retrieval works end-to-end (Riley persona dogfood: 5/5 recall correct), but two trust-contract gaps surfaced during the same dogfood:

1. **The "delete chats" UI implies more than it does.** Riley deleted his chat sessions expecting "the AI forgets me" — but mempalace + memory_v2 + memory_v3 + outbox + smart_suggestions all persisted. The user has no UI handle on the AI's actual long-term recall. (See `project_mempalace_p0_window.md` § "P3 entry day".)
2. **The existing "Incognito" mode is a Potemkin village.** Frontend ghost button + visual treatment + `createNewChat({temporary: true})` are wired. Backend `start_new_chat` reads `temporary=True` and writes `is_temporary=True` to the Firestore session doc. But no downstream writes are skipped: mempalace turns get indexed, memory_v2 extracts facts, the session doc itself persists. The greeting "Incognito mode. No history saved." is currently false.

ChatGPT's product handles both via clearly-separated UI surfaces:

- **Settings → Personalization → Memory** — list of facts the AI knows, per-fact delete, master toggle, "Reference saved memories" toggle, "Reference chat history" toggle, "Clear ChatGPT's memory" button.
- **Temporary chat** — toggle next to "New chat" in the sidebar; dashed border; banner ("This chat won't appear in history or update memory"); writes truly skipped end-to-end.
- **Delete chat** — documented as deletes-conversation-not-memory; copy is explicit.

This design brings XelaSphere to that bar.

## Scope

### In scope (Week 1, 5 PRs)

1. **Delete-chat copy fix** — clarify scope of the existing delete confirmation dialog.
2. **Memory panel "Conversation memory" tab** — extends the existing `app/templates/settings_api_keys.html` memory modal to surface mempalace turns alongside memory_v2 facts.
3. **Per-mempalace-item delete** — new sidecar endpoint + Flask wrapper + per-row delete in the new tab.
4. **Forget Me** — broaden the existing "Clear all" button to wipe the full memory layer (mempalace + memory_v2 + memory_v3 + outbox + smart_suggestions); chat sessions kept.
5. **Wire incognito to actually be temporary** — fix the existing `temporary: true` flag so it skips all downstream writes (mempalace, memory_v2, Firestore session doc).

### Out of scope (deferred)

- Inline "Memory updated" notifications (Week 2)
- Memory search inside the panel (Week 2)
- Memory ON/OFF master toggle separately from per-system toggles (Week 2 — overlaps with toggle plumbing here)
- Categorized memory display, confidence display, provenance display (Weeks 3-4)
- Full memory edit (not just delete) — Tier 3 future
- "Erase all my data" account-deletion flow (future)

## Design decisions

The five foundational design choices, locked via brainstorming Q&A on 2026-04-29:

| Decision | Choice |
|---|---|
| User-facing memory model | Two clearly-separated halves in the panel: "Memories" (memory_v2 facts) + "Conversation memory" (mempalace turns). Honest about the architecture; survives memory_v2's eventual P4 removal cleanly. |
| Unit of "memory" in the conversation memory section | All turns with role labels (`[user]` / `[assistant]`), individually deletable. Lets users see and clean up hallucinated assistant turns (the "Sarah Johnson editing your documentary" type from Riley's dogfood). |
| Forget Me scope | Memory layer only — mempalace + memory_v2 + memory_v3 + outbox + smart_suggestions. Chat sessions kept. Two scopes, two buttons. |
| Temporary chat semantics | True ephemeral. No chat history, no memory writes of any kind. Matches ChatGPT exactly. |
| Delete-chat copy fix | Pure text edit — scope clarification, no checkbox. Keeps the two-scope principle clean. |

## Architecture

### File map (what changes, where)

```
app/blueprints/memory_routes.py          +5 endpoints
  GET    /user/conversation-memory          (list mempalace turns, paginated)
  DELETE /user/conversation-memory/<id>     (single turn)
  GET    /user/memory-toggles               (read user prefs)
  POST   /user/memory-toggles               (write user prefs)
  DELETE /user/forget-me                    (cascade wipe — memory only)

app/blueprints/session_routes.py         ~5 lines: copy edit on
                                         delete-chat confirmation dialog

app/blueprints/chat_routes.py            ~30 lines: temp-mode session
                                         handling — mirror guest_mode
                                         pattern, gate mempalace + v2 +
                                         Firestore writes on
                                         is_temp_session

mempalace-sidecar/.../routes/palace.py   +1 endpoint
  DELETE /palace/{user_id}/drawers/{drawer_id}

mempalace-sidecar/.../palace_handle.py   +1 method
  delete_drawer(drawer_id) — chroma + turns.sqlite + audit log

app/templates/settings_api_keys.html     extend memory modal:
                                         - 3 toggle rows (master,
                                           ref-v2, ref-mempalace)
                                         - new "Conversation memory" tab
                                         - rename "Clear all" → "Forget Me"
                                         - update confirmation copy

app/static/js/memoryPanel.js (or         new tab fetcher + per-item
within settings_api_keys.html JS)         delete + toggle handlers

app/static/css/...                       optional polish for new tab
                                         rows + role badges
```

No new top-level Python modules required. No schema migrations.

### Component overview

**Memory modal (extended, in `settings_api_keys.html`):**

```
┌─────────────────────────────────────────────────────────┐
│  🧠 Memory                                              │
│  Things the AI remembers about you to personalize       │
│  responses.                                             │
│                                                         │
│  [●] Memory                                             │  ← master toggle
│  [●] Reference saved memories       (memory_v2)         │
│  [●] Reference chat history          (mempalace)        │
│                                                         │
│  ─────────────────────────────────────────              │
│  [ Memories ] [ Conversation memory ] [ Activity ]      │  ← tabs
│  ─────────────────────────────────────────              │
│  ...content per tab...                                  │
│                                                         │
│  ───                                                    │
│  N facts · M turns           [Refresh]  [Forget Me]    │
└─────────────────────────────────────────────────────────┘
```

Tabs:
- **Memories** (existing) — memory_v2 facts grouped by type, per-item delete + pin.
- **Conversation memory** (NEW) — paginated list of mempalace turns with `[user]` / `[assistant]` role badges, truncated text, timestamp, per-row delete.
  - Filter chips: "All turns" (default) · "User only" — for cleaner audit when wanted.
  - Search deferred to Week 2.
- **Activity** (existing) — dream consolidation log.

**Forget Me confirmation modal:**

```
┌──────────────────────────────────────┐
│  ⚠ Forget Me?                        │
│                                      │
│  Will be deleted:                    │
│   • N memory facts                   │
│   • M conversation turns             │
│   • Smart suggestions cache          │
│                                      │
│  Will be kept:                       │
│   • Your chat history                │
│     (delete separately)              │
│                                      │
│  [Cancel]              [Forget Me]   │
└──────────────────────────────────────┘
```

**Temporary chat (existing UI, fix backend wiring):** No new UI component. The existing ghost button in the sidebar header (`app/templates/index.html:341`) calls `chatManager.createNewChat(null, {temporary: true})` and the JS handles all visual state via `isTemporaryUiActive`. The dashed border, banner, and incognito greeting are already implemented. PR 5 fixes the backend to honor the flag.

**Delete-chat dialog (existing copy fix):**

```
Old: "Delete chat?"
New: "Delete chat? This removes the conversation from your
      history. The AI's memory of you stays intact — to clear
      that, use 'Forget Me' in Memory settings."
```

## Data flow

### Reading the "Conversation memory" tab

```
User opens tab in modal
  ↓ JS fetch
GET /user/conversation-memory?cursor=null&limit=100
  ↓ Flask handler (memory_routes.py)
  ↓ sidecar GET /palace/{user_id}/drawers?limit=100&offset=0
  ↓ sidecar returns {drawers: [{drawer_id, text, metadata, ts}, ...]}
  ↓ Flask formats:
    - derives role: starts with "ACTION: chat" → "assistant"; else "user"
    - strips "ACTION: chat\n" prefix from display text
    - sorts by ts desc
    - applies user's "User only" filter if set
  ↓ JSON response
{turns: [{drawer_id, role, text, ts}, ...], next_cursor}
  ↓ Frontend renders rows
```

### Deleting a single turn

```
User clicks 🗑 on a row
  ↓ confirm("Delete this turn?")
DELETE /user/conversation-memory/{drawer_id}
  ↓ Flask
  ↓ sidecar DELETE /palace/{user_id}/drawers/{drawer_id}
  ↓ sidecar (NEW endpoint)
    1. lookup drawer_id in chroma collection (verify exists)
    2. lookup matching row in turns.sqlite via drawer_id
    3. collection.delete(ids=[drawer_id])
    4. DELETE FROM turns WHERE drawer_id = ?
    5. audit log: {op: "delete_drawer", drawer_id, user_id, ts}
  ↓ 200 OK (or 404 if missing — idempotent)
  ↓ Frontend removes row, decrements counter
```

### Toggle reads/writes

Storage: `users/{uid}/preferences/memory` Firestore document. Three boolean fields: `master`, `ref_v2`, `ref_mempalace`.

```
On panel open:
  GET /user/memory-toggles
  → Flask reads Firestore prefs doc (defaults all true if missing)
  → {master: true, ref_v2: true, ref_mempalace: true}
  → Frontend pre-toggles checkboxes

User flips a toggle:
  POST /user/memory-toggles {master: false}
  → Flask writes to Firestore prefs

At chat time:
  chat_routes reads prefs once per request:
    - master OFF       → skip both mempalace + v2 retrieval AND writes
    - ref_v2 OFF       → skip v2 extraction at write, skip v2 retrieval
    - ref_mempalace OFF → skip mempalace adapter calls (read + write)
```

The master toggle is implemented as a per-user override of `MEMPALACE_MODE`. Server-level `MEMPALACE_MODE=active` remains the default; per-user `master: false` flips that user to effectively-OFF.

### Forget Me cascade

Best-effort cascade, partial-failure tolerant. Never aborts mid-cascade.

```
User clicks "Forget Me" → confirmation modal → confirms
  ↓ DELETE /user/forget-me
  ↓ Flask handler

  step 1: count what we're about to delete (for the response)
    counts = {
      v2_facts: count_memory_v2(uid),
      mempalace_turns: count_palace_drawers(uid),
      smart_suggestions: count_suggestions(uid),
    }

  step 2: cascade deletes (each independent)
    a. sidecar DELETE /palace/{uid}                  → wipes whole palace
    b. SQLite outbox: DELETE FROM outbox WHERE user_id=?
    c. Firestore users/{uid}/memory_v2: collection delete
    d. Firestore users/{uid}/memory_events: collection delete
    e. Firestore users/{uid}/smart_suggestions: collection delete

  step 3: build response
    {
      deleted: counts,
      partial_failures: [...],
      kept: ["chat_sessions", "audit_logs", "account_data"],
    }
  ↓ Frontend shows success toast + closes modal
  ↓ Forces refresh of memory panel (everything empty)
```

What Forget Me does NOT touch:
- `users/{uid}/chat_sessions` (separate "Delete chats" action)
- `users/{uid}/preferences` (the user's own toggle settings — if they had memory turned OFF before clicking Forget Me, that preference persists)
- Account info (`users/{uid}` doc itself)
- Audit logs (compliance retention)

### Temporary chat — fix the existing incognito feature

Frontend (NO changes — already wired):
- Sidebar ghost button + `tempChatButton` / `tempChatButtonDesktop`
- `chatManager.createNewChat({temporary: true})` is the entry point
- `isTemporaryUiActive` UI state, dashed-border treatment, banner — already implemented
- `greeting.js` incognito strings already in place

Backend changes (PR 5):

```python
# app/blueprints/chat_routes.py — start_new_chat()
# CURRENT:
#   if is_guest():
#       session_id = f"guest_{uuid.uuid4().hex[:16]}"
#       ... return ephemeral session_id, no Firestore write
#   else:
#       session_id = chat_session.create_session(
#           user_id, session_name=session_name, temporary=temporary
#       )
#       # ^ writes to Firestore even when temporary=True

# NEW (mirror guest_mode pattern for temporary):
if is_guest():
    session_id = f"guest_{uuid.uuid4().hex[:16]}"
    session['current_session_id'] = session_id
    return jsonify({...}), 201

if temporary:
    # True ephemeral — no Firestore session document
    session_id = f"temp_{uuid.uuid4().hex[:16]}"
    session['current_session_id'] = session_id
    return jsonify({
        'session_id': session_id,
        'status': 'temporary',
    }), 201

# Regular non-temporary path unchanged
session_id = chat_session.create_session(user_id, session_name=session_name)
```

Inside the chat handler (streaming + non-streaming), at the top alongside the existing `guest_mode` detection:

```python
guest_mode = 'guest_id' in session and 'user' not in session
is_temp_session = (
    isinstance(session_id, str) and session_id.startswith('temp_')
)
skip_writes = guest_mode or is_temp_session
```

Then gate every existing write site:

- **mempalace adapter `wake_up_sync`**: still call for context (maybe; see below) — but `record_turn` for both user-half and assistant-half: skip when `skip_writes`.
- **memory_v2 extraction post-turn**: skip when `skip_writes`.
- **Firestore session message append**: skip when `skip_writes`.
- **Semantic cache write** (PR #119 logic): unchanged — skip already covers temp via `_mp_active_injected` being false (no recall used).

**Open design point for `wake_up_sync` in temp mode:** ChatGPT's temp chat skips memory recall too — the conversation is fully isolated. We mirror that — skip mempalace `wake_up_sync` as well. Implementation: gate the wake-up call on `skip_writes` is wrong (it's not a write, it's a read), so explicitly: also skip wake_up_sync for `is_temp_session`.

Final temp-mode behavior: zero contact with persistent stores in either direction.

### Delete-chat copy fix

Pure text edit in `app/blueprints/session_routes.py` confirmation dialog template.

```python
old_text = "Delete this conversation? This action cannot be undone."
new_text = (
    "Delete this conversation? This removes it from your "
    "chat history. The AI's memory of you stays intact — "
    "to clear that, use 'Forget Me' in Memory settings."
)
```

No backend behavior change.

## Error handling

**Conversation memory tab (read):**
- Sidecar unreachable → toast "Couldn't load conversation memory. Retry?", don't update list.
- Empty palace → "No conversation memory yet" empty state.
- Pagination cursor invalid → silent reset to start.

**Per-item delete:**
- Sidecar 404 (drawer already gone) → succeed silently, remove row from UI (idempotent).
- Sidecar 500 → toast error, leave row in place, log warning.
- Double-click race → second call returns 404 → handled.

**Forget Me cascade (best-effort, never abort mid-cascade):**
- Each step independent, errors collected.
- Sidecar palace-delete fails → still wipe Firestore + outbox, report `partial_failures: ["palace"]`.
- Any single Firestore collection wipe fails → still wipe others.
- Outbox wipe fails → still report success on others.
- All-failed case → red toast "Couldn't delete some data — what was deleted: [list]. Retry?"
- Successful deletes never roll back; eventual consistency.

**Toggle writes:**
- Firestore prefs doc missing on read → defaults all `true`, no error.
- Toggle write fails → revert UI optimistic state, show toast.
- Toggle gates fail-OPEN: read errors treated as ON, never accidentally OFF.

**Temporary chat:**
- Connection loss mid-temp-session → no recovery needed (data was never saved).
- Page refresh mid-temp → Flask session keeps `current_session_id` with `temp_` prefix; chat handler treats fresh; prior turns in browser memory only, lost on refresh (matches ChatGPT — temp chats can't be reopened).
- User clicks ghost while in active session → switches to new temp session; prior active session unchanged in sidebar.

**Delete-chat copy:** no error states (text-only change).

## Testing

### Unit tests

```
tests/unit/test_memory_routes_conversation_memory.py
  - GET happy path with mocked sidecar response
  - GET handles empty palace
  - GET pagination cursor round-trip
  - DELETE 404 idempotent
  - DELETE 500 surfaces as 500
  - Auth: cross-user delete blocked

tests/unit/test_memory_routes_forget_me.py
  - Happy cascade — all stores wiped, counts in response
  - Sidecar fails: Firestore + outbox still wiped, partial_failures: ["palace"]
  - Firestore one collection fails: others still wiped
  - chat_sessions NOT touched (existence check after)
  - audit_logs NOT touched (compliance retention)

tests/unit/test_memory_routes_toggles.py
  - GET defaults when prefs doc missing → all true
  - POST round-trip
  - Toggle reads at chat time gate retrieval/extraction (integration)

tests/unit/test_chat_routes_temp_mode.py
  - POST /chat/new {temporary: true} → session_id starts "temp_", no Firestore write
  - Chat with temp_ session_id → no mempalace adapter calls (mock asserts)
  - Chat with temp_ session_id → no memory_v2 extraction (mock asserts)
  - Chat with temp_ session_id → no Firestore session message append
  - Existing guest_mode tests still pass (regression protection)

tests/unit/test_session_routes_delete_copy.py
  - Confirmation dialog renders new copy substring
  - "Forget Me in Memory settings" link present
```

### Sidecar tests

```
mempalace-sidecar/tests/unit/test_palace_handle_delete_drawer.py
  - delete_drawer(drawer_id) removes from chroma collection
  - delete_drawer(drawer_id) removes from turns.sqlite
  - delete_drawer(missing_id) is idempotent (no error raised)
  - delete_drawer writes audit log entry per spec §6
  - delete_drawer doesn't touch other users' palaces (isolation)

mempalace-sidecar/tests/unit/test_palace_routes_delete_drawer.py
  - DELETE /palace/{user_id}/drawers/{drawer_id} → 200
  - Wrong token → 401
  - Cross-user drawer_id → 404
```

### E2E manual checklist (one full pass per PR before merge)

1. Fresh user logs in, has 5-message conversation.
2. Settings → Memory → Conversation memory tab → 5+ turns visible, role badges correct.
3. Delete one user turn → verify gone from chroma via direct sidecar probe.
4. Toggle "Reference chat history" OFF → ask recall question → AI doesn't recall.
5. Toggle ON → AI recalls.
6. Click Forget Me → confirm → all turns gone, v2 facts gone, chat_sessions kept (sidebar still shows the chat).
7. Click ghost icon (incognito) → ask question with personal info → close → verify NO writes anywhere: outbox no rows, shadow JSONL no entries, Firestore session doc absent.
8. Delete a chat from sidebar → confirmation dialog reads new copy mentioning "Forget Me".
9. Confirm delete → chat_sessions doc gone, mempalace data NOT touched (palace turn count unchanged).

## PR breakdown (recommended order)

Vertical slices — each PR end-to-end and independently shippable. Order chosen to drive down user-visible risk first.

| # | PR title | Effort | Risk |
|---|---|---|---|
| 1 | `chore(session): clarify delete-chat dialog scope` | ~30 min | Trivial |
| 2 | `feat(memory): conversation-memory tab in settings panel (read-only)` | 1 day | Low |
| 3 | `feat(mempalace-sidecar): delete-drawer endpoint + per-item delete UI` | 1 day | Medium (sidecar API surface change) |
| 4 | `feat(memory): Forget Me cascade — broadens existing Clear All` | Half day | Medium (multi-store cascade — partial-failure handling) |
| 5 | `fix(chat): wire incognito flag to actually skip writes` | Half day | Medium (touches hot chat path; needs careful regression-test against guest_mode) |

Total: ~3.5-4 days of focused work.

If a PR uncovers a wrinkle, previous ships still useful and we pause to rethink. Matches the 12-PR-rapid-ship pattern from 2026-04-29 P3 entry day.

## Acceptance criteria (Week 1 done)

User-facing:
- A user can open Settings → Memory and see ALL of what the AI knows about them in two sections: facts + conversation turns.
- A user can delete any individual conversation turn or fact.
- A user can click "Forget Me" and watch the AI lose all long-term memory of them, while their chat history sidebar stays intact.
- A user can toggle each memory system on/off independently and feel that toggle take effect on the next chat.
- A user can click the incognito ghost and have a true ephemeral conversation — verified by NO traces in any persistent store.
- A user who clicks "Delete chat" sees clear copy explaining what's deleted vs what isn't.

Engineering:
- All unit tests pass.
- E2E manual checklist passes once for each merged PR.
- No regression in existing memory_v2 panel functionality (pin, dream activity, undo).
- No regression in existing guest_mode chat path (parallel pattern protected by tests).

## Future work (not in this spec)

- Tier 2 (Week 2): inline "Memory updated" notifications, memory search, master ON/OFF toggle separately from per-system.
- Tier 3 (Weeks 3-4): categorized memory display (wing/room surfaced), confidence display, provenance display.
- "Erase all my data" account-deletion-style flow.
- Memory edit (not just delete).
- Per-domain (per-wing) memory namespaces.
- Memory timeline view.
- Issue #121 — assistant hallucination indexing cleanup (orthogonal to this design).

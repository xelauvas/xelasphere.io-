# Mempalace Trust Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring XelaSphere's memory UX to ChatGPT parity — extend the memory panel for mempalace, ship Forget Me, wire the existing incognito flag end-to-end, fix the delete-chat copy ambiguity.

**Architecture:** Extend existing `app/blueprints/memory_routes.py` blueprint with conversation-memory + toggles + Forget-Me endpoints. Add per-drawer delete endpoint to mempalace-sidecar. Extend the existing memory modal in `app/templates/settings_api_keys.html` with a new tab and toggle rows. Fix the existing incognito feature whose UI is wired but whose backend writes still happen. No new top-level Python modules. No schema migrations.

**Tech Stack:** Flask blueprints, FastAPI sidecar, vanilla JS frontend, Firestore (user-scoped subcollections), SQLite (`turns.sqlite` + `outbox.db`), ChromaDB (palace embeddings), pytest.

**Spec:** `docs/superpowers/specs/2026-04-29-mempalace-trust-contract-design.md`

**PR breakdown deviation:** The spec lists 5 PRs but the toggles (master/ref_v2/ref_mempalace) didn't fit naturally into any of them on closer inspection — they're a separate concern from Forget Me. This plan splits to **6 PRs**, total effort still ~4 days. Documented at the start of the relevant PR sections.

---

## File Structure

### Files to create

```
app/mempalace/format.py                                       (already created in PR #118 — referenced)
app/blueprints/memory_routes.py                               EXTEND (add 6 endpoints)

mempalace-sidecar/mempalace_sidecar/palace_handle.py          EXTEND (add delete_drawer method)
mempalace-sidecar/mempalace_sidecar/routes/palace.py          EXTEND (add DELETE /drawers/<id> route)

app/templates/settings_api_keys.html                          EXTEND
  - new toggle rows
  - new "Conversation memory" tab button + panel
  - rename "Clear all" → "Forget Me"
  - update Forget Me confirmation copy
  - new toggle JS handlers + tab fetcher + delete handlers

app/static/js/startNewChatModule.js                           MODIFY
  - delete-chat confirmation copy fix (line ~4012)

app/blueprints/chat_routes.py                                 MODIFY
  - start_new_chat: add temp_ session_id path
  - chat handler: add is_temp_session detection + skip_writes gates
  - chat handler: read user memory toggles from Firestore prefs

tests/unit/test_memory_routes_conversation_memory.py          NEW
tests/unit/test_memory_routes_forget_me.py                    NEW
tests/unit/test_memory_routes_toggles.py                      NEW
tests/unit/test_chat_routes_temp_mode.py                      NEW
tests/unit/test_session_routes_delete_copy.py                 NEW

mempalace-sidecar/tests/unit/test_palace_handle_delete_drawer.py    NEW
mempalace-sidecar/tests/unit/test_palace_routes_delete_drawer.py    NEW
```

### File responsibilities

- `memory_routes.py` — owns user-facing memory CRUD + toggles + Forget Me cascade. Translates between Firestore + sidecar HTTP API.
- `palace_handle.py` (sidecar) — owns per-drawer mutations on chroma + turns.sqlite + audit log. Single source of truth for "what does deleting a drawer mean."
- `routes/palace.py` (sidecar) — thin HTTP wrapper over `palace_handle` methods. Validates auth.
- `settings_api_keys.html` — single template owns all settings UI. JS lives at the bottom of the file (existing convention); follow that pattern.
- `chat_routes.py` — chat lifecycle. Reads toggles + temp-session state and gates write/read sites.
- `startNewChatModule.js` — owns chat UI state including the delete-chat confirmation modal.

---

## PR 1 — Delete-chat copy fix

**Files:**
- Modify: `app/static/js/startNewChatModule.js` around line 4012
- Test: `tests/unit/test_session_routes_delete_copy.py` (new — small smoke test)

**Effort:** ~30 minutes

**Approach:** Pure text edit in the existing chat-delete confirmation modal. The text lives in `startNewChatModule.js` (NOT `session_routes.py` as the spec mistakenly noted — the spec was slightly wrong on file location, corrected here). One git diff, one commit.

### Task 1.1: Update the delete-chat confirmation copy

**Files:**
- Modify: `app/static/js/startNewChatModule.js:4012` and the surrounding `<p>` body text

- [ ] **Step 1: Read the existing dialog markup at line 4012**

```bash
sed -n '4000,4040p' app/static/js/startNewChatModule.js
```

Find the block that contains the `<h2>Delete chat?</h2>` heading and the body `<p>` describing the action. Copy the existing text verbatim so the next step replaces it cleanly.

- [ ] **Step 2: Update the body copy to clarify scope**

In `app/static/js/startNewChatModule.js`, find the `<h2>Delete chat?</h2>` block and replace the body paragraph. The existing body (whatever it currently says — usually something like "Are you sure?" or "This action cannot be undone.") becomes:

```html
<p>This removes the conversation from your chat history. The AI's memory of you stays intact — to clear that, use <strong>Forget Me</strong> in Memory settings.</p>
```

If the existing markup uses a different element style, match it; preserve any classes/IDs.

- [ ] **Step 3: Manual verify in browser**

Run the dev server (`./run-dev.sh` or however the project starts Flask) and, while logged in, click delete on any chat. Confirm the new copy renders with `<strong>Forget Me</strong>` styled correctly.

- [ ] **Step 4: Commit**

```bash
git checkout -b chore/clarify-delete-chat-dialog-scope
git add app/static/js/startNewChatModule.js
git commit -m "$(cat <<'EOF'
chore(session): clarify delete-chat dialog scope

Riley dogfood (2026-04-29) showed that "Delete chats" UI implied more
than the action delivered. Body copy now explicitly notes that mempalace +
memory_v2 retention persist after a chat-history delete; user is
pointed at "Forget Me" in Memory settings for the broader scope.

Per spec docs/superpowers/specs/2026-04-29-mempalace-trust-contract-design.md.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin chore/clarify-delete-chat-dialog-scope
gh pr create --title "chore(session): clarify delete-chat dialog scope" --body "Tier-1 trust-contract PR 1/6. Pure text edit. See spec for context."
```

---

## PR 2 — Conversation-memory tab (read-only)

**Files:**
- Modify: `app/blueprints/memory_routes.py` (add 1 endpoint at the end of the existing file)
- Modify: `app/templates/settings_api_keys.html` (extend memory modal)
- Test: `tests/unit/test_memory_routes_conversation_memory.py` (new)

**Effort:** ~1 day

**Approach:** Add a single GET endpoint that calls the sidecar's existing `GET /palace/{user_id}/drawers` and formats the response for the panel. Add a third tab button + panel to the modal markup. Add JS in the existing `<script>` block to fetch + render. No deletes yet — that's PR 3.

### Task 2.1: Backend GET /user/conversation-memory endpoint (TDD)

**Files:**
- Create: `tests/unit/test_memory_routes_conversation_memory.py`
- Modify: `app/blueprints/memory_routes.py` — add new endpoint at end of file

- [ ] **Step 1: Write the failing test for happy path**

Create `tests/unit/test_memory_routes_conversation_memory.py`:

```python
"""Tests for GET /user/conversation-memory endpoint.

Verifies the Flask wrapper around the sidecar's
GET /palace/{user_id}/drawers — formats the response into the
{turns: [...], next_cursor} shape the frontend expects.
"""
from unittest.mock import patch


def test_get_conversation_memory_happy_path(authenticated_client):
    """User can fetch their conversation memory turns; response shape
    matches the {turns, next_cursor} contract.
    """
    fake_sidecar_response = {
        "drawers": [
            {
                "drawer_id": "drawer_aaa",
                "text": "I'm Riley, documentary filmmaker in Toledo.",
                "metadata": {"wing": "conversations", "room": "session"},
                "ts": 1700000000.0,
            },
            {
                "drawer_id": "drawer_bbb",
                "text": "ACTION: chat\nGreat — what's the project about?",
                "metadata": {"wing": "conversations", "room": "session"},
                "ts": 1700000010.0,
            },
        ],
        "total": 2,
        "next_offset": None,
    }
    with patch("app.blueprints.memory_routes._sidecar_list_drawers",
               return_value=fake_sidecar_response):
        resp = authenticated_client.get("/user/conversation-memory")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "turns" in body
    assert len(body["turns"]) == 2
    # User turn — no ACTION:chat prefix
    assert body["turns"][0]["role"] == "user"
    assert body["turns"][0]["text"] == "I'm Riley, documentary filmmaker in Toledo."
    assert body["turns"][0]["drawer_id"] == "drawer_aaa"
    # Assistant turn — ACTION:chat prefix stripped
    assert body["turns"][1]["role"] == "assistant"
    assert body["turns"][1]["text"] == "Great — what's the project about?"
    assert "next_cursor" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/xelas.io/xelasphere.io-
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_conversation_memory.py -v
```

Expected: FAIL with `404` or `AttributeError` (endpoint doesn't exist yet, helper doesn't exist).

- [ ] **Step 3: Implement the helper + endpoint**

In `app/blueprints/memory_routes.py`, add at the bottom of the file (after the existing endpoints):

```python
import os
import requests as _requests


def _sidecar_base_url() -> str:
    """Return the base URL for the mempalace sidecar (UDS or HTTP per env).
    Mirrors how the existing adapter resolves the sidecar URL —
    see app.mempalace.adapter for the source of truth.
    """
    return os.getenv("MEMPALACE_SIDECAR_URL", "http://sidecar")


def _sidecar_token() -> str:
    return os.getenv("MEMPALACE_TOKEN", "")


def _sidecar_list_drawers(user_id: str, limit: int = 100, offset: int = 0) -> dict:
    """Call sidecar GET /palace/{user_id}/drawers and return the raw envelope.
    Separated so tests can monkey-patch this single seam.
    """
    url = f"{_sidecar_base_url()}/palace/{user_id}/drawers"
    resp = _requests.get(
        url,
        params={"limit": limit, "offset": offset},
        headers={"X-Sidecar-Token": _sidecar_token()},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


@memory_bp.route('/user/conversation-memory', methods=['GET'])
@error_handler
@login_required
def get_conversation_memory():
    """Get the user's mempalace conversation turns, formatted for the panel.

    Response shape:
        {turns: [{drawer_id, role, text, ts}], next_cursor: str|null}

    role is 'assistant' if text starts with "ACTION: chat"; else 'user'.
    The "ACTION: chat\\n" prefix is stripped for display.
    """
    user_id = session['user']['uid']
    cursor = request.args.get('cursor')
    limit = min(int(request.args.get('limit', 100)), 200)
    offset = int(cursor) if cursor and cursor.isdigit() else 0

    try:
        envelope = _sidecar_list_drawers(user_id, limit=limit, offset=offset)
    except Exception as exc:
        logger.warning("Sidecar drawers list failed: %s", exc)
        return jsonify({"error": "sidecar_unavailable"}), 503

    drawers = envelope.get("drawers", []) or []
    turns = []
    for d in drawers:
        text = (d.get("text") or "").strip()
        if not text:
            continue
        if text.startswith("ACTION: chat"):
            role = "assistant"
            display = text[len("ACTION: chat"):].lstrip("\n").strip()
        else:
            role = "user"
            display = text
        turns.append({
            "drawer_id": d.get("drawer_id"),
            "role": role,
            "text": display,
            "ts": d.get("ts"),
        })

    next_offset = envelope.get("next_offset")
    next_cursor = str(next_offset) if next_offset is not None else None
    return jsonify({"turns": turns, "next_cursor": next_cursor})
```

- [ ] **Step 4: Run test to verify it passes**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_conversation_memory.py::test_get_conversation_memory_happy_path -v
```

Expected: PASS

- [ ] **Step 5: Add tests for empty palace + sidecar failure**

Append to `tests/unit/test_memory_routes_conversation_memory.py`:

```python
def test_get_conversation_memory_empty_palace(authenticated_client):
    """Empty palace returns an empty turns array, not an error."""
    with patch("app.blueprints.memory_routes._sidecar_list_drawers",
               return_value={"drawers": [], "total": 0, "next_offset": None}):
        resp = authenticated_client.get("/user/conversation-memory")
    assert resp.status_code == 200
    assert resp.get_json() == {"turns": [], "next_cursor": None}


def test_get_conversation_memory_sidecar_unreachable(authenticated_client):
    """If the sidecar is down, return 503 — frontend will toast."""
    def boom(*a, **kw):
        raise ConnectionError("sidecar down")
    with patch("app.blueprints.memory_routes._sidecar_list_drawers",
               side_effect=boom):
        resp = authenticated_client.get("/user/conversation-memory")
    assert resp.status_code == 503
    assert resp.get_json() == {"error": "sidecar_unavailable"}


def test_get_conversation_memory_pagination_cursor_round_trip(authenticated_client):
    """Cursor (string offset) round-trips through the request → sidecar →
    response shape.
    """
    seen = {}
    def capture(user_id, limit=100, offset=0):
        seen["limit"] = limit
        seen["offset"] = offset
        return {"drawers": [], "total": 0, "next_offset": offset + limit}
    with patch("app.blueprints.memory_routes._sidecar_list_drawers",
               side_effect=capture):
        resp = authenticated_client.get("/user/conversation-memory?cursor=200&limit=50")
    assert resp.status_code == 200
    assert seen == {"limit": 50, "offset": 200}
    assert resp.get_json()["next_cursor"] == "250"
```

- [ ] **Step 6: Run all conversation-memory tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_conversation_memory.py -v
```

Expected: 4 passed.

- [ ] **Step 7: Commit (first half of PR 2)**

```bash
git checkout -b feat/memory-conversation-memory-tab
git add app/blueprints/memory_routes.py tests/unit/test_memory_routes_conversation_memory.py
git commit -m "feat(memory): add GET /user/conversation-memory endpoint

Reads from sidecar /palace/{user_id}/drawers, formats response into
{turns: [{drawer_id, role, text, ts}], next_cursor} for the new
Conversation memory tab. Role derived from ACTION:chat prefix; prefix
stripped for display. Pagination via offset cursor.

Tested: happy path, empty palace, sidecar 503, cursor round-trip.

Tier-1 trust-contract PR 2/6 (read-only — per-item delete in PR 3)."
```

### Task 2.2: Frontend tab markup + skeleton

**Files:**
- Modify: `app/templates/settings_api_keys.html` (lines 339-342 for tab buttons; line 357-358 for new panel)

- [ ] **Step 1: Read the existing tab structure**

```bash
sed -n '339,395p' app/templates/settings_api_keys.html
```

Note the structure: `.memory-tab-toggle` contains buttons with `data-memory-tab` attrs; `.memory-tab-panel` blocks have matching `data-memory-panel` attrs.

- [ ] **Step 2: Add the new tab button**

In `app/templates/settings_api_keys.html`, find the block:

```html
<div class="memory-tab-toggle">
    <button type="button" class="memory-tab-btn active" data-memory-tab="memories">Memories</button>
    <button type="button" class="memory-tab-btn" data-memory-tab="activity">Activity</button>
</div>
```

Replace with:

```html
<div class="memory-tab-toggle">
    <button type="button" class="memory-tab-btn active" data-memory-tab="memories">Memories</button>
    <button type="button" class="memory-tab-btn" data-memory-tab="conversation">Conversation memory</button>
    <button type="button" class="memory-tab-btn" data-memory-tab="activity">Activity</button>
</div>
```

- [ ] **Step 3: Add the new tab panel skeleton**

After the existing memories panel (closes around line 357 with `</div>`) and BEFORE the activity panel block, insert:

```html
<!-- Conversation memory tab (mempalace turns, NEW) -->
<div class="memory-tab-panel" data-memory-panel="conversation">
    <div class="conversation-memory-filter" style="padding: 0.5rem 0.75rem; display: flex; gap: 0.4rem;">
        <button type="button" class="conv-filter-chip active" data-conv-filter="all">All turns</button>
        <button type="button" class="conv-filter-chip" data-conv-filter="user">User only</button>
    </div>
    <div class="conversation-memory-container">
        <div class="conversation-loading" id="conversation-loading">
            <i class="fas fa-spinner fa-spin"></i>
            <span>Loading conversation memory...</span>
        </div>
        <div class="conversation-empty" id="conversation-empty" style="display: none;">
            <i class="fas fa-comment-dots"></i>
            <p>No conversation memory yet. As you chat, the AI saves what you tell it for future recall.</p>
        </div>
        <div class="conversation-error" id="conversation-error" style="display: none;">
            <p>Couldn't load conversation memory. <a href="#" id="conversation-retry">Retry</a></p>
        </div>
        <div class="conversation-list" id="conversation-list"></div>
    </div>
</div>
```

- [ ] **Step 4: Verify the markup renders without breaking the existing modal**

```bash
# Local server must be running
# Open Settings → Memory → click between the three tabs
# Verify clicking "Conversation memory" shows the loading spinner
# (data-fetch hasn't been wired yet — that's Task 2.3)
```

- [ ] **Step 5: Commit markup changes**

```bash
git add app/templates/settings_api_keys.html
git commit -m "feat(memory): add Conversation memory tab markup to settings panel

Skeleton tab: filter chips (All turns / User only), loading state,
empty state, error state, list container. Data fetcher wired in next
commit.

Tier-1 trust-contract PR 2/6."
```

### Task 2.3: Frontend JS — fetch + render

**Files:**
- Modify: `app/templates/settings_api_keys.html` (the `<script>` block at the bottom — find existing memory JS handlers and add new ones)

- [ ] **Step 1: Find the existing memory-modal JS block**

```bash
grep -n "openMemoryModal\|fetchMemories\|memory-buckets" app/templates/settings_api_keys.html | head -10
```

Note the function pattern used (typically `async function fetchMemories()` etc.). Match the style.

- [ ] **Step 2: Add the conversation-memory fetcher function**

Within the existing `<script>` block (search for the closing `</script>` tag near the bottom of the file), add BEFORE the closing tag:

```javascript
// ─── Conversation memory tab (mempalace turns) ───
async function fetchConversationMemory(filter = 'all') {
    const loading = document.getElementById('conversation-loading');
    const empty = document.getElementById('conversation-empty');
    const error = document.getElementById('conversation-error');
    const list = document.getElementById('conversation-list');

    loading.style.display = 'flex';
    empty.style.display = 'none';
    error.style.display = 'none';
    list.innerHTML = '';

    try {
        const resp = await fetch('/user/conversation-memory', { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const turns = (data.turns || []);
        const filtered = filter === 'user'
            ? turns.filter(t => t.role === 'user')
            : turns;

        loading.style.display = 'none';
        if (filtered.length === 0) {
            empty.style.display = 'flex';
            return;
        }
        list.innerHTML = filtered.map(t => `
            <div class="conv-row" data-drawer-id="${t.drawer_id}">
                <span class="conv-role-badge conv-role-${t.role}">[${t.role}]</span>
                <span class="conv-text">${escapeHtml(t.text)}</span>
                <button type="button" class="conv-delete-btn" data-drawer-id="${t.drawer_id}" title="Delete this turn">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `).join('');
    } catch (e) {
        console.warn('[conversation-memory] fetch failed:', e);
        loading.style.display = 'none';
        error.style.display = 'flex';
    }
}

function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s || '';
    return div.innerHTML;
}
```

- [ ] **Step 3: Wire the tab-switch event to fetch on first open**

Find the existing tab-switch handler (loop over `.memory-tab-btn`). It looks something like:

```javascript
memoryTabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-memory-tab');
        // ... activate panel ...
    });
});
```

Update the inside of that handler to also call `fetchConversationMemory()` when the conversation tab is selected:

```javascript
if (tab === 'conversation') {
    fetchConversationMemory('all');
}
```

- [ ] **Step 4: Wire the filter chips**

Add at the bottom of the same script block:

```javascript
document.querySelectorAll('.conv-filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        document.querySelectorAll('.conv-filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        const filter = chip.getAttribute('data-conv-filter');
        fetchConversationMemory(filter);
    });
});

document.getElementById('conversation-retry')?.addEventListener('click', (e) => {
    e.preventDefault();
    const activeFilter = document.querySelector('.conv-filter-chip.active')?.getAttribute('data-conv-filter') || 'all';
    fetchConversationMemory(activeFilter);
});
```

- [ ] **Step 5: Manual verify in browser**

Start dev server, log in as a user with mempalace data (you can use Riley's user_id `zHaSm1p58mOs3eFnp6uDwZBOeZJ2`). Open Settings → Memory → Conversation memory tab. Verify:
- Loading spinner appears briefly
- Rows render with `[user]` or `[assistant]` badge
- "User only" chip filters to user rows
- "All turns" chip restores

- [ ] **Step 6: Add minimal CSS for role badges + delete button**

Find the `<style>` block earlier in `settings_api_keys.html` (around line 35-200). Add at the bottom:

```css
/* Conversation memory tab */
.conversation-memory-container { max-height: 400px; overflow-y: auto; padding: 0.4rem 0.6rem; }
.conv-row { display: flex; align-items: flex-start; gap: 0.5rem; padding: 0.4rem 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.04); }
.conv-row:hover { background: rgba(255,255,255,0.02); }
.conv-role-badge { font-size: 0.65rem; font-weight: 600; padding: 0.1rem 0.4rem; border-radius: 4px; flex-shrink: 0; }
.conv-role-user { background: rgba(80, 200, 120, 0.15); color: #50c878; }
.conv-role-assistant { background: rgba(120, 160, 220, 0.15); color: #78a0dc; }
.conv-text { flex: 1; font-size: 0.8rem; color: #ccc; word-break: break-word; }
.conv-delete-btn { background: transparent; border: none; color: #888; cursor: pointer; padding: 0.2rem 0.4rem; flex-shrink: 0; }
.conv-delete-btn:hover { color: #f55; }
.conv-filter-chip { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); color: #888; padding: 0.3rem 0.7rem; border-radius: 6px; font-size: 0.75rem; cursor: pointer; }
.conv-filter-chip.active { background: rgba(255,255,255,0.1); color: #f5f6fb; }
```

- [ ] **Step 7: Commit JS + CSS**

```bash
git add app/templates/settings_api_keys.html
git commit -m "feat(memory): wire Conversation memory tab — fetch + render + filter

Pulls from /user/conversation-memory, renders rows with role badges,
supports All/User-only filter chips, retry on error. Delete buttons
present in markup but unwired (PR 3).

Tier-1 trust-contract PR 2/6."
```

- [ ] **Step 8: Push + open PR**

```bash
git push -u origin feat/memory-conversation-memory-tab
gh pr create --title "feat(memory): conversation-memory tab in settings panel (read-only)" --body "Tier-1 trust-contract PR 2/6.

- New GET /user/conversation-memory endpoint (formats sidecar response)
- New 'Conversation memory' tab in existing memory modal
- Filter chips (All turns / User only)
- Per-row delete buttons present but unwired (PR 3 wires them)

Spec: docs/superpowers/specs/2026-04-29-mempalace-trust-contract-design.md"
```

---

## PR 3 — Per-mempalace-item delete

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/palace_handle.py` — add `delete_drawer` method
- Modify: `mempalace-sidecar/mempalace_sidecar/routes/palace.py` — add DELETE route
- Modify: `app/blueprints/memory_routes.py` — add Flask wrapper
- Modify: `app/templates/settings_api_keys.html` — wire delete-button click handlers
- Test: `mempalace-sidecar/tests/unit/test_palace_handle_delete_drawer.py` (new)
- Test: `mempalace-sidecar/tests/unit/test_palace_routes_delete_drawer.py` (new)
- Test: append to `tests/unit/test_memory_routes_conversation_memory.py`

**Effort:** ~1 day

### Task 3.1: PalaceHandle.delete_drawer method (TDD)

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/palace_handle.py` — add method to `PalaceHandle` class
- Create: `mempalace-sidecar/tests/unit/test_palace_handle_delete_drawer.py`

- [ ] **Step 1: Write the failing test**

Create `mempalace-sidecar/tests/unit/test_palace_handle_delete_drawer.py`:

```python
"""Tests for PalaceHandle.delete_drawer.

Per-drawer surgical delete: remove from chroma collection AND
turns.sqlite atomically. Idempotent — missing drawer doesn't raise.
"""
from pathlib import Path

import pytest

from mempalace_sidecar.palace_handle import PalaceHandle


@pytest.fixture
def palace_path(tmp_path: Path) -> Path:
    return tmp_path / "palace"


@pytest.fixture
def handle(palace_path):
    h = PalaceHandle.open(palace_path)
    try:
        yield h
    finally:
        h.close()


def test_delete_drawer_removes_from_chroma_and_turns_sqlite(handle):
    """After delete, item is gone from BOTH stores."""
    # Add a drawer via the existing path (record_turn → ingest)
    receipt = handle.add_drawer(
        turn_id="t1",
        text="I am a test user statement.",
        wing="conversations",
        room="session",
        session_id="s1",
        pair_id="p1",
        role="user",
        ts=1700000000.0,
    )
    drawer_id = receipt.drawer_id

    # Verify present in chroma + turns.sqlite
    chroma_got_before = handle._collection.get(ids=[drawer_id])
    assert len(chroma_got_before.get("ids", [])) == 1
    sqlite_count_before = handle.turns.execute(
        "SELECT COUNT(*) FROM turns WHERE drawer_id = ?", (drawer_id,)
    ).fetchone()[0]
    assert sqlite_count_before == 1

    # Delete
    handle.delete_drawer(drawer_id)

    # Both stores must reflect the delete
    chroma_got_after = handle._collection.get(ids=[drawer_id])
    assert len(chroma_got_after.get("ids", [])) == 0
    sqlite_count_after = handle.turns.execute(
        "SELECT COUNT(*) FROM turns WHERE drawer_id = ?", (drawer_id,)
    ).fetchone()[0]
    assert sqlite_count_after == 0


def test_delete_drawer_missing_id_is_idempotent(handle):
    """Deleting a non-existent drawer must not raise."""
    handle.delete_drawer("drawer_does_not_exist_xyz")
    # No exception = pass


def test_delete_drawer_does_not_touch_other_drawers(handle):
    """Deleting one drawer leaves siblings intact."""
    r1 = handle.add_drawer(turn_id="t1", text="first", wing="conversations",
                           room="session", session_id="s1", pair_id="p1",
                           role="user", ts=1.0)
    r2 = handle.add_drawer(turn_id="t2", text="second", wing="conversations",
                           room="session", session_id="s1", pair_id="p2",
                           role="user", ts=2.0)
    handle.delete_drawer(r1.drawer_id)
    chroma_got = handle._collection.get(ids=[r2.drawer_id])
    assert len(chroma_got.get("ids", [])) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/xelas.io/xelasphere.io-/mempalace-sidecar
/opt/mempalace-sidecar/venv/bin/python -m pytest tests/unit/test_palace_handle_delete_drawer.py -v
```

Expected: FAIL with `AttributeError: 'PalaceHandle' object has no attribute 'delete_drawer'`.

- [ ] **Step 3: Implement delete_drawer**

In `mempalace-sidecar/mempalace_sidecar/palace_handle.py`, find the `PalaceHandle` class and add this method (place it after `list_drawers` or `delete_palace`, wherever fits the existing style):

```python
    def delete_drawer(self, drawer_id: str) -> None:
        """Surgical per-drawer delete: chroma collection + turns.sqlite.

        Idempotent — missing drawer is not an error. Both stores are
        updated; on partial failure (e.g., chroma succeeds but sqlite
        errors), the partial state is logged but not rolled back —
        eventual consistency wins for this op.

        Surfaced 2026-04-29 by trust-contract Week 1 design: users need
        a way to delete individual conversation turns from their palace
        (e.g., to remove hallucinated assistant responses indexed in
        prior shadow-mode usage).
        """
        if not drawer_id:
            return
        # Step 1: chroma — idempotent on missing IDs by default
        try:
            self._collection.delete(ids=[drawer_id])
        except Exception:
            log.exception("delete_drawer: chroma delete failed for %s", drawer_id)
        # Step 2: turns.sqlite
        try:
            self.turns.execute(
                "DELETE FROM turns WHERE drawer_id = ?", (drawer_id,)
            )
            self.turns.commit()
        except Exception:
            log.exception("delete_drawer: turns.sqlite delete failed for %s", drawer_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/opt/mempalace-sidecar/venv/bin/python -m pytest tests/unit/test_palace_handle_delete_drawer.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the full sidecar test suite to check for regressions**

```bash
/opt/mempalace-sidecar/venv/bin/python -m pytest --tb=short 2>&1 | tail -3
```

Expected: All previously-passing tests still pass (148+ passed at session start of 2026-04-29).

- [ ] **Step 6: Commit**

```bash
cd /root/xelas.io/xelasphere.io-
git checkout -b feat/mempalace-sidecar-delete-drawer
git add mempalace-sidecar/mempalace_sidecar/palace_handle.py mempalace-sidecar/tests/unit/test_palace_handle_delete_drawer.py
git commit -m "feat(mempalace-sidecar): PalaceHandle.delete_drawer

Surgical per-drawer delete touching both chroma collection and
turns.sqlite. Idempotent on missing drawer_id. Logs but doesn't
rollback partial failures.

Foundation for the trust-contract per-item delete UI (Week 1 PR 3).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task 3.2: Sidecar route DELETE /palace/{user_id}/drawers/{drawer_id} (TDD)

**Files:**
- Modify: `mempalace-sidecar/mempalace_sidecar/routes/palace.py` — add new route after `list_drawers`
- Create: `mempalace-sidecar/tests/unit/test_palace_routes_delete_drawer.py`

- [ ] **Step 1: Write the failing route test**

Create `mempalace-sidecar/tests/unit/test_palace_routes_delete_drawer.py`:

```python
"""Tests for DELETE /palace/{user_id}/drawers/{drawer_id} route."""
from fastapi.testclient import TestClient

from mempalace_sidecar.main import create_app


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMPALACE_TOKEN", "test-token")
    app = create_app()
    return TestClient(app)


def test_delete_drawer_returns_200_when_present(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    user_id = "test_user"
    headers = {"X-Sidecar-Token": "test-token"}

    # First ingest a turn so we have a drawer to delete
    ingest_resp = client.post(
        f"/palace/{user_id}/ingest",
        json={
            "turn_id": "t1",
            "text": "test statement",
            "wing": "conversations",
            "room": "session",
            "session_id": "s1",
            "pair_id": "p1",
            "role": "user",
            "ts": 1700000000.0,
        },
        headers=headers,
    )
    assert ingest_resp.status_code == 200
    drawer_id = ingest_resp.json()["drawer_id"]

    # Delete it
    resp = client.delete(
        f"/palace/{user_id}/drawers/{drawer_id}",
        headers=headers,
    )
    assert resp.status_code == 200


def test_delete_drawer_idempotent_on_missing(monkeypatch, tmp_path):
    """DELETE on non-existent drawer returns 200 (idempotent), not 404 —
    the underlying handle.delete_drawer is idempotent and errors
    here would just be a UX papercut.
    """
    client = _client(monkeypatch, tmp_path)
    headers = {"X-Sidecar-Token": "test-token"}
    # Pre-create the palace so the handle opens cleanly
    client.post(
        f"/palace/test_user/ingest",
        json={"turn_id": "t1", "text": "x", "wing": "conversations",
              "room": "session", "session_id": "s1", "pair_id": "p1",
              "role": "user", "ts": 1.0},
        headers=headers,
    )
    resp = client.delete(
        "/palace/test_user/drawers/nonexistent_drawer_id",
        headers=headers,
    )
    assert resp.status_code == 200


def test_delete_drawer_unauthorized_without_token(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/palace/test_user/drawers/some_id")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/xelas.io/xelasphere.io-/mempalace-sidecar
/opt/mempalace-sidecar/venv/bin/python -m pytest tests/unit/test_palace_routes_delete_drawer.py -v
```

Expected: FAIL — route returns 405 or 404 (not implemented).

- [ ] **Step 3: Implement the route**

In `mempalace-sidecar/mempalace_sidecar/routes/palace.py`, find the `list_drawers` route (around line 365). Add directly below it:

```python
@router.delete("/{user_id}/drawers/{drawer_id}", status_code=200)
async def delete_drawer(
    user_id: str,
    drawer_id: str,
    registry: PalaceRegistry = Depends(get_registry),
):
    """Surgical per-drawer delete. Idempotent on missing.

    Per-drawer (not per-palace) delete is the trust-contract Week 1
    feature: users need to clean up individual indexed turns
    (e.g., hallucinated assistant responses from earlier shadow mode).
    """
    if not user_id or not drawer_id:
        raise HTTPException(status_code=400, detail="missing user_id or drawer_id")
    try:
        async with registry.acquire(user_id, "delete_drawer") as entry:
            await asyncio.to_thread(entry.handle.delete_drawer, drawer_id)
    except Exception:
        log.exception("delete_drawer route failed user_id=%s drawer_id=%s",
                      user_id, drawer_id)
        raise HTTPException(status_code=500, detail="delete_drawer failed")
    return {"status": "ok", "drawer_id": drawer_id}
```

(Imports `asyncio` and `log` are likely already at the top of the file; if not, add: `import asyncio`, `from . import log` or wherever the existing route imports come from.)

- [ ] **Step 4: Run tests to verify pass**

```bash
/opt/mempalace-sidecar/venv/bin/python -m pytest tests/unit/test_palace_routes_delete_drawer.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /root/xelas.io/xelasphere.io-
git add mempalace-sidecar/mempalace_sidecar/routes/palace.py mempalace-sidecar/tests/unit/test_palace_routes_delete_drawer.py
git commit -m "feat(mempalace-sidecar): DELETE /palace/{user_id}/drawers/{drawer_id} route

Thin HTTP wrapper around PalaceHandle.delete_drawer. Auth via existing
X-Sidecar-Token middleware. Idempotent on missing drawer_id.

Tier-1 trust-contract PR 3/6."
```

### Task 3.3: Flask wrapper DELETE /user/conversation-memory/<drawer_id>

**Files:**
- Modify: `app/blueprints/memory_routes.py`
- Append: `tests/unit/test_memory_routes_conversation_memory.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_memory_routes_conversation_memory.py`:

```python
def test_delete_conversation_memory_item_happy_path(authenticated_client):
    with patch("app.blueprints.memory_routes._sidecar_delete_drawer",
               return_value=True) as mock_call:
        resp = authenticated_client.delete("/user/conversation-memory/drawer_xxx")
    assert resp.status_code == 200
    mock_call.assert_called_once_with("test_user_123", "drawer_xxx")


def test_delete_conversation_memory_item_sidecar_failure_returns_500(authenticated_client):
    def boom(*a, **kw):
        raise ConnectionError("sidecar down")
    with patch("app.blueprints.memory_routes._sidecar_delete_drawer",
               side_effect=boom):
        resp = authenticated_client.delete("/user/conversation-memory/drawer_xxx")
    assert resp.status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_conversation_memory.py::test_delete_conversation_memory_item_happy_path -v
```

Expected: FAIL — endpoint not yet defined.

- [ ] **Step 3: Implement the endpoint**

In `app/blueprints/memory_routes.py`, after the `get_conversation_memory` handler from PR 2, add:

```python
def _sidecar_delete_drawer(user_id: str, drawer_id: str) -> bool:
    """Call sidecar DELETE /palace/{user_id}/drawers/{drawer_id}.
    Separated as a single seam for testing.
    """
    url = f"{_sidecar_base_url()}/palace/{user_id}/drawers/{drawer_id}"
    resp = _requests.delete(
        url,
        headers={"X-Sidecar-Token": _sidecar_token()},
        timeout=5,
    )
    resp.raise_for_status()
    return True


@memory_bp.route('/user/conversation-memory/<drawer_id>', methods=['DELETE'])
@error_handler
@login_required
@csrf_required
def delete_conversation_memory_item(drawer_id: str):
    """Delete a single mempalace turn for the current user."""
    user_id = session['user']['uid']
    if not drawer_id:
        return jsonify({"error": "missing drawer_id"}), 400
    try:
        _sidecar_delete_drawer(user_id, drawer_id)
    except Exception as exc:
        logger.warning("Sidecar delete-drawer failed: %s", exc)
        return jsonify({"error": "sidecar_unavailable"}), 503
    return jsonify({"status": "ok", "drawer_id": drawer_id})
```

- [ ] **Step 4: Run tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_conversation_memory.py -v
```

Expected: 6 passed (4 from PR 2 + 2 new).

- [ ] **Step 5: Commit backend portion**

```bash
git add app/blueprints/memory_routes.py tests/unit/test_memory_routes_conversation_memory.py
git commit -m "feat(memory): DELETE /user/conversation-memory/<drawer_id>

Flask wrapper that bridges to sidecar DELETE /palace/.../drawers/<id>.
Tested: happy path, sidecar 503 fallback.

Tier-1 trust-contract PR 3/6."
```

### Task 3.4: Frontend per-row delete handler

**Files:**
- Modify: `app/templates/settings_api_keys.html` — JS section near the `fetchConversationMemory` function

- [ ] **Step 1: Add the click handler for `.conv-delete-btn`**

Inside the `<script>` block in `settings_api_keys.html`, after the `fetchConversationMemory` function from Task 2.3, add:

```javascript
// ─── Conversation memory: per-row delete ───
document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.conv-delete-btn');
    if (!btn) return;
    const drawerId = btn.getAttribute('data-drawer-id');
    if (!drawerId) return;
    if (!confirm('Delete this conversation turn? The AI will no longer remember it.')) return;

    btn.disabled = true;
    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const resp = await fetch(`/user/conversation-memory/${encodeURIComponent(drawerId)}`, {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrfToken },
        });
        if (resp.ok) {
            btn.closest('.conv-row')?.remove();
        } else {
            alert("Couldn't delete that turn — try again in a moment.");
            btn.disabled = false;
        }
    } catch (err) {
        console.warn('[conversation-memory] delete failed:', err);
        alert("Couldn't delete that turn — network error.");
        btn.disabled = false;
    }
});
```

- [ ] **Step 2: Manual verify in browser**

Refresh settings page → Memory → Conversation memory tab. Click trash on a turn. Confirm. Row disappears. Verify by direct sidecar probe:

```bash
TOKEN=$(sudo grep -oP 'MEMPALACE_TOKEN=\K\S+' /etc/mempalace/sidecar.env)
USER_ID="..."  # logged-in user
sudo curl -s --unix-socket /run/mempalace/sidecar.sock \
  "http://localhost/palace/${USER_ID}/drawers?limit=10" \
  -H "X-Sidecar-Token: $TOKEN" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for x in d.get('drawers',[])[:5]: print(x['drawer_id'], x['text'][:60])
"
```

The deleted drawer_id should not appear.

- [ ] **Step 3: Commit frontend portion**

```bash
git add app/templates/settings_api_keys.html
git commit -m "feat(memory): wire per-row delete in Conversation memory tab

Click trash → confirm → DELETE /user/conversation-memory/<id> →
row removed from UI. CSRF protected.

Tier-1 trust-contract PR 3/6."
```

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin feat/mempalace-sidecar-delete-drawer
gh pr create --title "feat(mempalace-sidecar): per-mempalace-item delete (closes Week-1 trust-contract PR 3)" --body "Tier-1 trust-contract PR 3/6.

- Sidecar: PalaceHandle.delete_drawer + DELETE route
- Flask: DELETE /user/conversation-memory/<drawer_id>
- Frontend: per-row delete button wires up

Spec: docs/superpowers/specs/2026-04-29-mempalace-trust-contract-design.md"
```

---

## PR 4 — Memory toggles (master + per-system)

**Files:**
- Modify: `app/blueprints/memory_routes.py` — 2 new endpoints
- Modify: `app/templates/settings_api_keys.html` — 3 toggle rows + JS handlers
- Modify: `app/blueprints/chat_routes.py` — read prefs at chat time, gate retrieval/extraction
- Test: `tests/unit/test_memory_routes_toggles.py` (new)

**Effort:** ~1 day

**Approach:** Three toggles backed by `users/{uid}/preferences/memory` Firestore doc with three boolean fields (`master`, `ref_v2`, `ref_mempalace`). All default to `true`. Read once per chat request and gate the existing mempalace + memory_v2 hooks.

### Task 4.1: Backend GET/POST /user/memory-toggles

**Files:**
- Modify: `app/blueprints/memory_routes.py`
- Create: `tests/unit/test_memory_routes_toggles.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_memory_routes_toggles.py`:

```python
"""Tests for /user/memory-toggles GET + POST.

Three boolean toggles stored as a single Firestore doc:
users/{uid}/preferences/memory
"""


def test_get_memory_toggles_defaults_when_doc_missing(authenticated_client, mock_firestore):
    """If no prefs doc exists yet, all three toggles default to true."""
    resp = authenticated_client.get("/user/memory-toggles")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "master": True,
        "ref_v2": True,
        "ref_mempalace": True,
    }


def test_post_memory_toggles_round_trip(authenticated_client, mock_firestore):
    """Toggle a value off, then read it back."""
    # Turn off mempalace
    resp = authenticated_client.post(
        "/user/memory-toggles",
        json={"ref_mempalace": False},
    )
    assert resp.status_code == 200

    # Read back
    resp2 = authenticated_client.get("/user/memory-toggles")
    body = resp2.get_json()
    assert body["ref_mempalace"] is False
    assert body["master"] is True
    assert body["ref_v2"] is True


def test_post_memory_toggles_partial_update(authenticated_client, mock_firestore):
    """POST with only some fields leaves others alone."""
    authenticated_client.post("/user/memory-toggles",
                               json={"master": False, "ref_v2": False})
    resp = authenticated_client.get("/user/memory-toggles")
    body = resp.get_json()
    assert body == {"master": False, "ref_v2": False, "ref_mempalace": True}


def test_post_memory_toggles_invalid_field_rejected(authenticated_client, mock_firestore):
    """Unknown fields don't get persisted (defensive)."""
    resp = authenticated_client.post(
        "/user/memory-toggles",
        json={"master": True, "rogue_field": "lol"},
    )
    assert resp.status_code == 200
    resp2 = authenticated_client.get("/user/memory-toggles")
    assert "rogue_field" not in resp2.get_json()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_toggles.py -v
```

Expected: 4 failures — endpoints don't exist.

- [ ] **Step 3: Implement the endpoints**

In `app/blueprints/memory_routes.py`, append:

```python
_TOGGLE_FIELDS = {"master", "ref_v2", "ref_mempalace"}
_TOGGLE_DEFAULTS = {f: True for f in _TOGGLE_FIELDS}


def _toggle_doc_ref(user_id: str):
    """Return the Firestore doc ref for the user's memory toggle prefs."""
    from app.config import db
    return db.collection("users").document(user_id) \
             .collection("preferences").document("memory")


@memory_bp.route('/user/memory-toggles', methods=['GET'])
@error_handler
@login_required
def get_memory_toggles():
    """Return the three boolean memory toggles. Defaults all-true."""
    user_id = session['user']['uid']
    snap = _toggle_doc_ref(user_id).get()
    data = snap.to_dict() if snap.exists else {}
    return jsonify({f: bool(data.get(f, True)) for f in _TOGGLE_FIELDS})


@memory_bp.route('/user/memory-toggles', methods=['POST'])
@error_handler
@login_required
@csrf_required
def post_memory_toggles():
    """Update one or more memory toggles. Unknown fields ignored."""
    user_id = session['user']['uid']
    payload = request.get_json(silent=True) or {}
    update = {f: bool(payload[f]) for f in _TOGGLE_FIELDS if f in payload}
    if not update:
        return jsonify({"error": "no valid fields"}), 400
    _toggle_doc_ref(user_id).set(update, merge=True)
    return jsonify({"status": "ok", "updated": update})
```

- [ ] **Step 4: Run tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_toggles.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit endpoint changes**

```bash
git checkout -b feat/memory-toggles
git add app/blueprints/memory_routes.py tests/unit/test_memory_routes_toggles.py
git commit -m "feat(memory): GET/POST /user/memory-toggles

Three boolean toggles (master, ref_v2, ref_mempalace) backing user
control over memory layers. Stored in users/{uid}/preferences/memory.
All default true; partial updates merge.

Tier-1 trust-contract PR 4/6."
```

### Task 4.2: chat_routes — gate retrieval + writes on toggles

**Files:**
- Modify: `app/blueprints/chat_routes.py` — read toggles once per request, gate the relevant hooks

- [ ] **Step 1: Add a helper to read toggles per request**

In `app/blueprints/chat_routes.py`, near the top of the file (after the existing imports), add:

```python
def _read_memory_toggles(user_id: str) -> dict:
    """Read user's memory toggle prefs once per request.

    Defaults all-true if doc missing. Errors fail-OPEN (return defaults)
    so a Firestore blip never accidentally turns memory off mid-request.
    """
    if not user_id:
        return {"master": True, "ref_v2": True, "ref_mempalace": True}
    try:
        snap = db.collection("users").document(user_id) \
                 .collection("preferences").document("memory").get()
        if not snap.exists:
            return {"master": True, "ref_v2": True, "ref_mempalace": True}
        data = snap.to_dict() or {}
        return {
            "master": bool(data.get("master", True)),
            "ref_v2": bool(data.get("ref_v2", True)),
            "ref_mempalace": bool(data.get("ref_mempalace", True)),
        }
    except Exception:
        logger.warning("memory toggle read failed; defaulting all-on", exc_info=True)
        return {"master": True, "ref_v2": True, "ref_mempalace": True}
```

- [ ] **Step 2: Read toggles at the top of the streaming handler**

In `chat_routes.py`, find the streaming chat handler (the function containing `_mp_active_injected` from PR #118 — search for `_mp_active_injected = False`). Just BEFORE that initialization, add:

```python
    # Read memory toggles once per request — gates retrieval + writes
    _mem_toggles = _read_memory_toggles(user_id) if user_id else {
        "master": True, "ref_v2": True, "ref_mempalace": True,
    }
    _mp_allowed = _mem_toggles["master"] and _mem_toggles["ref_mempalace"]
    _v2_allowed = _mem_toggles["master"] and _mem_toggles["ref_v2"]
```

- [ ] **Step 3: Gate the mempalace wake-up call on `_mp_allowed`**

Find the `_mp_mode = _mempalace_mode()` block (the existing wake-up gate from PR #118). Update the OUTER condition to also require `_mp_allowed`:

```python
    try:
        _mp_mode = _mempalace_mode()
        if _mp_mode != _MempalaceMode.OFF and user_id and _mp_allowed:
            # ... existing body unchanged ...
```

- [ ] **Step 4: Gate memory_v2 retrieval on `_v2_allowed`**

Find the existing memory_v2 retrieval block (search for `MemoryStoreV2` usage near line ~860-880). Wrap the retrieval call:

```python
    if not guest_mode and user_id and _v2_allowed:
        try:
            memory_session_ctx = SessionContext(...)
            # ... existing body unchanged
```

- [ ] **Step 5: Gate v2 extraction (post-turn) on `_v2_allowed`**

Find the post-turn memory_v2 extraction logic (where `memory_orchestrator_stream` runs). Add `_v2_allowed` to the gating condition.

- [ ] **Step 6: Gate the mempalace record_turn calls on `_mp_allowed`**

Find both record_turn call sites (user-half + assistant-half — search for `_mempalace_get_adapter().record_turn`). Wrap each in `if _mp_allowed:` (in addition to existing guards).

- [ ] **Step 7: Run existing chat tests for regressions**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/test_action_parser.py tests/test_mempalace_chat_scaffold_smoke.py -v
```

Expected: all pre-existing chat tests still pass.

- [ ] **Step 8: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(chat): gate retrieval + writes on user memory toggles

Reads users/{uid}/preferences/memory once per chat request. Computes:
- _mp_allowed = master AND ref_mempalace
- _v2_allowed = master AND ref_v2

Each existing memory hook (mempalace wake_up, mempalace record_turn,
memory_v2 retrieval, memory_v2 extraction) gates on the matching flag.

Fail-OPEN on Firestore blips — never accidentally turns memory off.

Tier-1 trust-contract PR 4/6."
```

### Task 4.3: Frontend toggle UI

**Files:**
- Modify: `app/templates/settings_api_keys.html` — insert toggle rows + JS

- [ ] **Step 1: Add toggle row markup**

In `settings_api_keys.html`, find the memory modal header block (around lines 316-336). After the `dream-toggle-row` block (which ends around line 336), add:

```html
<!-- Memory toggles (master + per-system) -->
<div class="memory-toggle-rows" style="padding: 0 1rem 0.75rem;">
    <div class="dream-toggle-row">
        <div class="dream-toggle-info">
            <span class="dream-toggle-label">Memory</span>
            <span class="dream-toggle-desc">Master switch — disables all memory recall + writes for your account</span>
        </div>
        <label class="dream-switch">
            <input type="checkbox" id="memory-toggle-master" checked>
            <span class="dream-switch-slider"></span>
        </label>
    </div>
    <div class="dream-toggle-row">
        <div class="dream-toggle-info">
            <span class="dream-toggle-label">Reference saved memories</span>
            <span class="dream-toggle-desc">Use facts the AI has learned about you (Profile, Preferences, etc.)</span>
        </div>
        <label class="dream-switch">
            <input type="checkbox" id="memory-toggle-ref-v2" checked>
            <span class="dream-switch-slider"></span>
        </label>
    </div>
    <div class="dream-toggle-row">
        <div class="dream-toggle-info">
            <span class="dream-toggle-label">Reference chat history</span>
            <span class="dream-toggle-desc">Use past conversation turns for personalized recall</span>
        </div>
        <label class="dream-switch">
            <input type="checkbox" id="memory-toggle-ref-mempalace" checked>
            <span class="dream-switch-slider"></span>
        </label>
    </div>
</div>
```

- [ ] **Step 2: Add JS to load + persist toggle state**

In the `<script>` block, near the `fetchConversationMemory` block, add:

```javascript
async function loadMemoryToggles() {
    try {
        const resp = await fetch('/user/memory-toggles', { credentials: 'same-origin' });
        if (!resp.ok) return;
        const t = await resp.json();
        document.getElementById('memory-toggle-master').checked = t.master !== false;
        document.getElementById('memory-toggle-ref-v2').checked = t.ref_v2 !== false;
        document.getElementById('memory-toggle-ref-mempalace').checked = t.ref_mempalace !== false;
    } catch (e) {
        console.warn('[memory-toggles] load failed:', e);
    }
}

async function persistMemoryToggle(field, value) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    try {
        const resp = await fetch('/user/memory-toggles', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({ [field]: value }),
        });
        if (!resp.ok) {
            // Revert UI optimistic state
            document.getElementById('memory-toggle-' + field.replace('_', '-')).checked = !value;
            alert('Could not save preference; please retry.');
        }
    } catch (e) {
        console.warn('[memory-toggles] persist failed:', e);
    }
}

document.getElementById('memory-toggle-master')?.addEventListener('change', (e) => {
    persistMemoryToggle('master', e.target.checked);
});
document.getElementById('memory-toggle-ref-v2')?.addEventListener('change', (e) => {
    persistMemoryToggle('ref_v2', e.target.checked);
});
document.getElementById('memory-toggle-ref-mempalace')?.addEventListener('change', (e) => {
    persistMemoryToggle('ref_mempalace', e.target.checked);
});
```

- [ ] **Step 3: Wire `loadMemoryToggles` into the modal-open handler**

Find the existing `openMemoryModal` (or similar — search for the function that opens the memory modal). Add a call to `loadMemoryToggles()` at the top:

```javascript
async function openMemoryModal() {
    // ...existing body...
    loadMemoryToggles();  // ADD THIS LINE
    // ...rest...
}
```

- [ ] **Step 4: Manual verify in browser**

Open Settings → Memory. Three toggles render at top, all checked. Toggle one off, refresh page, reopen modal — toggle stays off (Firestore round-trip works).

- [ ] **Step 5: Commit frontend toggle UI**

```bash
git add app/templates/settings_api_keys.html
git commit -m "feat(memory): toggle UI for master + per-system memory controls

Three switches in the memory modal:
- Master (overrides both)
- Reference saved memories (memory_v2)
- Reference chat history (mempalace)

Each persists to /user/memory-toggles on change. Optimistic UI with
revert on failure.

Tier-1 trust-contract PR 4/6."
```

- [ ] **Step 6: Push + open PR**

```bash
git push -u origin feat/memory-toggles
gh pr create --title "feat(memory): user-controlled memory toggles (master + per-system)" --body "Tier-1 trust-contract PR 4/6.

- New /user/memory-toggles GET/POST
- 3 toggle rows in memory modal
- chat_routes reads toggles per request and gates retrieval + writes

Spec: docs/superpowers/specs/2026-04-29-mempalace-trust-contract-design.md"
```

---

## PR 5 — Forget Me cascade

**Files:**
- Modify: `app/blueprints/memory_routes.py` — new endpoint `DELETE /user/forget-me` + cascade helpers
- Modify: `app/templates/settings_api_keys.html` — rename "Clear all" → "Forget Me" + new confirmation copy + new JS handler
- Test: `tests/unit/test_memory_routes_forget_me.py` (new)

**Effort:** ~half day

### Task 5.1: Backend Forget Me cascade endpoint (TDD)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_memory_routes_forget_me.py`:

```python
"""Tests for DELETE /user/forget-me — the multi-store cascade."""
from unittest.mock import patch, MagicMock


def test_forget_me_happy_cascade(authenticated_client, mock_firestore):
    """All five stores get wiped; chat_sessions stays intact."""
    with patch("app.blueprints.memory_routes._sidecar_delete_palace",
               return_value=True) as mock_palace, \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=12) as mock_outbox, \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               return_value=5) as mock_fs:
        resp = authenticated_client.delete("/user/forget-me")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["partial_failures"] == []
    mock_palace.assert_called_once_with("test_user_123")
    mock_outbox.assert_called_once_with("test_user_123")
    # Three subcollection wipes: memory_v2, memory_events, smart_suggestions
    assert mock_fs.call_count == 3


def test_forget_me_partial_failure_palace(authenticated_client, mock_firestore):
    """Sidecar palace-delete fails; Firestore + outbox still wiped."""
    def boom(*a, **kw):
        raise ConnectionError("sidecar down")
    with patch("app.blueprints.memory_routes._sidecar_delete_palace",
               side_effect=boom), \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=0), \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               return_value=0):
        resp = authenticated_client.delete("/user/forget-me")

    assert resp.status_code == 200  # Best-effort cascade — never abort
    body = resp.get_json()
    assert "palace" in body["partial_failures"]


def test_forget_me_does_not_touch_chat_sessions(authenticated_client, mock_firestore):
    """chat_sessions subcollection is NOT in the cascade — separate scope."""
    wiped = []
    def track_wipe(uid, subcol):
        wiped.append(subcol)
        return 0
    with patch("app.blueprints.memory_routes._sidecar_delete_palace",
               return_value=True), \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=0), \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               side_effect=track_wipe):
        authenticated_client.delete("/user/forget-me")
    assert "chat_sessions" not in wiped
    assert set(wiped) == {"memory_v2", "memory_events", "smart_suggestions"}
```

- [ ] **Step 2: Run tests to verify fail**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_forget_me.py -v
```

Expected: 3 failures.

- [ ] **Step 3: Implement Forget Me cascade**

Append to `app/blueprints/memory_routes.py`:

```python
import sqlite3 as _sqlite3


def _sidecar_delete_palace(user_id: str) -> bool:
    """Sidecar DELETE /palace/{user_id} — wipes whole palace."""
    url = f"{_sidecar_base_url()}/palace/{user_id}"
    resp = _requests.delete(
        url,
        headers={"X-Sidecar-Token": _sidecar_token()},
        timeout=10,
    )
    resp.raise_for_status()
    return True


def _wipe_outbox_for_user(user_id: str) -> int:
    """DELETE FROM outbox WHERE user_id = ?  → returns row count."""
    outbox_path = os.getenv("MEMPALACE_OUTBOX_PATH", "/var/lib/xelasphere/outbox.db")
    try:
        conn = _sqlite3.connect(outbox_path)
        try:
            cur = conn.execute("DELETE FROM outbox WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
    except Exception:
        logger.exception("forget-me: outbox wipe failed")
        raise


def _wipe_firestore_subcollection(user_id: str, subcollection: str) -> int:
    """Delete every doc in users/{user_id}/{subcollection}. Returns count."""
    from app.config import db
    coll = db.collection("users").document(user_id).collection(subcollection)
    deleted = 0
    while True:
        batch = list(coll.limit(500).stream())
        if not batch:
            break
        for d in batch:
            d.reference.delete()
            deleted += 1
        if len(batch) < 500:
            break
    return deleted


@memory_bp.route('/user/forget-me', methods=['DELETE'])
@error_handler
@login_required
@csrf_required
def forget_me():
    """Cascade-wipe the user's memory layer.

    Touches: mempalace palace, outbox, memory_v2, memory_events,
    smart_suggestions. Does NOT touch: chat_sessions, preferences,
    account doc, audit logs.

    Best-effort cascade — each step independent. Never aborts.
    """
    user_id = session['user']['uid']
    deleted = {}
    partial_failures = []

    # 1. Mempalace palace (sidecar)
    try:
        _sidecar_delete_palace(user_id)
        deleted["palace"] = "wiped"
    except Exception as exc:
        logger.warning("forget-me palace wipe failed: %s", exc)
        partial_failures.append("palace")

    # 2. Outbox (SQLite)
    try:
        deleted["outbox_rows"] = _wipe_outbox_for_user(user_id)
    except Exception:
        partial_failures.append("outbox")

    # 3. Firestore subcollections
    for subcol in ("memory_v2", "memory_events", "smart_suggestions"):
        try:
            deleted[f"{subcol}_docs"] = _wipe_firestore_subcollection(user_id, subcol)
        except Exception:
            logger.exception("forget-me %s wipe failed", subcol)
            partial_failures.append(subcol)

    return jsonify({
        "deleted": deleted,
        "partial_failures": partial_failures,
        "kept": ["chat_sessions", "preferences", "audit_logs", "account_data"],
    })
```

- [ ] **Step 4: Run tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_memory_routes_forget_me.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit backend cascade**

```bash
git checkout -b feat/forget-me-cascade
git add app/blueprints/memory_routes.py tests/unit/test_memory_routes_forget_me.py
git commit -m "feat(memory): DELETE /user/forget-me cascade

Wipes mempalace palace + outbox + memory_v2 + memory_events +
smart_suggestions. Best-effort: each step independent; partial
failures collected and reported. Never aborts mid-cascade.

Does NOT touch: chat_sessions, preferences, audit_logs, account doc.

Tier-1 trust-contract PR 5/6."
```

### Task 5.2: Frontend — rename Clear all → Forget Me + new confirmation

- [ ] **Step 1: Update modal footer button + confirmation modal copy**

In `settings_api_keys.html`, find the existing footer block:

```html
<button type="button" class="btn btn-danger" id="clear-all-memories">
    <i class="fas fa-trash"></i> Clear all
</button>
```

Replace label/id (or keep id for back-compat — I'll keep id but rename label):

```html
<button type="button" class="btn btn-danger" id="clear-all-memories">
    <i class="fas fa-eraser"></i> Forget Me
</button>
```

Find the existing confirm modal (around line 397):

```html
<h3>Clear all memories?</h3>
<p>This will permanently delete everything the AI has learned about you. This action cannot be undone.</p>
```

Replace with:

```html
<h3>Forget Me?</h3>
<p>This will permanently delete everything the AI has learned about you across all conversations.</p>
<div style="margin-top: 0.75rem; font-size: 0.85rem; color: #aaa;">
    <strong>Will be deleted:</strong>
    <ul style="margin: 0.3rem 0 0.6rem 1rem;">
        <li>All saved facts (Profile, Preferences, Projects, etc.)</li>
        <li>All conversation memory turns</li>
        <li>Smart suggestions cache</li>
    </ul>
    <strong>Will be kept:</strong>
    <ul style="margin: 0.3rem 0 0 1rem;">
        <li>Your chat history (delete separately if needed)</li>
        <li>Your account + preferences (memory toggles, etc.)</li>
    </ul>
</div>
<p style="margin-top: 0.5rem; font-style: italic; color: #888;">This action cannot be undone.</p>
```

- [ ] **Step 2: Update JS handler — call /user/forget-me instead of /user/memories**

Find the existing JS handler for the "Clear all" confirm button (search for `'/user/memories'` with `method: 'DELETE'`). Update the URL and the success-message language:

```javascript
// Was: fetch('/user/memories', { method: 'DELETE', ... })
// Now:
const resp = await fetch('/user/forget-me', {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': csrfToken },
});
if (resp.ok) {
    const result = await resp.json();
    // Show success message — note partial_failures if present
    const partial = result.partial_failures || [];
    if (partial.length === 0) {
        alert("Done. The AI has forgotten everything about you across all conversations.");
    } else {
        alert(`Mostly done — these stores couldn't be wiped right now: ${partial.join(', ')}. Try again in a moment.`);
    }
    // Refresh both memory lists
    fetchMemories();
    fetchConversationMemory('all');
}
```

- [ ] **Step 3: Manual verify**

Open Settings → Memory → click "Forget Me" → confirmation modal shows new copy → confirm → success toast → both Memories tab and Conversation memory tab refresh empty.

Verify with sidecar probe that palace is empty:

```bash
USER_ID="..."
TOKEN=$(sudo grep -oP 'MEMPALACE_TOKEN=\K\S+' /etc/mempalace/sidecar.env)
sudo curl -s --unix-socket /run/mempalace/sidecar.sock \
  "http://localhost/palace/${USER_ID}/drawers?limit=10" \
  -H "X-Sidecar-Token: $TOKEN" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('drawers:', len(d.get('drawers',[])))
"
```

- [ ] **Step 4: Commit frontend portion**

```bash
git add app/templates/settings_api_keys.html
git commit -m "feat(memory): rename Clear all → Forget Me + cascade-aware UI

- Footer button: 'Forget Me' with eraser icon
- Confirmation modal: explicit 'will be deleted' / 'will be kept' lists
- JS handler: calls /user/forget-me (cascade) instead of /user/memories (v2 only)
- Refreshes both Memories tab and Conversation memory tab post-success
- Surfaces partial_failures in the toast

Tier-1 trust-contract PR 5/6."
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/forget-me-cascade
gh pr create --title "feat(memory): Forget Me cascade — broadens existing Clear All" --body "Tier-1 trust-contract PR 5/6. See spec for context."
```

---

## PR 6 — Wire incognito to actually be temporary

**Files:**
- Modify: `app/blueprints/chat_routes.py` — `start_new_chat` temp_ session_id path; chat handler `is_temp_session` detection + skip_writes gates
- Test: `tests/unit/test_chat_routes_temp_mode.py` (new)

**Effort:** ~half day

**Approach:** The frontend is already fully wired. Sidebar ghost button calls `chatManager.createNewChat({temporary: true})`; visual treatment + banner already render. Backend currently writes to Firestore even for temp; this PR mirrors the `guest_mode` pattern so temp is truly ephemeral.

### Task 6.1: start_new_chat — temp_ session_id path (TDD)

**Files:**
- Modify: `app/blueprints/chat_routes.py:147` (`start_new_chat`)
- Create: `tests/unit/test_chat_routes_temp_mode.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_chat_routes_temp_mode.py`:

```python
"""Tests that wire the existing incognito flag to actually skip writes."""
from unittest.mock import patch, MagicMock


def test_chat_new_temp_returns_temp_prefixed_session_id(authenticated_client, mock_firestore):
    resp = authenticated_client.post(
        "/chat/new",
        json={"session_name": "test", "temporary": True},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["session_id"].startswith("temp_"), body
    assert body["status"] == "temporary"


def test_chat_new_temp_does_not_write_to_firestore(authenticated_client, mock_firestore):
    """A temporary session must never write a session document to Firestore.

    Mirror of the existing guest_mode behavior.
    """
    with patch("app.chat_session.create_session") as mock_create:
        resp = authenticated_client.post(
            "/chat/new",
            json={"session_name": "test", "temporary": True},
        )
    assert resp.status_code == 201
    mock_create.assert_not_called()


def test_chat_new_non_temp_still_writes_to_firestore(authenticated_client, mock_firestore):
    """Regression guard: non-temp path unchanged — still writes."""
    with patch("app.chat_session.create_session", return_value="session_xyz") as mock_create:
        resp = authenticated_client.post(
            "/chat/new",
            json={"session_name": "test", "temporary": False},
        )
    assert resp.status_code == 201
    mock_create.assert_called_once()
```

- [ ] **Step 2: Run tests to verify the temp ones fail**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_chat_routes_temp_mode.py -v
```

Expected: 2 of 3 fail (`test_chat_new_temp_returns_temp_prefixed_session_id`, `test_chat_new_temp_does_not_write_to_firestore`). Third passes already.

- [ ] **Step 3: Update start_new_chat — add temp_ branch**

In `app/blueprints/chat_routes.py`, find `start_new_chat` (line ~147). Replace the body after the guest_mode branch but BEFORE the regular create_session call:

```python
    # Guest mode: create ephemeral session (existing — unchanged)
    if is_guest():
        session_id = f"guest_{uuid.uuid4().hex[:16]}"
        session['current_session_id'] = session_id
        return jsonify({
            'message': 'Guest chat session created',
            'session_id': session_id,
            'status': 'guest'
        }), 201

    # Temporary chat: ephemeral, no Firestore write — mirrors guest_mode
    # (the existing temporary flag was previously a no-op on the backend
    # despite the frontend's "Incognito mode. No history saved" promise)
    if temporary:
        session_id = f"temp_{uuid.uuid4().hex[:16]}"
        session['current_session_id'] = session_id
        return jsonify({
            'message': 'Temporary chat session created',
            'session_id': session_id,
            'status': 'temporary',
        }), 201

    # Regular user: create persistent session (existing — unchanged)
    user_id = session['user']['uid']
    session_id = chat_session.create_session(user_id, session_name=session_name)
    session['current_session_id'] = session_id
    return jsonify({
        'message': 'Chat session created',
        'session_id': session_id,
        'status': 'active'
    }), 201
```

(Note: removed the `temporary=temporary` kwarg from `create_session` call since temp now takes the early return; non-temp users get the unchanged plain create_session call.)

- [ ] **Step 4: Run tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_chat_routes_temp_mode.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit start_new_chat fix**

```bash
git checkout -b fix/wire-incognito-actually-temporary
git add app/blueprints/chat_routes.py tests/unit/test_chat_routes_temp_mode.py
git commit -m "fix(chat): start_new_chat creates temp_ session for temporary chats

Mirror of guest_mode pattern. No Firestore session document written
when temporary=True. The existing incognito UI was lying — it's now
honest.

Tier-1 trust-contract PR 6/6 (1 of 2 commits)."
```

### Task 6.2: Chat handler — skip writes for temp_ session_id

**Files:**
- Modify: `app/blueprints/chat_routes.py` — chat handler (around line 544 where guest_mode is detected)

- [ ] **Step 1: Write failing test for skip-writes behavior**

Append to `tests/unit/test_chat_routes_temp_mode.py`:

```python
def test_chat_with_temp_session_skips_mempalace_record(authenticated_client, mock_firestore):
    """A POST /chat/stream with a temp_ session_id must not call
    mempalace adapter record_turn.
    """
    # Set the session_id in the Flask session before posting
    with authenticated_client.session_transaction() as sess:
        sess['current_session_id'] = "temp_abc123"

    with patch("app.blueprints.chat_routes._mempalace_get_adapter") as mock_adapter:
        # We just need to assert wake_up_sync + record_turn never called
        # The actual chat call may fail downstream; we only care about
        # the gating behavior.
        try:
            authenticated_client.post(
                "/chat/stream",
                json={"prompt": "hello", "model": "test", "session_id": "temp_abc123"},
            )
        except Exception:
            pass
        adapter_instance = mock_adapter.return_value
        adapter_instance.wake_up_sync.assert_not_called()
        adapter_instance.record_turn.assert_not_called()


def test_chat_with_regular_session_still_calls_mempalace(authenticated_client, mock_firestore):
    """Regression: non-temp session still calls adapter (before this PR's
    skip-writes logic, this was the only behavior; now we must keep it
    for regular sessions).
    """
    with authenticated_client.session_transaction() as sess:
        sess['current_session_id'] = "session_regular"

    with patch("app.blueprints.chat_routes._mempalace_get_adapter") as mock_adapter, \
         patch("app.blueprints.chat_routes._mempalace_mode") as mock_mode:
        from app.mempalace.feature_flag import MempalaceMode
        mock_mode.return_value = MempalaceMode.ACTIVE
        try:
            authenticated_client.post(
                "/chat/stream",
                json={"prompt": "hello", "model": "test", "session_id": "session_regular"},
            )
        except Exception:
            pass
        # adapter should have been instantiated and called
        # (we don't strictly assert call counts since downstream may fail;
        #  we assert it WAS called at least once)
        adapter_instance = mock_adapter.return_value
        # record_turn or wake_up_sync should be called at least once
        assert (adapter_instance.wake_up_sync.called
                or adapter_instance.record_turn.called)
```

- [ ] **Step 2: Run tests — temp test fails, regular test may pass**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_chat_routes_temp_mode.py::test_chat_with_temp_session_skips_mempalace_record -v
```

Expected: FAIL (currently no skip-writes logic for temp).

- [ ] **Step 3: Add `is_temp_session` detection alongside guest_mode**

In `app/blueprints/chat_routes.py`, find the existing `guest_mode = 'guest_id' in session and 'user' not in session` line (around 544). Add directly after:

```python
    is_temp_session = (
        isinstance(session_id, str) and session_id.startswith('temp_')
    )
    skip_writes = guest_mode or is_temp_session
```

(`session_id` should already be in scope at that point — it's the request's session_id. If the variable name differs in this function, use whatever holds the active session_id.)

- [ ] **Step 4: Update each `not guest_mode` write-gate to also exclude temp**

Search the chat handler for `not guest_mode` and replace with `not skip_writes` at the relevant write sites. Specifically:

- Firestore session message append (line ~628, ~639, ~862, ~2020, ~2084)
- memory_v2 extraction post-turn

Pattern:

```python
# Was:
if not guest_mode and user_id:
    # write something

# Now:
if not skip_writes and user_id:
    # write something
```

- [ ] **Step 5: Skip mempalace wake_up_sync and record_turn for temp**

Find the `if _mp_mode != _MempalaceMode.OFF and user_id` block (the wake-up gate from PR #118). Tighten the condition with `and not is_temp_session`:

```python
    if _mp_mode != _MempalaceMode.OFF and user_id and _mp_allowed and not is_temp_session:
        # ... existing wake_up + active injection body unchanged ...
```

For the record_turn call sites (search `_mempalace_get_adapter().record_turn`), wrap each with `if not skip_writes:`.

- [ ] **Step 6: Run all temp-mode tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/unit/test_chat_routes_temp_mode.py -v
```

Expected: 5 passed (3 from Task 6.1 + 2 from Task 6.2).

- [ ] **Step 7: Run full chat regression tests**

```bash
/opt/xelasphere/venv/bin/python -m pytest tests/test_action_parser.py tests/test_mempalace_chat_scaffold_smoke.py tests/test_mempalace_shadow_runtime.py -v 2>&1 | tail -10
```

Expected: all pre-existing chat tests still pass.

- [ ] **Step 8: Manual E2E in browser**

1. Click sidebar ghost icon → temp chat opens with dashed border + banner.
2. Send a message with personal info ("My favorite color is purple").
3. Verify response renders.
4. Open another tab, log in as same user, ask "what's my favorite color?" — AI does NOT recall purple (mempalace was never written).
5. Verify NO traces:

```bash
RILEY=zHaSm1p58mOs3eFnp6uDwZBOeZJ2  # or whatever user_id you tested with
sudo /opt/xelasphere/venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('/var/lib/xelasphere/outbox.db')
recent = conn.execute(\"SELECT user_id, role, ts FROM outbox WHERE user_id='$RILEY' ORDER BY ts DESC LIMIT 3\").fetchall()
print(recent)
"
# Should show NO rows from the temp conversation timestamp
```

- [ ] **Step 9: Commit**

```bash
git add app/blueprints/chat_routes.py tests/unit/test_chat_routes_temp_mode.py
git commit -m "fix(chat): wire incognito flag to actually skip writes

Mirror of existing guest_mode pattern: a temp_ session_id triggers
skip_writes which gates:
- mempalace wake_up_sync + record_turn (both halves)
- memory_v2 extraction
- Firestore session message append

The existing incognito UI was a Potemkin village (the greeting
'Incognito mode. No history saved.' was false). Now it's honest.

Manual E2E verified: ghost-mode chat → no outbox rows, no shadow JSONL,
no Firestore session doc.

Tier-1 trust-contract PR 6/6 (2 of 2 commits)."
```

- [ ] **Step 10: Push + open PR**

```bash
git push -u origin fix/wire-incognito-actually-temporary
gh pr create --title "fix(chat): wire incognito flag to actually skip writes" --body "Tier-1 trust-contract PR 6/6 (final).

The existing 'Incognito' UI shows 'No history saved.' but the backend
silently saves everything. This PR makes the promise true:

- start_new_chat with temporary=True returns a temp_ session_id (no
  Firestore session doc)
- Chat handler detects temp_ prefix and gates mempalace + memory_v2 +
  Firestore writes (mirrors existing guest_mode pattern)

Tested: 5 unit tests + manual E2E with browser ghost click.

Spec: docs/superpowers/specs/2026-04-29-mempalace-trust-contract-design.md"
```

---

## Self-Review

After writing the plan, fresh-eyes pass:

### Spec coverage check

Walk every spec section, confirm a task implements it:

| Spec section | Implemented in |
|---|---|
| Decision: two halves panel | Task 2.2 (panel markup), Task 2.3 (fetch + render), Task 2.1 (backend) |
| Decision: all-turns-with-role-labels unit | Task 2.1 + 2.3 (role badge in markup, role derivation in backend) |
| Decision: Forget Me memory-only scope | Task 5.1 (cascade body explicitly excludes chat_sessions) |
| Decision: temporary chat true ephemeral | Task 6.1 (no Firestore write) + Task 6.2 (skip all writes) |
| Decision: delete-chat copy edit only | Task 1.1 |
| Architecture file map (memory_routes.py +5 endpoints) | Tasks 2.1, 3.3, 4.1, 4.2, 5.1 — covers GET/DEL conversation-memory, GET/POST toggles, DEL forget-me. **5 endpoints. ✓** |
| Architecture (sidecar +1 endpoint) | Task 3.2 |
| Architecture (sidecar +1 method) | Task 3.1 |
| Memory modal extension | Tasks 2.2, 4.3, 5.2 |
| Per-mempalace-item delete | Tasks 3.1-3.4 |
| Forget Me cascade | Tasks 5.1-5.2 |
| Wire incognito | Tasks 6.1-6.2 |
| Toggle reads/writes | Tasks 4.1-4.3 |
| chat_routes temp gating | Tasks 4.2 + 6.2 |
| Error handling — sidecar 503 | Task 2.1 + 3.3 (test cases) |
| Error handling — partial Forget Me | Task 5.1 (test cases) |
| Testing — unit tests for each blueprint touchpoint | Tasks 2.1, 3.1, 3.2, 3.3, 4.1, 5.1, 6.1, 6.2 (each has TDD red-green) |
| Sidecar tests | Tasks 3.1, 3.2 |
| E2E manual checklist | Embedded in PR-merge steps (Tasks 2.3 step 5, 3.4 step 2, 5.2 step 3, 6.2 step 8) |

All spec items covered. ✓

### Placeholder scan

`grep -nE "TBD|TODO|XXX|FIXME|implement later|fill in"` against this plan: clean. ✓

### Type / API consistency

Endpoint names referenced across tasks are consistent:
- `/user/conversation-memory` (GET, Task 2.1; DELETE, Task 3.3)
- `/user/memory-toggles` (GET + POST, Task 4.1)
- `/user/forget-me` (DELETE, Task 5.1)
- `/palace/{user_id}/drawers/{drawer_id}` (Task 3.2)
- `delete_drawer` method name consistent across Tasks 3.1, 3.2, 3.3 ✓

Helper function names (`_sidecar_list_drawers`, `_sidecar_delete_drawer`, `_sidecar_delete_palace`, `_wipe_outbox_for_user`, `_wipe_firestore_subcollection`, `_toggle_doc_ref`, `_read_memory_toggles`) all defined exactly once and referenced consistently. ✓

Test fixture names (`authenticated_client`, `mock_firestore`) match the existing `tests/conftest.py` patterns. ✓

### PR ordering — do later PRs depend on earlier ones?

- PR 1: standalone — no dep
- PR 2: standalone backend; the per-row delete buttons in markup are unwired until PR 3, marked `disabled` in the markup until then (delete button just doesn't function — falls through silently)
- PR 3: depends on PR 2 (the conversation-memory tab existing). If they ship in any order other than 2 → 3, PR 3's frontend-wire step is a no-op until PR 2 lands.
- PR 4 (toggles): independent of others; can ship before or after PRs 2-3
- PR 5 (Forget Me): depends on PR 2 + PR 3 for the "refresh both lists" behavior in the success handler. Will degrade gracefully if conversation-memory tab doesn't exist (the function call simply finds no element and no-ops).
- PR 6: independent

Recommend ship order: 1 → 2 → 3 → 4 → 5 → 6. Total ~3.5-4 days.

---

## Acceptance criteria (Week 1 done)

User-facing:
- [ ] Settings → Memory shows two sections: facts (memory_v2) + conversation memory (mempalace turns).
- [ ] Each individual conversation turn can be deleted.
- [ ] Each individual fact can be deleted (already worked, regression-test).
- [ ] "Forget Me" wipes the memory layer; chat history sidebar persists.
- [ ] Three toggles (master, ref_v2, ref_mempalace) take effect on the next chat.
- [ ] Incognito ghost click produces a true ephemeral conversation — no traces in any persistent store.
- [ ] "Delete chat" copy explicitly mentions "Forget Me" for the broader scope.

Engineering:
- [ ] All new unit tests pass.
- [ ] Existing memory_v2 panel functionality (pin, dream activity, undo) still works.
- [ ] Existing guest_mode chat path still works (regression-protected by Task 6.2 step 7).
- [ ] Sidecar test suite still passes.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-29-mempalace-trust-contract.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. The 6 PRs become 6 subagent dispatches; I verify each PR builds on the last cleanly before launching the next.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster context flow but my context fills up across the multi-PR scope.

Which approach?

# Chats Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dedicated `/chats` page with full-feature chat browsing — search, filters, sort, star/pin, bulk operations — modeled on `claude.ai/chats`.

**Architecture:** Hybrid load. Backend returns flat metadata array on first load; client filter/sort/search runs in-memory for instant feedback; mutations round-trip with optimistic UI updates. Lazy backfill of new denormalized fields.

**Tech Stack:** Flask + Jinja templates, vanilla JS module, plain CSS. Firestore for persistence. pytest with `mock_firestore` and `authenticated_client` fixtures.

**Spec:** `docs/superpowers/specs/2026-05-03-chats-page-design.md`

---

## File Map

**Created:**
- `app/templates/chats.html` — page template
- `app/static/css/chats.css` — page styles (desktop + mobile)
- `app/static/js/chatsPage.js` — frontend module (state, fetch, filter/sort/render, mutations)
- `tests/unit/test_chats_endpoints.py` — backend endpoint tests

**Modified:**
- `app/blueprints/pages_routes.py` — add `GET /chats` route
- `app/blueprints/session_routes.py` — add `GET /chat/list-metadata`, `PATCH /chat/<session_id>`, `POST /chat/bulk-delete`
- `app/chat_session.py` — add `list_metadata()`, `update_session_metadata()`, `bulk_delete_sessions()` helpers
- `app/templates/index.html` — wire `#action-chats` button to `/chats`

---

## Task 1: Backend — `list_metadata()` helper on ChatSession

**Files:**
- Modify: `app/chat_session.py`
- Test: `tests/unit/test_chats_endpoints.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chats_endpoints.py`:

```python
"""Tests for /chats page backend endpoints."""
from unittest.mock import patch, MagicMock


def _make_session_doc(session_id="session_1", **overrides):
    """Helper: build a Firestore session doc snapshot."""
    base = {
        "title": "Test chat",
        "last_updated": "2026-05-03T10:00:00",
        "created_at": "2026-05-01T10:00:00",
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hi"},
                     {"role": "assistant", "content": "hello"}],
        "starred": False,
        "pinned": False,
        "tags": [],
        "msg_count": 2,
        "has_artifact": False,
        "has_attachment": False,
    }
    base.update(overrides)
    snap = MagicMock()
    snap.id = session_id
    snap.to_dict.return_value = base
    snap.exists = True
    return snap


def test_list_metadata_returns_flat_array(authenticated_client, mock_firestore):
    """GET /chat/list-metadata returns sessions metadata as flat array."""
    sessions = [_make_session_doc(session_id="session_a", title="Chat A"),
                _make_session_doc(session_id="session_b", title="Chat B")]
    with patch("app.chat_session.ChatSession.list_metadata",
               return_value=[s.to_dict() | {"session_id": s.id} for s in sessions]):
        resp = authenticated_client.get("/chat/list-metadata")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "sessions" in body
    assert len(body["sessions"]) == 2
    assert body["sessions"][0]["session_id"] == "session_a"
    assert body["sessions"][0]["title"] == "Chat A"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/xelas.io/xelasphere.io-
python -m pytest tests/unit/test_chats_endpoints.py::test_list_metadata_returns_flat_array -v
```
Expected: FAIL with route not found (404) or AttributeError on `list_metadata`.

- [ ] **Step 3: Add `list_metadata()` helper to `ChatSession` class**

In `app/chat_session.py`, add this method to the `ChatSession` class:

```python
def list_metadata(self, user_id: str) -> List[Dict[str, Any]]:
    """Return flat metadata for ALL sessions of a user — no message bodies.

    Used by the /chats page for client-side filter/sort/search.
    Lazy backfills msg_count / has_artifact / has_attachment if missing.
    """
    sessions_ref = (
        self.db.collection('users').document(user_id)
        .collection('sessions')
        .order_by('last_updated', direction=gc_firestore.Query.DESCENDING)
    )
    out: List[Dict[str, Any]] = []
    for snap in sessions_ref.stream():
        data = snap.to_dict() or {}
        # Skip soft-deleted or invalid docs
        if data.get('deleted'):
            continue

        # Backfill denormalized fields if missing
        msg_count = data.get('msg_count')
        has_artifact = data.get('has_artifact')
        has_attachment = data.get('has_attachment')

        needs_backfill = (
            msg_count is None or has_artifact is None or has_attachment is None
        )
        if needs_backfill:
            messages = data.get('messages', []) or []
            msg_count = len(messages)
            has_artifact = any(m.get('artifact') for m in messages)
            has_attachment = any(m.get('attachments') for m in messages)
            try:
                snap.reference.update({
                    'msg_count': msg_count,
                    'has_artifact': bool(has_artifact),
                    'has_attachment': bool(has_attachment),
                })
            except Exception:
                # Backfill is best-effort; missing fields stay missing on next read
                pass

        out.append({
            'session_id': snap.id,
            'title': data.get('title') or 'New chat',
            'last_updated': data.get('last_updated') or data.get('created_at') or '',
            'model': data.get('model') or '',
            'msg_count': int(msg_count or 0),
            'starred': bool(data.get('starred', False)),
            'pinned': bool(data.get('pinned', False)),
            'tags': list(data.get('tags', []) or []),
            'has_artifact': bool(has_artifact),
            'has_attachment': bool(has_attachment),
        })
    return out
```

- [ ] **Step 4: Add the route `GET /chat/list-metadata` to `app/blueprints/session_routes.py`**

Add this route (placement: anywhere in the file, near other GET routes):

```python
@session_bp.route('/chat/list-metadata', methods=['GET'])
def chat_list_metadata():
    """Return flat metadata array of all sessions for the current user.

    Used by the /chats page. No pagination, no message bodies.
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    chat_session = ChatSession(db)
    sessions = chat_session.list_metadata(user_id)
    response = jsonify({'sessions': sessions})
    response.headers['Cache-Control'] = 'no-store'
    return response
```

(Verify the existing imports at top of file include `session`, `jsonify`, `db`, `ChatSession` — they likely already do; if not, add them.)

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/unit/test_chats_endpoints.py::test_list_metadata_returns_flat_array -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/chat_session.py app/blueprints/session_routes.py tests/unit/test_chats_endpoints.py
git commit -m "feat(chats): add /chat/list-metadata endpoint with lazy denorm backfill"
```

---

## Task 2: Backend — `update_session_metadata()` + `PATCH /chat/<session_id>`

**Files:**
- Modify: `app/chat_session.py`, `app/blueprints/session_routes.py`
- Test: `tests/unit/test_chats_endpoints.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_chats_endpoints.py`:

```python
def test_patch_session_updates_starred(authenticated_client, mock_firestore):
    """PATCH /chat/<session_id> with {starred: true} updates the field."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.chat_session.ChatSession.update_session_metadata",
               return_value=True) as mock_update:
        resp = authenticated_client.patch(
            "/chat/session_abc",
            json={"starred": True},
            headers=headers,
        )
    assert resp.status_code == 204, resp.data
    mock_update.assert_called_once()
    args, _ = mock_update.call_args
    # call signature: (user_id, session_id, fields_dict)
    assert args[1] == "session_abc"
    assert args[2] == {"starred": True}


def test_patch_session_rejects_unknown_fields(authenticated_client, mock_firestore):
    """PATCH ignores unknown fields (whitelist enforced server-side)."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.chat_session.ChatSession.update_session_metadata",
               return_value=True) as mock_update:
        resp = authenticated_client.patch(
            "/chat/session_abc",
            json={"starred": True, "user_id": "evil_overwrite"},
            headers=headers,
        )
    assert resp.status_code == 204
    args, _ = mock_update.call_args
    assert "user_id" not in args[2]
    assert args[2] == {"starred": True}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_chats_endpoints.py -v -k patch
```
Expected: FAIL — route does not exist.

- [ ] **Step 3: Add `update_session_metadata()` helper to `ChatSession`**

Append to `app/chat_session.py`:

```python
ALLOWED_PATCH_FIELDS = {'starred', 'pinned', 'title', 'tags'}


def update_session_metadata(
    self, user_id: str, session_id: str, fields: Dict[str, Any]
) -> bool:
    """Update whitelisted metadata fields on a session document.

    Returns True on success, False if session not found.
    Silently ignores fields outside the whitelist (defense in depth).
    """
    cleaned = {k: v for k, v in fields.items() if k in ALLOWED_PATCH_FIELDS}
    if not cleaned:
        return True  # no-op
    ref = (self.db.collection('users').document(user_id)
           .collection('sessions').document(session_id))
    snap = ref.get()
    if not snap.exists:
        return False
    ref.update(cleaned)
    return True
```

- [ ] **Step 4: Add the `PATCH /chat/<session_id>` route**

Append to `app/blueprints/session_routes.py`:

```python
@session_bp.route('/chat/<session_id>', methods=['PATCH'])
def chat_patch_session(session_id):
    """Update starred / pinned / title / tags on a session.

    Body: any subset of {starred, pinned, title, tags}.
    Other fields are silently ignored (whitelist in ChatSession).
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'error': 'Invalid body'}), 400
    chat_session = ChatSession(db)
    ok = chat_session.update_session_metadata(user_id, session_id, body)
    if not ok:
        return jsonify({'error': 'Session not found'}), 404
    return ('', 204)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_chats_endpoints.py -v -k patch
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/chat_session.py app/blueprints/session_routes.py tests/unit/test_chats_endpoints.py
git commit -m "feat(chats): add PATCH /chat/<session_id> for star/pin/title/tags"
```

---

## Task 3: Backend — `bulk_delete_sessions()` + `POST /chat/bulk-delete`

**Files:**
- Modify: `app/chat_session.py`, `app/blueprints/session_routes.py`
- Test: `tests/unit/test_chats_endpoints.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_chats_endpoints.py`:

```python
def test_bulk_delete_removes_all_specified_sessions(authenticated_client, mock_firestore):
    """POST /chat/bulk-delete with list of ids deletes them all."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.chat_session.ChatSession.bulk_delete_sessions",
               return_value=3) as mock_del:
        resp = authenticated_client.post(
            "/chat/bulk-delete",
            json={"session_ids": ["s1", "s2", "s3"]},
            headers=headers,
        )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body == {"deleted": 3}
    mock_del.assert_called_once()


def test_bulk_delete_rejects_empty_list(authenticated_client, mock_firestore):
    """Empty session_ids → 400."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None):
        resp = authenticated_client.post(
            "/chat/bulk-delete",
            json={"session_ids": []},
            headers=headers,
        )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_chats_endpoints.py -v -k bulk_delete
```
Expected: FAIL — route does not exist.

- [ ] **Step 3: Add `bulk_delete_sessions()` helper**

Append to `app/chat_session.py`:

```python
def bulk_delete_sessions(self, user_id: str, session_ids: List[str]) -> int:
    """Delete multiple sessions in a single Firestore batch.

    Returns count of sessions deleted. Sessions that do not exist
    are silently skipped (the count reflects only existing ones).
    """
    if not session_ids:
        return 0
    batch = self.db.batch()
    deleted = 0
    for session_id in session_ids:
        ref = (self.db.collection('users').document(user_id)
               .collection('sessions').document(session_id))
        snap = ref.get()
        if snap.exists:
            batch.delete(ref)
            deleted += 1
    if deleted > 0:
        batch.commit()
    return deleted
```

- [ ] **Step 4: Add the `POST /chat/bulk-delete` route**

Append to `app/blueprints/session_routes.py`:

```python
@session_bp.route('/chat/bulk-delete', methods=['POST'])
def chat_bulk_delete():
    """Delete multiple sessions in a single batched operation.

    Body: {"session_ids": ["...", "..."]}
    Returns: {"deleted": <count>}
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    body = request.get_json(silent=True) or {}
    session_ids = body.get('session_ids')
    if not isinstance(session_ids, list) or not session_ids:
        return jsonify({'error': 'session_ids required (non-empty list)'}), 400
    if not all(isinstance(s, str) for s in session_ids):
        return jsonify({'error': 'session_ids must be strings'}), 400
    chat_session = ChatSession(db)
    count = chat_session.bulk_delete_sessions(user_id, session_ids)
    return jsonify({'deleted': count})
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/unit/test_chats_endpoints.py -v
```
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add app/chat_session.py app/blueprints/session_routes.py tests/unit/test_chats_endpoints.py
git commit -m "feat(chats): add POST /chat/bulk-delete (single batched Firestore commit)"
```

---

## Task 4: Page route + sidebar wiring

**Files:**
- Modify: `app/blueprints/pages_routes.py`, `app/templates/index.html`
- Create: `app/templates/chats.html`
- Test: manual smoke (browser)

- [ ] **Step 1: Add the `GET /chats` route**

In `app/blueprints/pages_routes.py`, near the `/images` route:

```python
@pages_bp.route('/chats', methods=['GET'])
def chats_page():
    """Render the chats browser page (full-feature chat list)."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_routes.login'))
    return render_template('chats.html')
```

(Verify imports include `render_template`, `session`, `redirect`, `url_for` — they likely already do; add if missing.)

- [ ] **Step 2: Create `app/templates/chats.html` with minimal skeleton**

```html
{% extends "base.html" %}
{% block title %}Chats — XelaSphere{% endblock %}
{% block content %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/chats.css') }}?v=1">

<main class="chats-page" id="chats-page">
    <header class="chats-page__header">
        <h1 class="chats-page__title">Chats</h1>
        <div class="chats-page__search">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="11" cy="11" r="8"></circle>
                <path d="M21 21l-4.35-4.35"></path>
            </svg>
            <input type="search" id="chats-search" placeholder="Search your chats" aria-label="Search chats">
        </div>
    </header>

    <div class="chats-page__toolbar">
        <div class="chats-page__filters" id="chats-filters">
            <button class="chats-chip" data-filter="starred" aria-pressed="false">★ Starred</button>
            <select class="chats-chip chats-chip--select" id="chats-filter-model" aria-label="Filter by model">
                <option value="">All models</option>
            </select>
            <button class="chats-chip" data-filter="hasArtifact" aria-pressed="false">Has artifact</button>
            <button class="chats-chip" data-filter="hasAttachment" aria-pressed="false">Has attachment</button>
        </div>
        <select class="chats-sort" id="chats-sort" aria-label="Sort by">
            <option value="recent" selected>Recent</option>
            <option value="oldest">Oldest</option>
            <option value="alpha">A–Z</option>
            <option value="msg_count">Most messages</option>
            <option value="starred_first">Starred first</option>
        </select>
    </div>

    <div class="chats-page__bulk-toolbar" id="chats-bulk-toolbar" hidden>
        <span class="chats-bulk__count" id="chats-bulk-count">0 selected</span>
        <button type="button" class="chats-bulk__btn" id="chats-bulk-star">Star</button>
        <button type="button" class="chats-bulk__btn chats-bulk__btn--danger" id="chats-bulk-delete">Delete</button>
        <button type="button" class="chats-bulk__btn" id="chats-bulk-cancel">Cancel</button>
    </div>

    <ul class="chats-page__list" id="chats-list" role="list"></ul>

    <div class="chats-page__empty" id="chats-empty" hidden>
        <p>No chats match your filters.</p>
        <button type="button" class="chats-empty__clear" id="chats-empty-clear">Clear filters</button>
    </div>
</main>

<script src="{{ url_for('static', filename='js/chatsPage.js') }}?v=1"></script>
{% endblock %}
```

- [ ] **Step 3: Wire the sidebar `#action-chats` button**

In `app/templates/index.html`, find the `#action-chats` button (around line 444). Add an `onclick` attribute (simplest), or add a JS listener in the appropriate handler module.

Edit the button line to:

```html
<button class="sidebar-action-btn" id="action-chats" aria-label="Chats" data-tooltip="Chats" onclick="window.location.href='/chats'">
```

- [ ] **Step 4: Verify the page loads**

Restart the service (after deploying):
```bash
sudo systemctl restart xelasphere
```
Open `https://your-host/chats` in a browser. Expected:
- Page loads (no 404, no 500)
- Header says "Chats"
- Empty list shown (no JS yet so nothing renders)
- No console errors except maybe `chatsPage.js 404` (file not yet created)

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/pages_routes.py app/templates/chats.html app/templates/index.html
git commit -m "feat(chats): add /chats page route + template skeleton + sidebar wiring"
```

---

## Task 5: CSS — chats.css desktop styles

**Files:**
- Create: `app/static/css/chats.css`

- [ ] **Step 1: Write the full desktop CSS**

Create `app/static/css/chats.css`:

```css
/* /chats page — desktop styles. Mobile rules in mobile.css. */

.chats-page {
    max-width: 960px;
    margin: 0 auto;
    padding: 32px 28px 48px;
    color: var(--text-primary, #e8e8f0);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}

/* ── Header ─────────────────────────────────────────────────────── */
.chats-page__header {
    display: flex;
    flex-direction: column;
    gap: 18px;
    margin-bottom: 18px;
}

.chats-page__title {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0;
}

.chats-page__search {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
    background: var(--bg-elevated, #1a1a22);
    border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
    border-radius: 12px;
    transition: border-color 0.15s ease;
}

.chats-page__search:focus-within {
    border-color: var(--accent-muted, rgba(124, 106, 239, 0.4));
}

.chats-page__search svg {
    color: var(--text-tertiary, #5a5a70);
    flex-shrink: 0;
}

.chats-page__search input {
    flex: 1;
    border: none;
    background: transparent;
    color: var(--text-primary, #e8e8f0);
    font-size: 15px;
    outline: none;
    padding: 0;
}

.chats-page__search input::placeholder {
    color: var(--text-tertiary, #5a5a70);
}

/* ── Toolbar (filters + sort) ──────────────────────────────────── */
.chats-page__toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;
}

.chats-page__filters {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}

.chats-chip {
    background: transparent;
    color: var(--text-secondary, #9898b0);
    border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
    border-radius: 999px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
    font-family: inherit;
}

.chats-chip:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.05));
    color: var(--text-primary, #e8e8f0);
}

.chats-chip[aria-pressed="true"] {
    background: var(--accent-muted, rgba(124, 106, 239, 0.15));
    color: var(--text-primary, #e8e8f0);
    border-color: var(--accent-muted, rgba(124, 106, 239, 0.4));
}

.chats-chip--select {
    appearance: none;
    -webkit-appearance: none;
    padding-right: 28px;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239898b0' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
    background-repeat: no-repeat;
    background-position: right 8px center;
    background-size: 12px;
}

.chats-sort {
    appearance: none;
    -webkit-appearance: none;
    background: transparent;
    color: var(--text-secondary, #9898b0);
    border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
    border-radius: 8px;
    padding: 6px 28px 6px 12px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    font-family: inherit;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239898b0' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
    background-repeat: no-repeat;
    background-position: right 8px center;
    background-size: 12px;
}

/* ── Bulk toolbar ─────────────────────────────────────────────── */
.chats-page__bulk-toolbar {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    margin-bottom: 12px;
    background: var(--bg-elevated, #1a1a22);
    border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

.chats-bulk__count {
    font-size: 14px;
    font-weight: 500;
    color: var(--text-primary, #e8e8f0);
    margin-right: auto;
}

.chats-bulk__btn {
    background: transparent;
    color: var(--text-primary, #e8e8f0);
    border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    font-family: inherit;
    transition: background 0.12s ease;
}

.chats-bulk__btn:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.05));
}

.chats-bulk__btn--danger {
    color: #f87171;
    border-color: rgba(248, 113, 113, 0.3);
}

.chats-bulk__btn--danger:hover {
    background: rgba(248, 113, 113, 0.1);
}

/* ── List + rows ──────────────────────────────────────────────── */
.chats-page__list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
}

.chats-row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 16px;
    border-radius: 10px;
    cursor: pointer;
    transition: background 0.12s ease;
}

.chats-row:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.04));
}

.chats-row__check {
    flex-shrink: 0;
    width: 18px;
    height: 18px;
    opacity: 0;
    transition: opacity 0.12s ease;
    cursor: pointer;
}

.chats-row:hover .chats-row__check,
.chats-row.is-selected .chats-row__check,
.chats-page.in-select-mode .chats-row__check {
    opacity: 1;
}

.chats-row__star {
    flex-shrink: 0;
    background: none;
    border: none;
    padding: 4px;
    cursor: pointer;
    color: var(--text-tertiary, #5a5a70);
    line-height: 0;
    transition: color 0.12s ease;
    font-family: inherit;
}

.chats-row__star:hover {
    color: var(--text-primary, #e8e8f0);
}

.chats-row__star.is-starred {
    color: #fbbf24;
}

.chats-row__title {
    flex: 1;
    min-width: 0;
    font-size: 15px;
    font-weight: 500;
    color: var(--text-primary, #e8e8f0);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.chats-row__meta {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
    font-size: 12px;
    color: var(--text-tertiary, #5a5a70);
}

.chats-row__model {
    font-variant: small-caps;
    letter-spacing: 0.04em;
}

.chats-row__indicators {
    display: inline-flex;
    gap: 6px;
}

.chats-row__indicator {
    color: var(--text-tertiary, #5a5a70);
    line-height: 0;
}

.chats-row__time {
    flex-shrink: 0;
    font-size: 12px;
    color: var(--text-tertiary, #5a5a70);
    font-variant-numeric: tabular-nums;
    min-width: 64px;
    text-align: right;
}

.chats-row__actions {
    flex-shrink: 0;
    opacity: 0;
    transition: opacity 0.12s ease;
}

.chats-row:hover .chats-row__actions {
    opacity: 1;
}

.chats-row__kebab {
    background: none;
    border: none;
    padding: 4px 8px;
    cursor: pointer;
    color: var(--text-tertiary, #5a5a70);
    border-radius: 6px;
    line-height: 0;
    font-family: inherit;
}

.chats-row__kebab:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.05));
    color: var(--text-primary, #e8e8f0);
}

/* ── Empty state ──────────────────────────────────────────────── */
.chats-page__empty {
    text-align: center;
    padding: 48px 16px;
    color: var(--text-secondary, #9898b0);
}

.chats-page__empty p {
    margin: 0 0 16px;
    font-size: 15px;
}

.chats-empty__clear {
    background: transparent;
    color: var(--accent-muted, rgba(124, 106, 239, 0.9));
    border: none;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    text-decoration: underline;
    font-family: inherit;
}

/* ── Loading skeleton ─────────────────────────────────────────── */
.chats-row.is-skeleton {
    pointer-events: none;
}

.chats-row.is-skeleton .chats-row__title,
.chats-row.is-skeleton .chats-row__meta,
.chats-row.is-skeleton .chats-row__time {
    background: var(--bg-hover, rgba(255, 255, 255, 0.05));
    color: transparent;
    border-radius: 4px;
    animation: chats-skeleton-pulse 1.5s ease-in-out infinite;
}

@keyframes chats-skeleton-pulse {
    0%, 100% { opacity: 0.6; }
    50%      { opacity: 1; }
}
```

- [ ] **Step 2: Verify CSS loads in browser**

Hard reload `/chats`. Expected:
- Header has 28px bold title "Chats"
- Search input is rounded with 12px radius, border on focus changes color
- Filter chips are pill-shaped
- No JS errors (page is still empty, but the chrome looks right)

- [ ] **Step 3: Commit**

```bash
git add app/static/css/chats.css
git commit -m "feat(chats): page CSS — desktop styles"
```

---

## Task 6: Frontend — initial load + render (`chatsPage.js`)

**Files:**
- Create: `app/static/js/chatsPage.js`

- [ ] **Step 1: Create `chatsPage.js` with state, fetch, render**

Create `app/static/js/chatsPage.js`:

```javascript
// /chats page — full-feature chat browser.
// State: in-memory + sessionStorage cache.
// Filter/sort/search: all client-side against cached array.
// Mutations: optimistic UI + server roundtrip.

(function () {
    'use strict';

    const CACHE_KEY = 'chats-page-cache';
    const CACHE_TTL_MS = 60_000;

    const state = {
        sessions: [],
        search: '',
        filters: {
            starred: false,
            model: '',
            hasArtifact: false,
            hasAttachment: false,
        },
        sort: 'recent',
        selected: new Set(),
        loading: false,
        error: null,
    };

    const els = {
        list: document.getElementById('chats-list'),
        empty: document.getElementById('chats-empty'),
        search: document.getElementById('chats-search'),
        filterModel: document.getElementById('chats-filter-model'),
        filterChips: document.querySelectorAll('.chats-chip[data-filter]'),
        sort: document.getElementById('chats-sort'),
        bulkToolbar: document.getElementById('chats-bulk-toolbar'),
        bulkCount: document.getElementById('chats-bulk-count'),
        bulkStar: document.getElementById('chats-bulk-star'),
        bulkDelete: document.getElementById('chats-bulk-delete'),
        bulkCancel: document.getElementById('chats-bulk-cancel'),
        emptyClear: document.getElementById('chats-empty-clear'),
    };

    /* ─── cache ───────────────────────────────────────────────── */

    function loadCache() {
        try {
            const raw = sessionStorage.getItem(CACHE_KEY);
            if (!raw) return null;
            const obj = JSON.parse(raw);
            if (!obj || typeof obj !== 'object') return null;
            if (Date.now() - obj.cachedAt > CACHE_TTL_MS) return null;
            return obj.sessions || null;
        } catch (_) { return null; }
    }

    function saveCache(sessions) {
        try {
            sessionStorage.setItem(CACHE_KEY, JSON.stringify({
                sessions, cachedAt: Date.now(),
            }));
        } catch (_) { /* quota exceeded — ignore */ }
    }

    function invalidateCache() {
        try { sessionStorage.removeItem(CACHE_KEY); } catch (_) {}
    }

    /* ─── fetch ───────────────────────────────────────────────── */

    async function fetchSessions() {
        state.loading = true;
        renderSkeleton();
        try {
            const resp = await fetch('/chat/list-metadata', {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const body = await resp.json();
            state.sessions = body.sessions || [];
            state.error = null;
            saveCache(state.sessions);
        } catch (err) {
            state.error = err.message || 'Failed to load chats';
            state.sessions = [];
        } finally {
            state.loading = false;
            populateModelFilter();
            render();
        }
    }

    function populateModelFilter() {
        const models = Array.from(new Set(
            state.sessions.map(s => s.model).filter(Boolean)
        )).sort();
        const current = els.filterModel.value;
        els.filterModel.innerHTML = '<option value="">All models</option>' +
            models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
        if (models.includes(current)) els.filterModel.value = current;
    }

    /* ─── filter / sort ───────────────────────────────────────── */

    function getVisibleSessions() {
        const q = state.search.trim().toLowerCase();
        let filtered = state.sessions.filter(s => {
            if (state.filters.starred && !s.starred) return false;
            if (state.filters.model && s.model !== state.filters.model) return false;
            if (state.filters.hasArtifact && !s.has_artifact) return false;
            if (state.filters.hasAttachment && !s.has_attachment) return false;
            if (q && !(s.title || '').toLowerCase().includes(q)) return false;
            return true;
        });
        switch (state.sort) {
            case 'oldest':
                filtered.sort((a, b) => (a.last_updated || '').localeCompare(b.last_updated || ''));
                break;
            case 'alpha':
                filtered.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
                break;
            case 'msg_count':
                filtered.sort((a, b) => (b.msg_count || 0) - (a.msg_count || 0));
                break;
            case 'starred_first':
                filtered.sort((a, b) => {
                    if (a.starred !== b.starred) return a.starred ? -1 : 1;
                    return (b.last_updated || '').localeCompare(a.last_updated || '');
                });
                break;
            case 'recent':
            default:
                filtered.sort((a, b) => (b.last_updated || '').localeCompare(a.last_updated || ''));
        }
        return filtered;
    }

    /* ─── render ──────────────────────────────────────────────── */

    function render() {
        const visible = getVisibleSessions();
        if (visible.length === 0) {
            els.list.innerHTML = '';
            els.empty.hidden = false;
            return;
        }
        els.empty.hidden = true;
        els.list.innerHTML = visible.map(renderRow).join('');
        attachRowListeners();
    }

    function renderSkeleton() {
        els.empty.hidden = true;
        const skeletonCount = 6;
        const rows = [];
        for (let i = 0; i < skeletonCount; i++) {
            rows.push(`
                <li class="chats-row is-skeleton">
                    <span class="chats-row__check"></span>
                    <span class="chats-row__star">★</span>
                    <span class="chats-row__title">Loading title placeholder text</span>
                    <span class="chats-row__meta">model · 0 msgs</span>
                    <span class="chats-row__time">—</span>
                </li>
            `);
        }
        els.list.innerHTML = rows.join('');
    }

    function renderRow(s) {
        const isSelected = state.selected.has(s.session_id);
        return `
            <li class="chats-row${isSelected ? ' is-selected' : ''}" data-session-id="${escapeHtml(s.session_id)}">
                <input type="checkbox" class="chats-row__check" aria-label="Select chat"
                    ${isSelected ? 'checked' : ''}>
                <button type="button" class="chats-row__star${s.starred ? ' is-starred' : ''}"
                    aria-label="${s.starred ? 'Unstar' : 'Star'} this chat" aria-pressed="${s.starred}">
                    ${s.starred ? '★' : '☆'}
                </button>
                <span class="chats-row__title">${escapeHtml(s.title)}</span>
                <span class="chats-row__meta">
                    <span class="chats-row__model">${escapeHtml(s.model || '')}</span>
                    <span class="chats-row__msgs">${s.msg_count || 0} msgs</span>
                    <span class="chats-row__indicators">
                        ${s.has_artifact ? '<span class="chats-row__indicator" title="Has artifact">◆</span>' : ''}
                        ${s.has_attachment ? '<span class="chats-row__indicator" title="Has attachment">📎</span>' : ''}
                    </span>
                </span>
                <span class="chats-row__time">${formatTime(s.last_updated)}</span>
                <span class="chats-row__actions">
                    <button type="button" class="chats-row__kebab" aria-label="More actions">⋯</button>
                </span>
            </li>
        `;
    }

    function attachRowListeners() {
        // Wired in later tasks — for now, click the title navigates.
        document.querySelectorAll('.chats-row').forEach(row => {
            const titleEl = row.querySelector('.chats-row__title');
            if (titleEl) {
                titleEl.addEventListener('click', () => {
                    const id = row.dataset.sessionId;
                    if (id) window.location.href = '/c/' + encodeURIComponent(id);
                });
            }
        });
    }

    /* ─── helpers ─────────────────────────────────────────────── */

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function formatTime(iso) {
        if (!iso) return '';
        const then = new Date(iso);
        if (isNaN(then.getTime())) return '';
        const now = new Date();
        const diffMs = now - then;
        const diffH = diffMs / 3600000;
        if (diffH < 1) return Math.max(1, Math.floor(diffMs / 60000)) + 'm';
        if (diffH < 24) return Math.floor(diffH) + 'h';
        const diffD = diffH / 24;
        if (diffD < 7) return Math.floor(diffD) + 'd';
        return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    /* ─── boot ────────────────────────────────────────────────── */

    function boot() {
        const cached = loadCache();
        if (cached) {
            state.sessions = cached;
            populateModelFilter();
            render();
            // Background refresh
            fetchSessions();
        } else {
            fetchSessions();
        }
    }

    /* ─── background refresh (30s while page is visible) ─────── */
    let refreshTimer = null;
    function startBackgroundRefresh() {
        clearInterval(refreshTimer);
        refreshTimer = setInterval(() => {
            if (document.visibilityState === 'visible') fetchSessions();
        }, 30_000);
    }
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') fetchSessions();
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { boot(); startBackgroundRefresh(); }, { once: true });
    } else {
        boot();
        startBackgroundRefresh();
    }

    // Expose for later tasks
    window.__chatsPage = { state, render, fetchSessions, invalidateCache };
})();
```

- [ ] **Step 2: Verify in browser**

Hard reload `/chats`. Expected:
- Skeleton rows appear briefly
- Real chat rows render with title + model + msg count + time
- Click a title → navigates to `/c/<session_id>`
- No console errors
- Sessionstorage has `chats-page-cache` key

- [ ] **Step 3: Commit**

```bash
git add app/static/js/chatsPage.js
git commit -m "feat(chats): initial fetch + render with sessionStorage cache"
```

---

## Task 7: Frontend — search, filter chips, sort

**Files:**
- Modify: `app/static/js/chatsPage.js`

- [ ] **Step 1: Add input/change listeners after the `boot()` definition**

In `chatsPage.js`, replace the `boot()` function with:

```javascript
function boot() {
    bindControls();
    const cached = loadCache();
    if (cached) {
        state.sessions = cached;
        populateModelFilter();
        render();
        fetchSessions(); // background refresh
    } else {
        fetchSessions();
    }
}

function bindControls() {
    let searchTimer = null;
    els.search.addEventListener('input', e => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            state.search = e.target.value || '';
            render();
        }, 80);
    });

    els.filterChips.forEach(chip => {
        chip.addEventListener('click', () => {
            const key = chip.dataset.filter;
            state.filters[key] = !state.filters[key];
            chip.setAttribute('aria-pressed', String(state.filters[key]));
            render();
        });
    });

    els.filterModel.addEventListener('change', e => {
        state.filters.model = e.target.value || '';
        render();
    });

    els.sort.addEventListener('change', e => {
        state.sort = e.target.value || 'recent';
        render();
    });

    els.emptyClear.addEventListener('click', () => {
        state.search = '';
        state.filters = { starred: false, model: '', hasArtifact: false, hasAttachment: false };
        els.search.value = '';
        els.filterChips.forEach(c => c.setAttribute('aria-pressed', 'false'));
        els.filterModel.value = '';
        render();
    });
}
```

- [ ] **Step 2: Verify in browser**

Hard reload. Expected:
- Type in search → list filters live
- Click "★ Starred" chip → only starred chats show
- Change model dropdown → filters by model
- Change sort dropdown → list re-orders
- Click "Clear filters" in empty state → filters reset

- [ ] **Step 3: Commit**

```bash
git add app/static/js/chatsPage.js
git commit -m "feat(chats): client-side search + filter chips + sort"
```

---

## Task 8: Frontend — star toggle (single + bulk)

**Files:**
- Modify: `app/static/js/chatsPage.js`

- [ ] **Step 1: Add the star + delegation logic**

Replace `attachRowListeners()` with the version below (delegation-based, handles star + checkbox + title click in one place):

```javascript
function attachRowListeners() {
    // Single delegation listener on the list — survives re-renders
    if (els.list.dataset.bound === '1') return;
    els.list.dataset.bound = '1';

    els.list.addEventListener('click', e => {
        const row = e.target.closest('.chats-row');
        if (!row) return;
        const id = row.dataset.sessionId;
        if (!id) return;

        // Star button
        if (e.target.closest('.chats-row__star')) {
            e.preventDefault();
            e.stopPropagation();
            toggleStar(id);
            return;
        }

        // Checkbox
        if (e.target.matches('.chats-row__check')) {
            e.stopPropagation();
            toggleSelect(id, e.target.checked);
            return;
        }

        // Kebab — wired in Task 10
        if (e.target.closest('.chats-row__kebab')) {
            e.preventDefault();
            e.stopPropagation();
            return;
        }

        // Default: click row → open chat
        if (e.target.closest('.chats-row__title') || e.target === row) {
            window.location.href = '/c/' + encodeURIComponent(id);
        }
    });
}

async function toggleStar(sessionId) {
    const s = state.sessions.find(x => x.session_id === sessionId);
    if (!s) return;
    const next = !s.starred;
    s.starred = next;  // optimistic
    saveCache(state.sessions);
    render();
    try {
        const resp = await fetch('/chat/' + encodeURIComponent(sessionId), {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
            },
            credentials: 'same-origin',
            body: JSON.stringify({ starred: next }),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
    } catch (err) {
        s.starred = !next;  // revert
        saveCache(state.sessions);
        render();
        showToast('Couldn\'t update star. Try again.');
    }
}

function getCsrfToken() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
}

function showToast(msg) {
    // Minimal toast — uses existing window.showToast if available, else console
    if (typeof window.showToast === 'function') return window.showToast(msg);
    console.warn('[chats]', msg);
}
```

Make sure the `chats.html` template includes the CSRF meta tag — it does via `base.html` if base extends from there. Verify by viewing source on `/chats`. If absent, add `<meta name="csrf-token" content="{{ csrf_token() }}">` to `chats.html` head.

- [ ] **Step 2: Add `toggleSelect()` stub** (will be expanded in Task 9)

```javascript
function toggleSelect(sessionId, isChecked) {
    if (isChecked) state.selected.add(sessionId);
    else state.selected.delete(sessionId);
    // Bulk toolbar visibility wired in Task 9
}
```

- [ ] **Step 3: Verify in browser**

Hard reload. Expected:
- Click ☆ on a row → fills to ★, persists across reload
- Click ★ → reverts to ☆
- Click row title → navigates to chat
- Click checkbox doesn't navigate

- [ ] **Step 4: Commit**

```bash
git add app/static/js/chatsPage.js app/templates/chats.html
git commit -m "feat(chats): star toggle with optimistic update + PATCH"
```

---

## Task 9: Frontend — bulk select + bulk-delete + bulk-star

**Files:**
- Modify: `app/static/js/chatsPage.js`

- [ ] **Step 1: Expand bulk handling**

Replace the stub `toggleSelect` from Task 8 with:

```javascript
function toggleSelect(sessionId, isChecked) {
    if (isChecked) state.selected.add(sessionId);
    else state.selected.delete(sessionId);
    updateBulkToolbar();
}

function updateBulkToolbar() {
    const count = state.selected.size;
    if (count === 0) {
        els.bulkToolbar.hidden = true;
    } else {
        els.bulkToolbar.hidden = false;
        els.bulkCount.textContent = count + (count === 1 ? ' selected' : ' selected');
    }
}

function bindBulkActions() {
    els.bulkCancel.addEventListener('click', () => {
        state.selected.clear();
        updateBulkToolbar();
        render();
    });

    els.bulkStar.addEventListener('click', async () => {
        const ids = Array.from(state.selected);
        if (ids.length === 0) return;
        // Optimistic: star them all
        ids.forEach(id => {
            const s = state.sessions.find(x => x.session_id === id);
            if (s) s.starred = true;
        });
        saveCache(state.sessions);
        render();
        try {
            await Promise.all(ids.map(id => fetch('/chat/' + encodeURIComponent(id), {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
                body: JSON.stringify({ starred: true }),
            }).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); })));
        } catch (_) {
            showToast('Some sessions could not be starred. Refreshing…');
            fetchSessions();
        }
    });

    els.bulkDelete.addEventListener('click', async () => {
        const ids = Array.from(state.selected);
        if (ids.length === 0) return;
        const confirmed = window.confirm(
            'Delete ' + ids.length + ' chat' + (ids.length === 1 ? '' : 's') + '? This cannot be undone.'
        );
        if (!confirmed) return;
        // Optimistic remove
        const removed = state.sessions.filter(s => ids.includes(s.session_id));
        state.sessions = state.sessions.filter(s => !ids.includes(s.session_id));
        state.selected.clear();
        updateBulkToolbar();
        saveCache(state.sessions);
        render();
        try {
            const resp = await fetch('/chat/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
                body: JSON.stringify({ session_ids: ids }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
        } catch (_) {
            // Revert
            state.sessions = state.sessions.concat(removed);
            saveCache(state.sessions);
            showToast('Bulk delete failed. Restored.');
            render();
        }
    });
}
```

- [ ] **Step 2: Call `bindBulkActions()` from `boot()`**

Add `bindBulkActions();` line right after `bindControls();` in `boot()`.

- [ ] **Step 3: Verify in browser**

Hard reload. Expected:
- Hover a row → checkbox appears
- Check 2-3 rows → bulk toolbar appears: "3 selected · Star · Delete · Cancel"
- Click "Star" → stars added to all 3, persists
- Click "Delete" → confirm modal → rows disappear, persist on reload
- Click "Cancel" → selection clears, checkboxes hide

- [ ] **Step 4: Commit**

```bash
git add app/static/js/chatsPage.js
git commit -m "feat(chats): bulk select with bulk-delete and bulk-star actions"
```

---

## Task 10: Frontend — kebab menu (rename + single delete)

**Files:**
- Modify: `app/static/js/chatsPage.js`, `app/static/css/chats.css`

- [ ] **Step 1: Add kebab menu CSS to `chats.css`**

Append to `app/static/css/chats.css`:

```css
/* ── Kebab popover ────────────────────────────────────────────── */
.chats-row__menu {
    position: absolute;
    background: var(--bg-elevated, #1a1a22);
    border: 1px solid var(--border-primary, rgba(255, 255, 255, 0.08));
    border-radius: 8px;
    padding: 4px 0;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
    z-index: 100;
    min-width: 140px;
}

.chats-row__menu-item {
    display: block;
    width: 100%;
    background: none;
    border: none;
    padding: 8px 14px;
    text-align: left;
    font-size: 13px;
    color: var(--text-primary, #e8e8f0);
    cursor: pointer;
    font-family: inherit;
}

.chats-row__menu-item:hover {
    background: var(--bg-hover, rgba(255, 255, 255, 0.06));
}

.chats-row__menu-item--danger {
    color: #f87171;
}

.chats-row__title.is-editing {
    background: var(--bg-elevated, #1a1a22);
    border: 1px solid var(--accent-muted, rgba(124, 106, 239, 0.4));
    border-radius: 6px;
    padding: 4px 8px;
    outline: none;
    cursor: text;
}
```

- [ ] **Step 2: Wire the kebab in `chatsPage.js`**

Inside `attachRowListeners()`, replace the `// Kebab — wired in Task 10` block with:

```javascript
        // Kebab
        if (e.target.closest('.chats-row__kebab')) {
            e.preventDefault();
            e.stopPropagation();
            openKebabMenu(row, id);
            return;
        }
```

Add these functions to the IIFE:

```javascript
let openMenu = null;

function openKebabMenu(row, sessionId) {
    closeKebabMenu();
    const menu = document.createElement('div');
    menu.className = 'chats-row__menu';
    menu.innerHTML = `
        <button type="button" class="chats-row__menu-item" data-action="rename">Rename</button>
        <button type="button" class="chats-row__menu-item chats-row__menu-item--danger" data-action="delete">Delete</button>
    `;
    document.body.appendChild(menu);
    const r = row.querySelector('.chats-row__kebab').getBoundingClientRect();
    menu.style.top = (r.bottom + window.scrollY + 4) + 'px';
    menu.style.left = (r.right + window.scrollX - menu.offsetWidth) + 'px';
    menu.addEventListener('click', e => {
        const btn = e.target.closest('.chats-row__menu-item');
        if (!btn) return;
        const action = btn.dataset.action;
        closeKebabMenu();
        if (action === 'rename') startRename(row, sessionId);
        if (action === 'delete') deleteSingle(sessionId);
    });
    setTimeout(() => document.addEventListener('click', closeKebabMenu, { once: true }), 0);
    openMenu = menu;
}

function closeKebabMenu() {
    if (openMenu) { openMenu.remove(); openMenu = null; }
}

function startRename(row, sessionId) {
    const titleEl = row.querySelector('.chats-row__title');
    if (!titleEl) return;
    const original = titleEl.textContent;
    titleEl.classList.add('is-editing');
    titleEl.contentEditable = 'true';
    titleEl.focus();
    // Select all text
    const range = document.createRange();
    range.selectNodeContents(titleEl);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);

    const finish = async (commit) => {
        titleEl.contentEditable = 'false';
        titleEl.classList.remove('is-editing');
        const next = (titleEl.textContent || '').trim();
        if (!commit || !next || next === original) {
            titleEl.textContent = original;
            return;
        }
        const s = state.sessions.find(x => x.session_id === sessionId);
        if (s) { s.title = next; saveCache(state.sessions); }
        try {
            const resp = await fetch('/chat/' + encodeURIComponent(sessionId), {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json',
                           'X-CSRFToken': getCsrfToken() },
                credentials: 'same-origin',
                body: JSON.stringify({ title: next }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
        } catch (_) {
            if (s) { s.title = original; saveCache(state.sessions); render(); }
            showToast('Rename failed.');
        }
    };

    titleEl.addEventListener('blur', () => finish(true), { once: true });
    titleEl.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
        if (e.key === 'Escape') { e.preventDefault(); finish(false); titleEl.blur(); }
    });
}

async function deleteSingle(sessionId) {
    const s = state.sessions.find(x => x.session_id === sessionId);
    if (!s) return;
    const confirmed = window.confirm('Delete "' + s.title + '"? This cannot be undone.');
    if (!confirmed) return;
    state.sessions = state.sessions.filter(x => x.session_id !== sessionId);
    saveCache(state.sessions);
    render();
    try {
        const resp = await fetch('/chat/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json',
                       'X-CSRFToken': getCsrfToken() },
            credentials: 'same-origin',
            body: JSON.stringify({ session_id: sessionId }),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
    } catch (_) {
        state.sessions = state.sessions.concat([s]);
        saveCache(state.sessions);
        render();
        showToast('Delete failed. Restored.');
    }
}
```

- [ ] **Step 3: Verify in browser**

Hard reload. Expected:
- Hover row → ⋯ kebab visible
- Click kebab → popover with "Rename" / "Delete"
- Click Rename → title becomes editable, Enter saves, Escape cancels, persists
- Click Delete → confirm → row gone, persists

- [ ] **Step 4: Commit**

```bash
git add app/static/js/chatsPage.js app/static/css/chats.css
git commit -m "feat(chats): kebab menu — inline rename + single delete"
```

---

## Task 11: Mobile responsive

**Files:**
- Modify: `app/static/css/chats.css`

- [ ] **Step 1: Append mobile rules to `chats.css`**

```css
/* ── Mobile (≤768px) ──────────────────────────────────────────── */
@media (max-width: 768px) {
    .chats-page {
        padding: 20px 16px 32px;
    }

    .chats-page__title {
        font-size: 24px;
    }

    .chats-page__toolbar {
        flex-direction: column;
        align-items: stretch;
        gap: 8px;
    }

    .chats-page__filters {
        gap: 6px;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        flex-wrap: nowrap;
        padding-bottom: 4px;
    }

    .chats-row {
        padding: 12px 10px;
        gap: 10px;
    }

    .chats-row__check {
        opacity: 1;  /* always visible on touch */
    }

    .chats-row__model,
    .chats-row__msgs {
        display: none;  /* drop secondary metadata on narrow widths */
    }

    .chats-row__actions {
        opacity: 1;
    }

    .chats-page__bulk-toolbar {
        position: fixed;
        top: auto;
        bottom: 0;
        left: 0;
        right: 0;
        margin: 0;
        border-radius: 12px 12px 0 0;
        padding: 12px 16px env(safe-area-inset-bottom, 12px);
        z-index: 50;
    }
}

@media (max-width: 480px) {
    .chats-page__title {
        font-size: 22px;
    }

    .chats-row__time {
        min-width: 48px;
        font-size: 11px;
    }
}
```

- [ ] **Step 2: Verify in browser at 375px and 768px**

Open DevTools → device toolbar → iPhone SE (375). Expected:
- Header smaller, padding tighter
- Filter chips scroll horizontally
- Rows show only title + indicators + time
- Checkboxes always visible
- Bulk toolbar slides up from bottom of viewport

- [ ] **Step 3: Commit**

```bash
git add app/static/css/chats.css
git commit -m "feat(chats): mobile responsive — stacked toolbar, dropped columns, fixed bulk bar"
```

---

## Task 12: Empty states + error handling

**Files:**
- Modify: `app/static/js/chatsPage.js`, `app/templates/chats.html`

- [ ] **Step 1: Add error UI to `chats.html`**

After the empty state div in `chats.html`, add:

```html
    <div class="chats-page__error" id="chats-error" hidden>
        <p id="chats-error-msg">Couldn't load your chats.</p>
        <button type="button" class="chats-empty__clear" id="chats-error-retry">Retry</button>
    </div>
```

- [ ] **Step 2: Add error CSS**

Append to `chats.css`:

```css
.chats-page__error {
    text-align: center;
    padding: 48px 16px;
    color: var(--text-secondary, #9898b0);
}

.chats-page__error p {
    margin: 0 0 16px;
    font-size: 15px;
}
```

- [ ] **Step 3: Wire error state in `chatsPage.js`**

Add to `els`:
```javascript
        error: document.getElementById('chats-error'),
        errorMsg: document.getElementById('chats-error-msg'),
        errorRetry: document.getElementById('chats-error-retry'),
```

In `render()`, before checking `visible.length === 0`:
```javascript
function render() {
    if (state.error) {
        els.list.innerHTML = '';
        els.empty.hidden = true;
        els.error.hidden = false;
        els.errorMsg.textContent = state.error;
        return;
    }
    els.error.hidden = true;
    const visible = getVisibleSessions();
    // ...rest unchanged
}
```

In `bindControls()`, add:
```javascript
    els.errorRetry.addEventListener('click', () => {
        state.error = null;
        fetchSessions();
    });
```

Also handle the special "no chats at all" case. Modify the empty state section in `render()`:

```javascript
    if (visible.length === 0) {
        els.list.innerHTML = '';
        els.empty.hidden = false;
        const hasFilters = state.search || state.filters.starred || state.filters.model
            || state.filters.hasArtifact || state.filters.hasAttachment;
        const msg = els.empty.querySelector('p');
        const btn = els.empty.querySelector('.chats-empty__clear');
        if (state.sessions.length === 0) {
            msg.textContent = 'No chats yet. Start a new chat to get going.';
            btn.textContent = 'New chat';
            btn.onclick = () => { window.location.href = '/'; };
        } else if (hasFilters) {
            msg.textContent = 'No chats match your filters.';
            btn.textContent = 'Clear filters';
            btn.onclick = null;  // existing emptyClear listener handles this
        } else {
            msg.textContent = 'No chats found.';
        }
        return;
    }
```

- [ ] **Step 4: Verify in browser**

- Visit `/chats` while logged out → should redirect to login
- With network throttled / offline → retry button appears, retry refetches
- New account with zero chats → "No chats yet" + "New chat" button
- Apply filter that matches nothing → "No chats match" + clear button

- [ ] **Step 5: Commit**

```bash
git add app/static/js/chatsPage.js app/templates/chats.html app/static/css/chats.css
git commit -m "feat(chats): error state with retry + contextual empty messages"
```

---

## Task 13: Mobile long-press to enter select-mode

**Files:**
- Modify: `app/static/js/chatsPage.js`

- [ ] **Step 1: Add long-press detection**

Add to the IIFE:

```javascript
function bindLongPress() {
    let pressTimer = null;
    let pressTarget = null;
    const LONG_PRESS_MS = 500;

    els.list.addEventListener('touchstart', e => {
        const row = e.target.closest('.chats-row');
        if (!row) return;
        pressTarget = row;
        pressTimer = setTimeout(() => {
            if (pressTarget === row) {
                document.querySelector('.chats-page').classList.add('in-select-mode');
                const id = row.dataset.sessionId;
                if (id) toggleSelect(id, true);
                row.querySelector('.chats-row__check').checked = true;
                if (navigator.vibrate) navigator.vibrate(20);
            }
        }, LONG_PRESS_MS);
    }, { passive: true });

    const cancel = () => {
        clearTimeout(pressTimer);
        pressTimer = null;
        pressTarget = null;
    };
    els.list.addEventListener('touchend', cancel, { passive: true });
    els.list.addEventListener('touchmove', cancel, { passive: true });
    els.list.addEventListener('touchcancel', cancel, { passive: true });
}
```

Call it from `boot()` after `bindBulkActions()`:
```javascript
    bindLongPress();
```

When bulk-cancel is clicked, also remove the select-mode class. Update the cancel handler in `bindBulkActions()`:
```javascript
    els.bulkCancel.addEventListener('click', () => {
        state.selected.clear();
        document.querySelector('.chats-page').classList.remove('in-select-mode');
        updateBulkToolbar();
        render();
    });
```

- [ ] **Step 2: Verify on mobile (or DevTools touch emulation)**

- Long-press a row (~500ms) → checkbox checks, bulk toolbar slides up, vibration buzz on supporting devices
- Tap other rows → toggle selection
- Tap "Cancel" → exits select mode

- [ ] **Step 3: Commit**

```bash
git add app/static/js/chatsPage.js
git commit -m "feat(chats): mobile long-press to enter bulk select mode"
```

---

## Task 13b: Date range filter

**Files:**
- Modify: `app/templates/chats.html`, `app/static/js/chatsPage.js`, `app/static/css/chats.css`

- [ ] **Step 1: Add date inputs to the filter row in `chats.html`**

In the `.chats-page__filters` container, after the model select, add:

```html
            <label class="chats-chip chats-chip--date">
                From <input type="date" id="chats-filter-from" aria-label="From date">
            </label>
            <label class="chats-chip chats-chip--date">
                To <input type="date" id="chats-filter-to" aria-label="To date">
            </label>
```

- [ ] **Step 2: Append date-input styling to `chats.css`**

```css
.chats-chip--date {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--text-secondary, #9898b0);
    cursor: text;
}

.chats-chip--date input {
    background: transparent;
    border: none;
    color: var(--text-primary, #e8e8f0);
    font: inherit;
    outline: none;
    padding: 0;
    color-scheme: dark;
}
```

- [ ] **Step 3: Wire date filter in `chatsPage.js`**

Add to `state.filters`:
```javascript
            dateFrom: '',
            dateTo: '',
```

Add to `els`:
```javascript
        filterFrom: document.getElementById('chats-filter-from'),
        filterTo: document.getElementById('chats-filter-to'),
```

In `getVisibleSessions()`, add to the filter chain (before the `q` check):
```javascript
            if (state.filters.dateFrom && (s.last_updated || '') < state.filters.dateFrom) return false;
            if (state.filters.dateTo && (s.last_updated || '') > state.filters.dateTo + 'T23:59:59') return false;
```

In `bindControls()`, add:
```javascript
    els.filterFrom.addEventListener('change', e => {
        state.filters.dateFrom = e.target.value || '';
        render();
    });
    els.filterTo.addEventListener('change', e => {
        state.filters.dateTo = e.target.value || '';
        render();
    });
```

In the empty-clear handler:
```javascript
    state.filters = { starred: false, model: '', hasArtifact: false, hasAttachment: false,
                      dateFrom: '', dateTo: '' };
    els.filterFrom.value = '';
    els.filterTo.value = '';
```

- [ ] **Step 4: Verify in browser**

- Set From date → only sessions on/after that date show
- Set To date → only sessions on/before that date show
- Both dates → range filter
- "Clear filters" resets dates

- [ ] **Step 5: Commit**

```bash
git add app/templates/chats.html app/static/js/chatsPage.js app/static/css/chats.css
git commit -m "feat(chats): date range filter (from / to)"
```

---

## Task 14: Final integration test + manual smoke

- [ ] **Step 1: Run all backend tests**

```bash
python -m pytest tests/unit/test_chats_endpoints.py -v
```
Expected: ALL PASS.

- [ ] **Step 2: Run any existing related tests for regressions**

```bash
python -m pytest tests/unit/test_chat_routes_temp_mode.py tests/test_voice_sessions.py -v
```
Expected: ALL PASS (no regressions).

- [ ] **Step 3: Manual smoke checklist**

Open `/chats` and verify:
- [ ] Page loads in <500ms with cache; <1s without cache
- [ ] Search filters live as you type
- [ ] Each filter chip toggles correctly
- [ ] Sort dropdown changes order
- [ ] Star icon click persists across reload
- [ ] Hover row → checkbox + kebab visible
- [ ] Bulk select shows toolbar
- [ ] Bulk delete + bulk star both work, errors revert
- [ ] Kebab → rename inline, persists
- [ ] Kebab → delete with confirm, persists
- [ ] Click row title navigates to `/c/<session_id>`
- [ ] Sidebar `Chats` button navigates to `/chats`
- [ ] Mobile (375px): columns dropped, long-press selects, bulk toolbar at bottom
- [ ] Empty state: "no chats yet" for new users; "no matches" for filtered
- [ ] Error state: retry button works after network failure

- [ ] **Step 4: Sync `/opt/xelasphere/` (if deploying via file-copy)**

Per the project's deploy mechanism, copy edited files from the dev repo to `/opt/xelasphere/`:

```bash
DEV=/root/xelas.io/xelasphere.io-
PROD=/opt/xelasphere
for f in \
    app/blueprints/pages_routes.py \
    app/blueprints/session_routes.py \
    app/chat_session.py \
    app/templates/chats.html \
    app/templates/index.html \
    app/static/css/chats.css \
    app/static/js/chatsPage.js \
    tests/unit/test_chats_endpoints.py; do
  cp "$DEV/$f" "$PROD/$f"
done
sudo systemctl restart xelasphere
```

- [ ] **Step 5: Commit final test results + push branch**

```bash
git push origin <branch-name>
```

Then open a PR.

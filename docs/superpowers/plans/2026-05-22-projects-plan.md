# Projects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a faithful ChatGPT-Projects-equivalent feature in XelaSphere — Projects bundle custom instructions, uploaded files (text-extracted), and a chat list; chats inside a Project automatically inherit the Project's instructions and file context as their system prompt.

**Architecture:** Five milestones, each independently shippable.
- **M0** — Backend foundation. `ProjectService` data layer, CRUD blueprint, `project_id` foreign key on sessions, history-feed filtering. No UI changes.
- **M1** — Sidebar Projects section + Create modal. The placeholder "Projects · Coming soon" button (`index.html:489`) is replaced; chats can be created in a Project from the sidebar.
- **M2** — Project landing page (`/projects/<id>`): instructions editor, chats list, "+ New chat in this project".
- **M3** — Files. Upload, extract via existing `TextExtractorUtility`, store text in subcollection, list/delete on landing page. System-prompt injection at chat send.
- **M4** — Polish. "Add to project" sidebar menu (wires `#action-add-to-project`), "in: <project>" chat header tag, color picker, delete-cascade dialog.

**Tech Stack:** Flask blueprint (Python 3, Firestore via Google Cloud SDK), pytest with conftest fixtures, vanilla JS modules (no framework), plain CSS using existing design tokens. No JS test runner in the codebase — TDD applies to Python only; frontend tasks are implementation + manual verification.

---

## Spec reference

`docs/superpowers/specs/2026-05-22-projects-design.md` (commit `93aaee8`)

## Codebase facts the implementing engineer should know upfront

- Sessions live at `users/{uid}/chat_sessions/{sessionId}` in Firestore.
- The chat history feed is `GET /chat/history` in `app/blueprints/session_routes.py:450`. Server categorizes by recency via `categorize_sessions()` at `session_routes.py:43`.
- Session data layer is `app/chat_session.py`. PATCH allowlist `ALLOWED_PATCH_FIELDS` is at `chat_session.py:17`.
- Blueprint registration site is `app/__init__.py:195-213` — add `app.register_blueprint(project_bp)` alongside the others.
- Sidebar markup is in `app/templates/index.html` under `#sidebar` (around line 381). The existing **`#action-projects`** placeholder button is at `index.html:489-498`; the **`#action-add-to-project`** menu item is at `index.html:1490`. Both get wired up by this work.
- Sidebar JS rendering is `app/static/js/startNewChatModule.js` — `renderChatTabs()` at line 585.
- Existing file upload pipeline: `app/blueprints/upload_routes.py`. Text extraction: `app.text_extractor.TextExtractorUtility.extract_text(file_path)` → `(text, debug_info)`. Files land under `UPLOAD_FOLDER/<user_id>/YYYY/MM/DD/`.
- System-prompt assembly happens in `app/blueprints/chat_routes.py` at two sites — line 1183 (`base_system = SYSTEM_INSTRUCTIONS + "\n" + get_developer_instructions(stream_model)`) and line 3154 (same pattern). Both must inject Project context.
- Auth: `@login_required` decorator (from `app.blueprints.shared`); CSRF on writes via `@csrf_required` (from `app.limiter`).
- Tests live under `tests/`. `conftest.py` provides Flask test client and Firestore fixtures. Run with `pytest tests/<file>::<test_name> -v`.

## File map

### Created
- `app/blueprints/project_routes.py` — Flask blueprint for Projects + files (CRUD + reorder)
- `app/project_service.py` — data-layer module (Projects + per-Project file text subcollection)
- `app/project_context.py` — pure helper that builds the system-prompt addition from a Project (instructions + files concatenation within token budget)
- `app/static/js/projectsModule.js` — sidebar state, list render, create-modal logic, "Add to project" menu wiring
- `app/static/js/projectsPage.js` — Project landing page (instructions editor + autosave, chats list, files list, color picker)
- `app/static/css/projects.css` — sidebar Projects section, create modal, landing page, file rows, "in: <project>" tag
- `app/templates/projects_page.html` — Project landing page template
- `tests/test_project_routes.py` — CRUD route integration tests
- `tests/test_project_service.py` — data-layer tests
- `tests/test_project_context.py` — system-prompt assembly tests
- `tests/test_chat_history_project_filter.py` — sidebar feed filter tests
- `tests/test_chat_system_prompt_injection.py` — end-to-end test that a chat in a Project sees Project context

### Modified
- `app/chat_session.py` — add `project_id` to `ALLOWED_PATCH_FIELDS` (line 17); thread `project_id`/`ungrouped` into `get_history()`; add `count_grouped()` helper
- `app/blueprints/session_routes.py:450` — accept `?project_id=…` and `?ungrouped=1`; return `grouped_count` when relevant
- `app/blueprints/chat_routes.py:1183` and `:3154` — when the session has `project_id`, inject Project context into `base_system`
- `app/blueprints/pages_routes.py` — add `GET /projects/<id>` route that renders `projects_page.html`
- `app/__init__.py:195-213` — register `project_bp`
- `app/templates/index.html:489-498` — remove the "Projects · Coming soon" placeholder; add the Projects section markup above the chat tab list; mount the Create-project modal
- `app/templates/index.html:1490` — wire the existing `#action-add-to-project` placeholder to open the project picker
- `app/static/js/startNewChatModule.js` — query `/chat/history` with `?ungrouped=1` at the top level (or `?project_id=X` inside a Project); render the `+ N more in projects` hint
- `firestore.rules` — add rule for `users/{uid}/projects/**`

### Untouched (intentional)
- `categorize_sessions()` in `session_routes.py:43` — operates on whatever list it receives. Filtering happens upstream in the data layer.
- `TextExtractorUtility` — already extracts text from PDFs/docs/images. Reused as-is.
- File upload route `POST /upload` — kept for chat-attached files. Projects get their own `POST /projects/<id>/files` route that wraps the same storage + extraction pipeline.

---

## Milestone 0 — Backend foundation (CRUD + filtering, no UI)

Ships the data model, REST API, and feed filtering. Independently testable via `curl`. No user-visible UI changes yet — the existing "Projects · Coming soon" button stays as-is.

### Task 0.1: Add `project_id` to the session PATCH allowlist

**Files:**
- Modify: `app/chat_session.py:17`
- Test: `tests/test_project_session_patch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_session_patch.py`:

```python
"""Sessions accept project_id via the existing PATCH /chat/<id> route."""
import pytest
from app.chat_session import ALLOWED_PATCH_FIELDS


def test_project_id_in_allowed_patch_fields():
    assert 'project_id' in ALLOWED_PATCH_FIELDS


def test_patch_session_sets_project_id(authed_client, sample_session):
    sid = sample_session['session_id']
    resp = authed_client.patch(f'/chat/{sid}', json={'project_id': 'p-abc'})
    assert resp.status_code == 204

    resp2 = authed_client.get(f'/chat/session/{sid}')
    assert resp2.json['session']['project_id'] == 'p-abc'


def test_patch_session_clears_project_id(authed_client, sample_session):
    sid = sample_session['session_id']
    authed_client.patch(f'/chat/{sid}', json={'project_id': 'p-abc'})
    resp = authed_client.patch(f'/chat/{sid}', json={'project_id': None})
    assert resp.status_code == 204

    resp2 = authed_client.get(f'/chat/session/{sid}')
    assert resp2.json['session'].get('project_id') is None
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_project_session_patch.py -v`
Expected: all three FAIL — `project_id` not in allowlist.

- [ ] **Step 3: Implement**

In `app/chat_session.py:17`, change:

```python
ALLOWED_PATCH_FIELDS = {'starred', 'pinned', 'title', 'tags'}
```

to:

```python
ALLOWED_PATCH_FIELDS = {'starred', 'pinned', 'title', 'tags', 'project_id'}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_project_session_patch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/chat_session.py tests/test_project_session_patch.py
git commit -m "feat(projects): allow project_id on session PATCH"
```

---

### Task 0.2: Create `ProjectService` data layer

**Files:**
- Create: `app/project_service.py`
- Test: `tests/test_project_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_project_service.py`:

```python
"""ProjectService — CRUD + files subcollection."""
import pytest
from app.project_service import ProjectService


@pytest.fixture
def svc(firestore_db):
    return ProjectService(firestore_db)


def test_create_returns_id_and_writes_doc(svc):
    p = svc.create('u1', name='Copenhagen Trip', color='#2563eb')
    assert p['id']
    assert p['name'] == 'Copenhagen Trip'
    assert p['color'] == '#2563eb'
    assert p['instructions'] == ''
    assert p['files'] == []
    assert p['position'] == 0
    assert 'created_at' in p
    assert 'updated_at' in p


def test_create_appends_position(svc):
    a = svc.create('u1', name='A', color='#000')
    b = svc.create('u1', name='B', color='#000')
    assert b['position'] == a['position'] + 1


def test_list_orders_by_position(svc):
    svc.create('u1', name='First', color='#000')
    svc.create('u1', name='Second', color='#000')
    out = svc.list('u1')
    assert [p['name'] for p in out] == ['First', 'Second']


def test_update_instructions(svc):
    p = svc.create('u1', name='X', color='#000')
    svc.update('u1', p['id'], {'instructions': 'Be warm and Welsh.'})
    assert svc.get('u1', p['id'])['instructions'] == 'Be warm and Welsh.'


def test_update_rejects_unknown_fields(svc):
    p = svc.create('u1', name='X', color='#000')
    svc.update('u1', p['id'], {'name': 'Y', 'evil': '1'})
    fetched = svc.get('u1', p['id'])
    assert fetched['name'] == 'Y'
    assert 'evil' not in fetched


def test_delete_removes_doc_and_files_subcollection(svc):
    p = svc.create('u1', name='X', color='#000')
    svc.add_file(
        'u1', p['id'],
        file_id='f1', filename='a.pdf', mime_type='application/pdf',
        size_bytes=1234, storage_path='u1/2026/05/22/a.pdf',
        extracted_text='hello world',
    )
    svc.delete('u1', p['id'])
    assert svc.get('u1', p['id']) is None
    # Files subcollection should be empty/gone.
    assert svc.list_files('u1', p['id']) == []


def test_add_file_stores_metadata_inline_and_text_in_subcollection(svc):
    p = svc.create('u1', name='X', color='#000')
    svc.add_file(
        'u1', p['id'],
        file_id='f1', filename='a.pdf', mime_type='application/pdf',
        size_bytes=1234, storage_path='u1/2026/05/22/a.pdf',
        extracted_text='hello world',
    )
    fetched = svc.get('u1', p['id'])
    assert len(fetched['files']) == 1
    meta = fetched['files'][0]
    assert meta['file_id'] == 'f1'
    assert meta['filename'] == 'a.pdf'
    assert meta['text_char_count'] == len('hello world')
    # Text body lives in the subcollection.
    files = svc.list_files('u1', p['id'])
    assert len(files) == 1
    assert files[0]['extracted_text'] == 'hello world'


def test_add_file_truncates_text_at_storage_cap(svc):
    p = svc.create('u1', name='X', color='#000')
    huge = 'x' * 250_000
    svc.add_file(
        'u1', p['id'], file_id='f1', filename='big.txt',
        mime_type='text/plain', size_bytes=len(huge),
        storage_path='_', extracted_text=huge,
    )
    files = svc.list_files('u1', p['id'])
    assert len(files[0]['extracted_text']) == 200_000
    assert files[0]['extracted_text'].endswith('[truncated]')


def test_add_file_rejects_when_at_file_cap(svc, monkeypatch):
    monkeypatch.setattr('app.project_service.MAX_FILES_PER_PROJECT', 2)
    p = svc.create('u1', name='X', color='#000')
    for i in range(2):
        svc.add_file(
            'u1', p['id'], file_id=f'f{i}', filename=f'{i}.txt',
            mime_type='text/plain', size_bytes=1, storage_path='_',
            extracted_text='x',
        )
    with pytest.raises(ProjectService.LimitExceeded):
        svc.add_file(
            'u1', p['id'], file_id='f3', filename='3.txt',
            mime_type='text/plain', size_bytes=1, storage_path='_',
            extracted_text='x',
        )


def test_remove_file_clears_metadata_and_subcollection_doc(svc):
    p = svc.create('u1', name='X', color='#000')
    svc.add_file(
        'u1', p['id'], file_id='f1', filename='a.pdf',
        mime_type='application/pdf', size_bytes=1, storage_path='_',
        extracted_text='hi',
    )
    svc.remove_file('u1', p['id'], 'f1')
    fetched = svc.get('u1', p['id'])
    assert fetched['files'] == []
    assert svc.list_files('u1', p['id']) == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_project_service.py -v`
Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the service**

Create `app/project_service.py`:

```python
"""ProjectService — data-layer wrapper for users/{uid}/projects/{projectId}.

Files split between inline metadata (on the Project doc) and per-file text
docs in users/{uid}/projects/{projectId}/files/{fileId}. See spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore as gc_firestore


MAX_FILES_PER_PROJECT = 20
PER_FILE_TEXT_CAP = 200_000  # storage cap, chars
ALLOWED_FIELDS = {'name', 'color', 'instructions', 'position'}


class ProjectService:
    class LimitExceeded(Exception):
        pass

    def __init__(self, db):
        self.db = db

    def _coll(self, uid: str):
        return self.db.collection('users').document(uid).collection('projects')

    def _files_coll(self, uid: str, project_id: str):
        return self._coll(uid).document(project_id).collection('files')

    def list(self, uid: str) -> List[Dict[str, Any]]:
        q = self._coll(uid).order_by('position', direction=gc_firestore.Query.ASCENDING)
        out = []
        for snap in q.stream():
            doc = snap.to_dict() or {}
            doc['id'] = snap.id
            out.append(_normalize(doc))
        return out

    def get(self, uid: str, project_id: str) -> Optional[Dict[str, Any]]:
        snap = self._coll(uid).document(project_id).get()
        if not snap.exists:
            return None
        doc = snap.to_dict() or {}
        doc['id'] = snap.id
        return _normalize(doc)

    def count(self, uid: str) -> int:
        return sum(1 for _ in self._coll(uid).stream())

    def create(self, uid: str, *, name: str, color: str) -> Dict[str, Any]:
        existing = self.list(uid)
        position = (existing[-1]['position'] + 1) if existing else 0
        now = datetime.utcnow()
        payload = {
            'name': name,
            'color': color,
            'instructions': '',
            'files': [],
            'position': position,
            'created_at': now,
            'updated_at': now,
        }
        ref = self._coll(uid).document()
        ref.set(payload)
        return self.get(uid, ref.id)

    def update(self, uid: str, project_id: str, fields: Dict[str, Any]) -> bool:
        cleaned = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS}
        if not cleaned:
            return False
        cleaned['updated_at'] = datetime.utcnow()
        ref = self._coll(uid).document(project_id)
        if not ref.get().exists:
            return False
        ref.update(cleaned)
        return True

    def delete(self, uid: str, project_id: str) -> bool:
        ref = self._coll(uid).document(project_id)
        if not ref.get().exists:
            return False
        # Delete files subcollection.
        for snap in self._files_coll(uid, project_id).stream():
            snap.reference.delete()
        ref.delete()
        return True

    def add_file(
        self, uid: str, project_id: str, *,
        file_id: str, filename: str, mime_type: str, size_bytes: int,
        storage_path: str, extracted_text: str,
    ) -> Dict[str, Any]:
        proj = self.get(uid, project_id)
        if proj is None:
            raise ValueError('Project not found')
        if len(proj['files']) >= MAX_FILES_PER_PROJECT:
            raise self.LimitExceeded(f'Max {MAX_FILES_PER_PROJECT} files per project')

        text = extracted_text or ''
        if len(text) > PER_FILE_TEXT_CAP:
            # Reserve room for the marker.
            text = text[:PER_FILE_TEXT_CAP - len('[truncated]')] + '[truncated]'

        now = datetime.utcnow()
        meta = {
            'file_id': file_id,
            'filename': filename,
            'mime_type': mime_type,
            'size_bytes': size_bytes,
            'storage_path': storage_path,
            'text_char_count': len(text),
            'uploaded_at': now,
        }

        ref = self._coll(uid).document(project_id)
        ref.update({
            'files': gc_firestore.ArrayUnion([meta]),
            'updated_at': now,
        })
        self._files_coll(uid, project_id).document(file_id).set({
            'extracted_text': text,
            'uploaded_at': now,
        })
        return meta

    def remove_file(self, uid: str, project_id: str, file_id: str) -> bool:
        proj = self.get(uid, project_id)
        if proj is None:
            return False
        new_files = [f for f in proj['files'] if f.get('file_id') != file_id]
        if len(new_files) == len(proj['files']):
            return False  # not present
        self._coll(uid).document(project_id).update({
            'files': new_files,
            'updated_at': datetime.utcnow(),
        })
        self._files_coll(uid, project_id).document(file_id).delete()
        return True

    def list_files(self, uid: str, project_id: str) -> List[Dict[str, Any]]:
        """Returns extracted-text docs from the subcollection."""
        out = []
        for snap in self._files_coll(uid, project_id).stream():
            doc = snap.to_dict() or {}
            doc['file_id'] = snap.id
            out.append(_normalize(doc))
        return out


def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    for k in ('created_at', 'updated_at', 'uploaded_at'):
        v = doc.get(k)
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    files = doc.get('files')
    if isinstance(files, list):
        for f in files:
            v = f.get('uploaded_at')
            if isinstance(v, datetime):
                f['uploaded_at'] = v.isoformat()
    return doc
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_project_service.py -v`
Expected: all 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/project_service.py tests/test_project_service.py
git commit -m "feat(projects): ProjectService data layer (CRUD + files subcollection)"
```

---

### Task 0.3: `project_routes` blueprint — Project CRUD

**Files:**
- Create: `app/blueprints/project_routes.py`
- Modify: `app/__init__.py:195-213`
- Test: `tests/test_project_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_project_routes.py`:

```python
"""Project CRUD route integration tests."""
import pytest


def test_list_projects_empty(authed_client):
    resp = authed_client.get('/projects')
    assert resp.status_code == 200
    assert resp.json == {'projects': []}


def test_create_project_returns_201(authed_client):
    resp = authed_client.post('/projects', json={'name': 'Copenhagen Trip'})
    assert resp.status_code == 201
    body = resp.json
    assert body['project']['id']
    assert body['project']['name'] == 'Copenhagen Trip'
    # Color auto-assigned from palette.
    assert body['project']['color'].startswith('#')


def test_create_rejects_empty_name(authed_client):
    resp = authed_client.post('/projects', json={'name': '   '})
    assert resp.status_code == 400


def test_create_rejects_long_name(authed_client):
    resp = authed_client.post('/projects', json={'name': 'X' * 121})
    assert resp.status_code == 400


def test_create_accepts_optional_color(authed_client):
    resp = authed_client.post('/projects', json={'name': 'A', 'color': '#16a34a'})
    assert resp.status_code == 201
    assert resp.json['project']['color'] == '#16a34a'


def test_get_project(authed_client):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    resp = authed_client.get(f'/projects/{pid}')
    assert resp.status_code == 200
    assert resp.json['project']['name'] == 'X'


def test_get_project_404(authed_client):
    assert authed_client.get('/projects/nope').status_code == 404


def test_patch_instructions(authed_client):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    resp = authed_client.patch(f'/projects/{pid}', json={'instructions': 'Be warm.'})
    assert resp.status_code == 204
    assert authed_client.get(f'/projects/{pid}').json['project']['instructions'] == 'Be warm.'


def test_patch_rejects_long_instructions(authed_client):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    resp = authed_client.patch(f'/projects/{pid}', json={'instructions': 'x' * 8001})
    assert resp.status_code == 400


def test_patch_color_must_be_hex(authed_client):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    resp = authed_client.patch(f'/projects/{pid}', json={'color': 'blue'})
    assert resp.status_code == 400


def test_delete_keep_chats_default(authed_client, sample_session):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    sid = sample_session['session_id']
    authed_client.patch(f'/chat/{sid}', json={'project_id': pid})

    resp = authed_client.delete(f'/projects/{pid}')
    assert resp.status_code == 204
    # Session survives with project_id cleared.
    sj = authed_client.get(f'/chat/session/{sid}')
    assert sj.status_code == 200
    assert sj.json['session'].get('project_id') is None


def test_delete_cascade_deletes_chats(authed_client, sample_session):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    sid = sample_session['session_id']
    authed_client.patch(f'/chat/{sid}', json={'project_id': pid})

    resp = authed_client.delete(f'/projects/{pid}?cascade=delete')
    assert resp.status_code == 204
    assert authed_client.get(f'/chat/session/{sid}').status_code == 404


def test_login_required(client):
    assert client.get('/projects').status_code in (302, 401)
    assert client.post('/projects', json={'name': 'X'}).status_code in (302, 401)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_project_routes.py -v`
Expected: all fail — blueprint not registered, routes don't exist.

- [ ] **Step 3: Implement the blueprint**

Create `app/blueprints/project_routes.py`:

```python
"""Project CRUD routes. See spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from app.config import db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import error_handler, login_required, chat_session
from app.project_service import ProjectService

logger = logging.getLogger(__name__)

project_bp = Blueprint('project_bp', __name__)

MAX_NAME_LEN = 120
MAX_INSTRUCTIONS_LEN = 8_000
COLOR_PALETTE = [
    '#d97706', '#dc2626', '#db2777', '#7c3aed', '#2563eb',
    '#0891b2', '#16a34a', '#ca8a04', '#475569', '#737373',
]


def _service() -> ProjectService:
    return ProjectService(db)


def _validate_color(color: str) -> bool:
    if not isinstance(color, str) or not color.startswith('#'):
        return False
    if len(color) not in (4, 7, 9):  # #rgb, #rrggbb, #rrggbbaa
        return False
    try:
        int(color[1:], 16)
        return True
    except ValueError:
        return False


@project_bp.route('/projects', methods=['GET'])
@error_handler
@login_required
def list_projects():
    uid = session['user']['uid']
    return jsonify({'projects': _service().list(uid)})


@project_bp.route('/projects', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def create_project():
    uid = session['user']['uid']
    body = request.get_json(silent=True) or {}
    name = body.get('name')
    if not isinstance(name, str) or not name.strip():
        return jsonify({'error': 'name required'}), 400
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        return jsonify({'error': f'name max {MAX_NAME_LEN} chars'}), 400

    color = body.get('color')
    if color is None:
        # Auto-assign by cycling palette by current count.
        idx = _service().count(uid) % len(COLOR_PALETTE)
        color = COLOR_PALETTE[idx]
    elif not _validate_color(color):
        return jsonify({'error': 'color must be valid hex starting with #'}), 400

    p = _service().create(uid, name=name, color=color)
    return jsonify({'project': p}), 201


@project_bp.route('/projects/<project_id>', methods=['GET'])
@error_handler
@login_required
def get_project(project_id):
    uid = session['user']['uid']
    p = _service().get(uid, project_id)
    if not p:
        return jsonify({'error': 'Project not found'}), 404
    return jsonify({'project': p})


@project_bp.route('/projects/<project_id>', methods=['PATCH'])
@limiter.limit("60 per minute")
@csrf_required
@error_handler
@login_required
def patch_project(project_id):
    uid = session['user']['uid']
    body = request.get_json(silent=True) or {}
    cleaned = {}

    if 'name' in body:
        if not isinstance(body['name'], str) or not body['name'].strip():
            return jsonify({'error': 'name must be non-empty string'}), 400
        name = body['name'].strip()
        if len(name) > MAX_NAME_LEN:
            return jsonify({'error': f'name max {MAX_NAME_LEN} chars'}), 400
        cleaned['name'] = name
    if 'color' in body:
        if not _validate_color(body['color']):
            return jsonify({'error': 'color must be valid hex starting with #'}), 400
        cleaned['color'] = body['color']
    if 'instructions' in body:
        if body['instructions'] is None:
            cleaned['instructions'] = ''
        else:
            if not isinstance(body['instructions'], str):
                return jsonify({'error': 'instructions must be string'}), 400
            if len(body['instructions']) > MAX_INSTRUCTIONS_LEN:
                return jsonify({'error': f'instructions max {MAX_INSTRUCTIONS_LEN} chars'}), 400
            cleaned['instructions'] = body['instructions']

    if not cleaned:
        return jsonify({'error': 'no editable fields provided'}), 400

    ok = _service().update(uid, project_id, cleaned)
    if not ok:
        return jsonify({'error': 'Project not found'}), 404
    return ('', 204)


@project_bp.route('/projects/<project_id>', methods=['DELETE'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def delete_project(project_id):
    uid = session['user']['uid']
    cascade = (request.args.get('cascade') or 'keep').lower()
    if cascade not in ('keep', 'delete'):
        return jsonify({'error': "cascade must be 'keep' or 'delete'"}), 400

    from google.cloud.firestore_v1.base_query import FieldFilter
    sessions_ref = db.collection('users').document(uid).collection('chat_sessions')
    child_ids = [
        snap.id for snap in
        sessions_ref.where(filter=FieldFilter('project_id', '==', project_id)).stream()
    ]

    if cascade == 'delete' and child_ids:
        chat_session.bulk_delete_sessions(uid, child_ids)
    elif cascade == 'keep' and child_ids:
        batch = db.batch()
        for cid in child_ids:
            batch.update(sessions_ref.document(cid), {'project_id': None})
        batch.commit()

    ok = _service().delete(uid, project_id)
    if not ok:
        return jsonify({'error': 'Project not found'}), 404
    return ('', 204)
```

- [ ] **Step 4: Register the blueprint**

In `app/__init__.py`, in the block at lines 195-213, add:

```python
from app.blueprints.project_routes import project_bp
app.register_blueprint(project_bp)
```

(Add it just before the `status_bp` registration.)

- [ ] **Step 5: Run the route tests**

Run: `pytest tests/test_project_routes.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/project_routes.py app/__init__.py tests/test_project_routes.py
git commit -m "feat(projects): CRUD blueprint with cascade-on-delete"
```

---

### Task 0.4: History feed filtering — `?project_id=…` and `?ungrouped=1`

**Files:**
- Modify: `app/chat_session.py:get_history`, add `count_grouped`
- Modify: `app/blueprints/session_routes.py:450`
- Test: `tests/test_chat_history_project_filter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chat_history_project_filter.py`:

```python
"""GET /chat/history must support project_id and ungrouped filters."""
import pytest


def test_history_default_includes_all(authed_client, two_sessions):
    a, b = two_sessions
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    authed_client.patch(f"/chat/{b['session_id']}", json={'project_id': pid})

    resp = authed_client.get('/chat/history')
    assert resp.status_code == 200
    ids = _ids(resp.json['categorized_history'])
    assert a['session_id'] in ids
    assert b['session_id'] in ids


def test_ungrouped_hides_in_project_sessions(authed_client, two_sessions):
    a, b = two_sessions
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    authed_client.patch(f"/chat/{b['session_id']}", json={'project_id': pid})

    resp = authed_client.get('/chat/history?ungrouped=1')
    ids = _ids(resp.json['categorized_history'])
    assert a['session_id'] in ids
    assert b['session_id'] not in ids
    assert resp.json['grouped_count'] == 1


def test_project_id_filter_returns_only_that_project(authed_client, two_sessions):
    a, b = two_sessions
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    authed_client.patch(f"/chat/{b['session_id']}", json={'project_id': pid})

    resp = authed_client.get(f'/chat/history?project_id={pid}')
    ids = _ids(resp.json['categorized_history'])
    assert b['session_id'] in ids
    assert a['session_id'] not in ids


def _ids(categorized):
    out = set()
    for bucket in categorized.values():
        for chat in bucket:
            out.add(chat['session_id'])
    return out
```

If a `two_sessions` fixture is not already in `tests/conftest.py`, add it:

```python
@pytest.fixture
def two_sessions(authed_client, firestore_db, test_user_id):
    from datetime import datetime
    ref = firestore_db.collection('users').document(test_user_id).collection('chat_sessions')
    out = []
    for i in range(2):
        doc = ref.document()
        doc.set({
            'session_name': f'Test {i}',
            'created_at': datetime.utcnow(),
            'last_updated': datetime.utcnow(),
            'messages': [],
            'pages': [],
            'status': 'active',
        })
        out.append({'session_id': doc.id})
    return out
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_chat_history_project_filter.py -v`
Expected: all fail.

- [ ] **Step 3: Extend `get_history()` in `app/chat_session.py`**

Find `get_history()` at line 197. Change its signature and head:

```python
def get_history(
    self,
    user_id: str,
    page_size: int = 10,
    page_token: Optional[str] = None,
    project_id: Optional[str] = None,
    ungrouped: bool = False,
) -> Dict[str, Any]:
    from google.cloud.firestore_v1.base_query import FieldFilter
    sessions_query = self._get_sessions_query(user_id, page_token)
    if project_id is not None:
        sessions_query = sessions_query.where(filter=FieldFilter('project_id', '==', project_id))
    elif ungrouped:
        sessions_query = sessions_query.where(filter=FieldFilter('project_id', '==', None))
    sessions = sessions_query.limit(page_size + 1).stream()
    # ... (rest of method unchanged)
```

Add a helper at the end of the class:

```python
def count_grouped(self, user_id: str) -> int:
    """Return the count of sessions whose project_id is not null.

    Firestore '!=' queries require composite indexes. If unavailable,
    fall back to: total_active_count - ungrouped_count.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter
    ref = (
        self.db.collection('users').document(user_id)
        .collection('chat_sessions')
        .where(filter=FieldFilter('project_id', '!=', None))
    )
    return sum(1 for _ in ref.stream())
```

- [ ] **Step 4: Update the route**

In `app/blueprints/session_routes.py:450`:

```python
@session_bp.route('/chat/history', methods=['GET'])
@error_handler
@login_required
def get_chat_history():
    """Get paginated chat history for the user, optionally filtered by project."""
    user_id = session['user']['uid']
    page_size = request.args.get('page_size', default=10, type=int)
    page_token = request.args.get('page_token', default=None, type=str)
    project_id = request.args.get('project_id', default=None, type=str)
    ungrouped = request.args.get('ungrouped', default='0') == '1'

    history = chat_session.get_history(
        user_id, page_size, page_token,
        project_id=project_id, ungrouped=ungrouped,
    )
    chat_history = history.get('chat_history', [])
    if not isinstance(chat_history, list):
        logger.warning("Invalid 'chat_history' format.")
        raise TypeError("Expected 'chat_history' to be a list of dictionaries.")

    categorized_history = categorize_sessions(chat_history)

    payload = {
        'categorized_history': categorized_history,
        'next_page_token': history.get('next_page_token'),
    }
    if ungrouped and not project_id:
        payload['grouped_count'] = chat_session.count_grouped(user_id)

    return make_etag_response(payload)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_chat_history_project_filter.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/chat_session.py app/blueprints/session_routes.py tests/test_chat_history_project_filter.py tests/conftest.py
git commit -m "feat(projects): project_id and ungrouped filters on /chat/history"
```

---

### Task 0.5: Firestore rules for `projects` subcollection

**Files:**
- Modify: `firestore.rules`

- [ ] **Step 1: Read current rules**

Run: `cat firestore.rules`. Locate the `match /users/{uid}` block and the existing `chat_sessions` rule.

- [ ] **Step 2: Add the projects rules**

Inside `match /users/{uid}` add:

```javascript
match /projects/{projectId} {
  allow read, write: if request.auth != null && request.auth.uid == uid;
  match /files/{fileId} {
    allow read, write: if request.auth != null && request.auth.uid == uid;
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add firestore.rules
git commit -m "feat(projects): firestore rules for projects + files subcollection"
```

---

### Task 0.6: M0 sanity check

- [ ] **Step 1: Run all sphere/project-touching tests**

Run: `pytest tests/ -v -k "project"`
Expected: every test PASS.

- [ ] **Step 2: Run the full suite to confirm no regressions**

Run: `pytest tests/ -v`
Expected: no new failures relative to a `main` baseline run.

- [ ] **Step 3: Manual smoke via curl (server running locally)**

```bash
curl -X POST http://localhost:5000/projects \
  -H 'Content-Type: application/json' -H 'X-CSRFToken: <csrf>' \
  -b 'session=<your cookie>' \
  -d '{"name":"Copenhagen Trip"}'

curl http://localhost:5000/projects -b 'session=<your cookie>'
```

Confirm: project created with auto color, list returns it. Patch instructions, delete with cascade=keep, confirm session survives.

- [ ] **Step 4: Tag M0**

```bash
git tag projects-m0
```

---

## Milestone 1 — Sidebar Projects section + Create modal

Replaces the "Projects · Coming soon" placeholder with the real section. Users can create Projects via a ChatGPT-style modal and see them listed. Drag-drop and Add-to-project menu wiring come in M4; this milestone is "see and create".

### Task 1.1: Replace `#action-projects` placeholder with Projects section markup

**Files:**
- Modify: `app/templates/index.html:489-498`

- [ ] **Step 1: Confirm exact line range of the placeholder**

Run: `grep -n 'action-projects\|sidebar-secondary-nav' app/templates/index.html | head`. Confirm the placeholder `<button id="action-projects" …>` block starts at 489.

- [ ] **Step 2: Replace the placeholder with the Projects section**

Replace `<button id="action-projects" …>…</button>` (lines 489-498) with:

```html
<!-- Projects section: replaces the prior "Projects · Coming soon" placeholder. -->
<section id="projects-section" class="sidebar-projects" aria-label="Projects">
  <header class="sidebar-projects-header">
    <span class="sidebar-projects-title">Projects</span>
    <button type="button" id="project-create-btn" class="sidebar-projects-add" aria-label="Create project" data-tooltip="New project">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
    </button>
  </header>
  <ul id="project-list" class="sidebar-projects-list" role="list"></ul>
</section>
<div id="project-more-hint" class="sidebar-projects-more-hint" hidden></div>
```

- [ ] **Step 3: Add the Create-project modal at end of `<body>`**

Just before `</body>`, append:

```html
<div id="project-create-modal" class="project-modal" hidden role="dialog" aria-modal="true" aria-labelledby="project-modal-title">
  <div class="project-modal-backdrop" data-dismiss="modal"></div>
  <div class="project-modal-content">
    <h2 id="project-modal-title" class="project-modal-heading">Create project</h2>
    <label class="project-modal-label" for="project-name-input">Project name</label>
    <input id="project-name-input" class="project-modal-input" type="text" maxlength="120" placeholder="Copenhagen Trip" autocomplete="off" />
    <p class="project-modal-helper">Projects keep chats, files, and custom instructions in one place. Use them for ongoing work, or just to keep things tidy.</p>
    <div class="project-modal-actions">
      <button type="button" class="project-modal-cancel" data-dismiss="modal">Cancel</button>
      <button type="button" id="project-modal-create" class="project-modal-create" disabled>Create project</button>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Link the new stylesheet in `<head>`**

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/projects.css') }}">
```

- [ ] **Step 5: Commit**

```bash
git add app/templates/index.html
git commit -m "feat(projects): sidebar section + create modal markup (replaces placeholder)"
```

---

### Task 1.2: Projects CSS

**Files:**
- Create: `app/static/css/projects.css`

- [ ] **Step 1: Read existing sidebar styles for tokens**

Run: `head -120 app/static/css/sidebarContent.css`. Note tokens used (e.g. `var(--text-primary)`, `var(--border-secondary)`, hover backgrounds) and reuse them.

- [ ] **Step 2: Write the stylesheet**

Create `app/static/css/projects.css`:

```css
/* Projects — sidebar section, create modal, landing page.
   Spec: docs/superpowers/specs/2026-05-22-projects-design.md */

/* ---------- Sidebar section ---------- */
.sidebar-projects {
  padding: 8px 4px;
  border-bottom: 1px solid var(--border-secondary, rgba(255,255,255,0.07));
}

.sidebar-projects-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text-tertiary, rgba(255,255,255,0.5));
}

.sidebar-projects-add {
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 120ms ease;
}
.sidebar-projects-add:hover {
  background: var(--bg-hover, rgba(255,255,255,0.06));
}

.sidebar-projects-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.sidebar-project-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  cursor: pointer;
  border-radius: 6px;
  margin: 1px 4px;
  text-decoration: none;
  color: inherit;
  transition: background 120ms ease;
}
.sidebar-project-row:hover {
  background: var(--bg-hover, rgba(255,255,255,0.06));
}
.sidebar-project-row[data-drag-over='true'] {
  background: color-mix(in srgb, var(--project-accent, #2563eb) 18%, transparent);
}
.sidebar-project-row .folder-icon {
  flex: 0 0 18px;
  height: 18px;
  width: 18px;
  color: var(--project-accent, #2563eb);
}
.sidebar-project-row .name {
  flex: 1;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-projects-more-hint {
  padding: 8px 12px;
  font-size: 12px;
  font-style: italic;
  color: var(--text-tertiary, rgba(255,255,255,0.45));
}

/* ---------- Create modal ---------- */
.project-modal {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.project-modal[hidden] { display: none; }

.project-modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0,0,0,0.5);
  backdrop-filter: blur(2px);
}

.project-modal-content {
  position: relative;
  width: min(520px, calc(100vw - 32px));
  background: var(--bg-elevated, #2b2a27);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
  border-radius: 14px;
  padding: 24px;
  box-shadow: 0 12px 36px rgba(0,0,0,0.5);
}

.project-modal-heading {
  margin: 0 0 16px;
  font-size: 18px;
  font-weight: 600;
}

.project-modal-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
  color: var(--text-secondary, rgba(255,255,255,0.7));
}

.project-modal-input {
  width: 100%;
  background: var(--bg-base, #1f1e1c);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.12));
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 15px;
  color: inherit;
  outline: none;
  transition: border-color 120ms ease;
}
.project-modal-input:focus {
  border-color: var(--accent, #2563eb);
}

.project-modal-helper {
  margin: 12px 0 20px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-tertiary, rgba(255,255,255,0.55));
}

.project-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.project-modal-cancel,
.project-modal-create {
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 14px;
  cursor: pointer;
  border: 1px solid var(--border-primary, rgba(255,255,255,0.12));
  background: transparent;
  color: inherit;
}
.project-modal-create {
  background: var(--accent, #2563eb);
  border-color: var(--accent, #2563eb);
  color: white;
}
.project-modal-create:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ---------- "in: <project>" tag at top of chat area ---------- */
.project-context-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--project-accent, #2563eb) 14%, transparent);
  color: var(--project-accent, #2563eb);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
}
.project-context-tag .folder-icon { width: 12px; height: 12px; }
```

- [ ] **Step 3: Commit**

```bash
git add app/static/css/projects.css
git commit -m "feat(projects): sidebar + modal CSS"
```

---

### Task 1.3: `projectsModule.js` — state, list render, create modal

**Files:**
- Create: `app/static/js/projectsModule.js`
- Modify: `app/templates/index.html` (add `<script>`)
- Modify: `app/static/js/sidebarHandler.js` (bootstrap call)

- [ ] **Step 1: Create the module**

Create `app/static/js/projectsModule.js`:

```javascript
/**
 * projectsModule.js — Projects sidebar state, list render, create modal.
 * Spec: docs/superpowers/specs/2026-05-22-projects-design.md
 */
(function () {
  'use strict';

  const FOLDER_SVG = `
    <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>
      <path d="M2 10h20"/>
    </svg>
  `;

  const state = { projects: [], loaded: false };
  const els = {};

  function cache() {
    els.section = document.getElementById('projects-section');
    els.list = document.getElementById('project-list');
    els.createBtn = document.getElementById('project-create-btn');
    els.moreHint = document.getElementById('project-more-hint');
    els.modal = document.getElementById('project-create-modal');
    els.modalInput = document.getElementById('project-name-input');
    els.modalCreate = document.getElementById('project-modal-create');
  }

  async function fetchProjects() {
    const resp = await fetch('/projects', { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`GET /projects ${resp.status}`);
    return (await resp.json()).projects || [];
  }

  function renderList() {
    if (!els.list) return;
    els.list.innerHTML = '';
    state.projects.forEach((project) => {
      const li = document.createElement('li');
      li.className = 'sidebar-project-row';
      li.dataset.projectId = project.id;
      li.style.setProperty('--project-accent', project.color);
      li.innerHTML = `${FOLDER_SVG}<span class="name">${escapeHtml(project.name)}</span>`;
      li.addEventListener('click', () => {
        window.location.href = `/projects/${encodeURIComponent(project.id)}`;
      });
      attachDropTarget(li, project.id);
      els.list.appendChild(li);
    });
  }

  /* ---------- Create modal ---------- */
  function openModal() {
    els.modal.hidden = false;
    els.modalInput.value = '';
    els.modalCreate.disabled = true;
    els.modalInput.focus();
  }
  function closeModal() {
    els.modal.hidden = true;
  }

  function bindModal() {
    if (!els.modal) return;
    els.createBtn?.addEventListener('click', openModal);
    els.modal.querySelectorAll('[data-dismiss="modal"]').forEach((el) => {
      el.addEventListener('click', closeModal);
    });
    els.modalInput.addEventListener('input', () => {
      els.modalCreate.disabled = !els.modalInput.value.trim();
    });
    els.modalInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !els.modalCreate.disabled) {
        e.preventDefault();
        commit();
      } else if (e.key === 'Escape') {
        closeModal();
      }
    });
    els.modalCreate.addEventListener('click', commit);
  }

  async function commit() {
    const name = els.modalInput.value.trim();
    if (!name) return;
    els.modalCreate.disabled = true;
    try {
      const resp = await fetch('/projects', {
        method: 'POST',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ name }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const { project } = await resp.json();
      closeModal();
      // Navigate to the new Project landing page.
      window.location.href = `/projects/${encodeURIComponent(project.id)}`;
    } catch (err) {
      window.toast?.error?.(`Couldn't create project: ${err.message}`);
      els.modalCreate.disabled = false;
    }
  }

  /* ---------- Drag-drop (move chat into project) ---------- */
  function attachDropTarget(rowEl, projectId) {
    rowEl.addEventListener('dragover', (e) => {
      if (!e.dataTransfer.types.includes('application/x-session-id')) return;
      e.preventDefault();
      rowEl.dataset.dragOver = 'true';
    });
    rowEl.addEventListener('dragleave', () => { delete rowEl.dataset.dragOver; });
    rowEl.addEventListener('drop', async (e) => {
      delete rowEl.dataset.dragOver;
      const sessionId = e.dataTransfer.getData('application/x-session-id');
      if (!sessionId) return;
      e.preventDefault();
      await fetch(`/chat/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ project_id: projectId }),
      });
      window.dispatchEvent(new CustomEvent('projects:session-moved', { detail: { sessionId, projectId } }));
    });
  }

  /* ---------- Helpers ---------- */
  function jsonHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    if (csrf) h['X-CSRFToken'] = csrf;
    return h;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  async function reload() {
    state.projects = await fetchProjects();
    state.loaded = true;
    renderList();
  }

  async function init() {
    cache();
    if (!els.section) return;
    bindModal();
    try { await reload(); } catch (e) { console.warn('projects: load failed', e); }
  }

  window.projects = { init, reload, getList: () => state.projects.slice() };
})();
```

- [ ] **Step 2: Add `<script>` tag in `index.html`**

Near other module script tags (before `</body>`):

```html
<script src="{{ url_for('static', filename='js/projectsModule.js') }}?v=1" defer></script>
```

- [ ] **Step 3: Bootstrap from `sidebarHandler.js`**

Locate where `startNewChatModule` initializes; after that line add:

```javascript
window.projects?.init?.();
```

- [ ] **Step 4: Manual smoke**

Reload app. Expected:
- Projects header appears above the chat list with a small "+" button.
- Click "+" → modal opens, helper text is the ChatGPT copy.
- Type "Copenhagen Trip" → Create project becomes enabled → click → navigates to `/projects/<id>` (this URL returns 404 right now — that's M2's job).
- Back to sidebar: reload → the project row appears.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/projectsModule.js app/static/js/sidebarHandler.js app/templates/index.html
git commit -m "feat(projects): client module — list, create modal"
```

---

### Task 1.4: Hide in-project sessions from time buckets + "+N more" hint

**Files:**
- Modify: `app/static/js/startNewChatModule.js`

- [ ] **Step 1: Find the history fetch site**

Run: `grep -n "/chat/history" app/static/js/startNewChatModule.js`. Identify the fetch URL used by the sidebar's history loader.

- [ ] **Step 2: Append `?ungrouped=1` at top level**

At that fetch site, change the URL to include `ungrouped=1`:

```javascript
const params = new URLSearchParams();
params.set('page_size', '50');  // or whatever existing value is
const activeProject = window.location.pathname.startsWith('/projects/') ? ... : null;
if (activeProject) params.set('project_id', activeProject);
else params.set('ungrouped', '1');
// fetch(`/chat/history?${params}`)
```

(Adapt to the existing fetch call signature — most likely a single-line `fetch('/chat/history?...')`.)

- [ ] **Step 3: Capture `grouped_count` from the response**

After the response parses, store:

```javascript
this.groupedCount = response.grouped_count || 0;
```

- [ ] **Step 4: Render the "+N more" hint**

At the end of `renderChatTabs()` (after the existing "All chats" footer block), add:

```javascript
const hint = document.getElementById('project-more-hint');
if (hint) {
  if (this.groupedCount > 0) {
    hint.textContent = `+ ${this.groupedCount} more in projects`;
    hint.hidden = false;
  } else {
    hint.hidden = true;
  }
}
```

- [ ] **Step 5: Re-render after move**

Below the module init or in `sidebarHandler.js`:

```javascript
window.addEventListener('projects:session-moved', () => {
  window.startNewChatModule?.fetchAndRender?.();
});
```

(Confirm the actual exposed method name via grep — adapt.)

- [ ] **Step 6: Manual smoke**

- Create a project, then in another tab move a session into it via the API (`curl -X PATCH /chat/<id> -d '{"project_id":"<pid>"}'`) and reload the sidebar. Expected: the moved chat disappears from Today, and `+ 1 more in projects` hint appears below the chat list.

- [ ] **Step 7: Commit**

```bash
git add app/static/js/startNewChatModule.js
git commit -m "feat(projects): hide in-project chats from time buckets; render '+N more' hint"
```

---

### Task 1.5: M1 sanity check + tag

- [ ] **Step 1: Manual smoke (golden path so far)**

1. Reload app, click `+` next to Projects header.
2. Modal opens with exact ChatGPT helper copy.
3. Type "Copenhagen Trip" → Create project → URL becomes `/projects/<id>` (404 page is fine for now).
4. Hit back, reload → "Copenhagen Trip" appears in the Projects section.

- [ ] **Step 2: Tag M1**

```bash
git tag projects-m1
```

---

## Milestone 2 — Project landing page + Instructions

The `/projects/<id>` URL becomes a real page: title, instructions editor with autosave, chats list, "+ New chat in this project". Files come in M3.

### Task 2.1: Add the `/projects/<id>` page route + template

**Files:**
- Modify: `app/blueprints/pages_routes.py`
- Create: `app/templates/projects_page.html`

- [ ] **Step 1: Locate the existing pages blueprint**

Run: `grep -n "@pages_bp.route\|render_template" app/blueprints/pages_routes.py | head -10`. Note the pattern.

- [ ] **Step 2: Add the route**

In `app/blueprints/pages_routes.py`, add:

```python
from app.project_service import ProjectService
from app.config import db

@pages_bp.route('/projects/<project_id>', methods=['GET'])
@login_required
def project_page(project_id):
    uid = session['user']['uid']
    project = ProjectService(db).get(uid, project_id)
    if not project:
        return render_template('404.html'), 404  # or use existing 404 pattern
    return render_template('projects_page.html', project=project)
```

(Match the existing imports and `@login_required` pattern in the file.)

- [ ] **Step 3: Create the template**

Create `app/templates/projects_page.html`:

```html
{% extends "base.html" %}
{% block title %}{{ project.name }} · XelaSphere{% endblock %}

{% block content %}
<main class="project-page" data-project-id="{{ project.id }}" style="--project-accent: {{ project.color }};">
  <header class="project-page-header">
    <a href="/" class="project-page-back" aria-label="Back to chats">←</a>
    <svg class="project-page-folder" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>
      <path d="M2 10h20"/>
    </svg>
    <h1 class="project-page-title" id="project-title" contenteditable="false">{{ project.name }}</h1>
    <button type="button" id="project-rename" class="project-page-edit" aria-label="Rename project">Rename</button>
    <button type="button" id="project-menu" class="project-page-menu" aria-label="Project options">⋯</button>
  </header>

  <button type="button" id="project-new-chat" class="project-new-chat-btn">+ New chat in this project</button>

  <section class="project-section">
    <h2 class="project-section-title">Instructions</h2>
    <textarea id="project-instructions" class="project-instructions"
              maxlength="8000" placeholder="How should the AI respond in this project?">{{ project.instructions or '' }}</textarea>
    <div class="project-instructions-status" id="project-instructions-status" aria-live="polite"></div>
  </section>

  <section class="project-section">
    <h2 class="project-section-title">Files</h2>
    <div class="project-files-empty">Files come in the next release.</div>
  </section>

  <section class="project-section">
    <h2 class="project-section-title">Chats</h2>
    <ul id="project-chats" class="project-chats" role="list"></ul>
  </section>
</main>
<script src="{{ url_for('static', filename='js/projectsPage.js') }}?v=1" defer></script>
{% endblock %}
```

(If the project doesn't use Jinja `extends`, adapt to whatever bare-template pattern is in use. Run `head -20 app/templates/index.html` to see the convention.)

- [ ] **Step 4: Add page CSS** in `app/static/css/projects.css`

Append:

```css
.project-page {
  max-width: 860px;
  margin: 0 auto;
  padding: 32px 24px;
}
.project-page-header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 24px;
}
.project-page-back {
  font-size: 18px; text-decoration: none; color: var(--text-secondary, rgba(255,255,255,0.7));
}
.project-page-folder { width: 28px; height: 28px; color: var(--project-accent); }
.project-page-title { flex: 1; font-size: 24px; font-weight: 600; margin: 0; }
.project-page-edit,
.project-page-menu {
  background: transparent; border: 1px solid var(--border-primary, rgba(255,255,255,0.12));
  border-radius: 8px; padding: 4px 10px; font-size: 13px; color: inherit; cursor: pointer;
}

.project-new-chat-btn {
  width: 100%;
  padding: 14px 18px;
  background: var(--project-accent);
  color: white;
  border: none;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  margin-bottom: 32px;
}

.project-section { margin: 28px 0; }
.project-section-title {
  font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--text-tertiary, rgba(255,255,255,0.5));
  margin: 0 0 10px;
}

.project-instructions {
  width: 100%;
  min-height: 120px;
  resize: vertical;
  background: var(--bg-elevated, #2b2a27);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
  border-radius: 10px;
  padding: 12px 14px;
  color: inherit;
  font-size: 14px;
  line-height: 1.55;
  outline: none;
  font-family: inherit;
}
.project-instructions:focus { border-color: var(--project-accent); }

.project-instructions-status {
  font-size: 12px;
  color: var(--text-tertiary, rgba(255,255,255,0.45));
  margin-top: 4px;
  min-height: 16px;
}

.project-chats {
  list-style: none;
  padding: 0;
  margin: 0;
}
.project-chat-row {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
}
.project-chat-row:hover { background: var(--bg-hover, rgba(255,255,255,0.05)); }
.project-chat-row .title { flex: 1; font-size: 14px; }
.project-chat-row .updated { font-size: 12px; color: var(--text-tertiary); }
```

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/pages_routes.py app/templates/projects_page.html app/static/css/projects.css
git commit -m "feat(projects): /projects/<id> landing page route + template"
```

---

### Task 2.2: `projectsPage.js` — instructions autosave, chats list, new-chat CTA

**Files:**
- Create: `app/static/js/projectsPage.js`

- [ ] **Step 1: Implement the page module**

Create `app/static/js/projectsPage.js`:

```javascript
/**
 * projectsPage.js — Project landing page.
 * Spec: docs/superpowers/specs/2026-05-22-projects-design.md
 */
(function () {
  'use strict';

  const root = document.querySelector('.project-page');
  if (!root) return;

  const projectId = root.dataset.projectId;
  const els = {
    title: document.getElementById('project-title'),
    rename: document.getElementById('project-rename'),
    instructions: document.getElementById('project-instructions'),
    status: document.getElementById('project-instructions-status'),
    newChat: document.getElementById('project-new-chat'),
    chats: document.getElementById('project-chats'),
  };

  /* ---------- Instructions autosave ---------- */
  let saveTimer = null;
  els.instructions.addEventListener('input', () => {
    setStatus('Saving…');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveInstructions, 1500);
  });
  els.instructions.addEventListener('blur', () => {
    clearTimeout(saveTimer);
    saveInstructions();
  });

  async function saveInstructions() {
    try {
      const resp = await fetch(`/projects/${encodeURIComponent(projectId)}`, {
        method: 'PATCH',
        headers: jsonHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify({ instructions: els.instructions.value }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setStatus('Saved');
      setTimeout(() => setStatus(''), 1200);
    } catch (e) {
      setStatus('Save failed');
    }
  }

  function setStatus(text) { els.status.textContent = text; }

  /* ---------- Rename ---------- */
  els.rename?.addEventListener('click', () => {
    const next = window.prompt('Rename project:', els.title.textContent);
    if (!next || next.trim() === els.title.textContent) return;
    fetch(`/projects/${encodeURIComponent(projectId)}`, {
      method: 'PATCH', headers: jsonHeaders(), credentials: 'same-origin',
      body: JSON.stringify({ name: next.trim() }),
    }).then((r) => {
      if (r.ok) {
        els.title.textContent = next.trim();
        document.title = `${next.trim()} · XelaSphere`;
      }
    });
  });

  /* ---------- New chat in project ---------- */
  els.newChat.addEventListener('click', async () => {
    /* Create a new chat with project_id pre-set. Adapts to whatever
       new-chat endpoint exists. If the existing flow is client-side
       only, set a sentinel and redirect to the chat URL with project=ID. */
    window.location.href = `/?new=1&project_id=${encodeURIComponent(projectId)}`;
  });

  /* ---------- Chats list ---------- */
  loadChats();

  async function loadChats() {
    const resp = await fetch(`/chat/history?project_id=${encodeURIComponent(projectId)}&page_size=200`, {
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const body = await resp.json();
    const all = Object.values(body.categorized_history || {}).flat();
    renderChats(all);
  }

  function renderChats(chats) {
    els.chats.innerHTML = '';
    if (!chats.length) {
      els.chats.innerHTML = '<li class="project-chats-empty">No chats yet — start one above.</li>';
      return;
    }
    chats.forEach((c) => {
      const li = document.createElement('li');
      li.className = 'project-chat-row';
      li.innerHTML = `<span class="title">${escapeHtml(c.session_name)}</span>
                      <span class="updated">${relTime(c.last_updated)}</span>`;
      li.addEventListener('click', () => {
        window.location.href = `/?session=${encodeURIComponent(c.session_id)}`;
      });
      els.chats.appendChild(li);
    });
  }

  /* ---------- Helpers ---------- */
  function jsonHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    if (csrf) h['X-CSRFToken'] = csrf;
    return h;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
  function relTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  }
})();
```

- [ ] **Step 2: Wire the chat creator to honor `project_id` query param**

In the main chat module (likely `app/static/js/startNewChatModule.js` — find the new-chat logic), when the URL contains `?new=1&project_id=X`, call the chat-creation function with `{project_id: X}` in its options, then strip the query params. Example after the existing init:

```javascript
const initialParams = new URL(window.location.href).searchParams;
if (initialParams.get('new') === '1' && initialParams.get('project_id')) {
  const pid = initialParams.get('project_id');
  window.history.replaceState({}, '', window.location.pathname);
  window.chatManager?.createNewChat?.(null, { project_id: pid });
}
```

The existing `createNewChat` signature already accepts an options object — pass `{ project_id }` through to whatever POST it makes to start a session, and have that endpoint persist `project_id` on the new session doc. If the new-chat handler doesn't currently round-trip extra fields, add minimal support: server-side, when creating a new session and the body contains `project_id`, write it to the session doc.

- [ ] **Step 3: Manual smoke**

- Go to `/projects/<id>` from a created Project.
- Type instructions → wait 1.5s → "Saved" appears.
- Click "+ New chat in this project" → returns to `/`, a new chat appears with `project_id` set (verify with curl `/chat/session/<id>`).

- [ ] **Step 4: Commit**

```bash
git add app/static/js/projectsPage.js app/static/js/startNewChatModule.js
git commit -m "feat(projects): landing page — instructions autosave, chats list, new-chat CTA"
```

---

### Task 2.3: M2 sanity + tag

- [ ] **Step 1: Full manual smoke**

1. Create project from sidebar.
2. Add instructions, see Saved indicator.
3. Click "+ New chat in this project" → new chat opens.
4. Send a message in the new chat → confirm `project_id` is persisted (next milestone will actually USE this in the system prompt).
5. Return to landing page → chat appears in the Chats list.

- [ ] **Step 2: Tag**

```bash
git tag projects-m2
```

---

## Milestone 3 — Files + System-prompt injection

The big one. Files can be uploaded to a Project; their extracted text is injected into the system prompt for every chat in the Project alongside the Project's instructions.

### Task 3.1: `project_context` helper — assembles the system-prompt addition

**Files:**
- Create: `app/project_context.py`
- Test: `tests/test_project_context.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_project_context.py`:

```python
"""Pure helper that assembles the Project context block for system prompt injection."""
import pytest
from app.project_context import build_project_context_block, INJECTION_BUDGET_CHARS


def test_empty_project_returns_empty_string():
    assert build_project_context_block(
        instructions='', files=[],
    ) == ''


def test_instructions_only():
    block = build_project_context_block(instructions='Be warm.', files=[])
    assert '--- Project instructions ---' in block
    assert 'Be warm.' in block
    assert '--- Project knowledge ---' not in block


def test_one_file_under_budget():
    files = [
        {'filename': 'a.txt', 'extracted_text': 'hello'},
    ]
    block = build_project_context_block(instructions='', files=files)
    assert '--- Project knowledge ---' in block
    assert 'a.txt' in block
    assert 'hello' in block


def test_files_with_instructions():
    block = build_project_context_block(
        instructions='Be terse.',
        files=[{'filename': 'a.txt', 'extracted_text': 'hello'}],
    )
    assert block.index('--- Project instructions ---') < block.index('--- Project knowledge ---')


def test_budget_truncates_oldest_first():
    big = 'x' * INJECTION_BUDGET_CHARS  # one file fills the whole budget
    files = [
        {'filename': 'a.txt', 'extracted_text': big},
        {'filename': 'b.txt', 'extracted_text': 'world'},
    ]
    block = build_project_context_block(instructions='', files=files)
    # a.txt is fully included; b.txt may be truncated / omitted.
    assert 'a.txt' in block
    assert 'Some project files were truncated' in block


def test_budget_with_overflow_partial_inclusion():
    chunk = 'x' * (INJECTION_BUDGET_CHARS // 2)
    files = [
        {'filename': 'a.txt', 'extracted_text': chunk},
        {'filename': 'b.txt', 'extracted_text': chunk + 'overflow'},
    ]
    block = build_project_context_block(instructions='', files=files)
    assert 'a.txt' in block
    # b.txt's overflow gets truncated.
    assert 'overflow' not in block or 'truncated' in block.lower()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_project_context.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the helper**

Create `app/project_context.py`:

```python
"""Pure helper — builds the Project context block prepended to the system prompt
when a chat session belongs to a Project. Spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

from typing import List, Dict

INJECTION_BUDGET_CHARS = 80_000


def build_project_context_block(*, instructions: str, files: List[Dict[str, str]]) -> str:
    """Assemble the addendum that gets concatenated to the system prompt.

    Files are included whole oldest-first; if one would push past the budget,
    it is included partially and the rest are dropped. A trailing notice is
    appended if any truncation occurred.
    """
    parts: List[str] = []
    instructions = (instructions or '').strip()
    if instructions:
        parts.append('--- Project instructions ---')
        parts.append(instructions)

    truncated = False
    files_parts: List[str] = []
    budget = INJECTION_BUDGET_CHARS
    for f in files:
        text = (f.get('extracted_text') or '').strip()
        if not text:
            continue
        header = f"\n{f.get('filename', 'file')}:"
        # Subtract the header from the budget calculation.
        budget_for_body = budget - len(header)
        if budget_for_body <= 0:
            truncated = True
            break
        if len(text) <= budget_for_body:
            files_parts.append(header)
            files_parts.append(text)
            budget -= len(header) + len(text)
        else:
            files_parts.append(header)
            files_parts.append(text[:budget_for_body])
            truncated = True
            budget = 0
            break

    if files_parts:
        parts.append('--- Project knowledge ---')
        parts.extend(files_parts)
        if truncated:
            parts.append('\n(Some project files were truncated to fit in context.)')

    if not parts:
        return ''
    return '\n\n'.join(parts).strip() + '\n'
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_project_context.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/project_context.py tests/test_project_context.py
git commit -m "feat(projects): project_context helper — builds system-prompt addendum"
```

---

### Task 3.2: Wire system-prompt injection in `chat_routes.py`

**Files:**
- Modify: `app/blueprints/chat_routes.py:1183` and `:3154`
- Test: `tests/test_chat_system_prompt_injection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_system_prompt_injection.py`:

```python
"""When a session is in a Project, the system prompt sees Project context."""
import pytest


def test_system_prompt_contains_project_instructions(authed_client, monkeypatch):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    authed_client.patch(f'/projects/{pid}', json={'instructions': 'Be warm and Welsh.'})

    # Create a chat in the project.
    sid_resp = authed_client.post('/chat/new', json={'project_id': pid})
    assert sid_resp.status_code in (200, 201)
    sid = sid_resp.json['session']['session_id']

    captured = {}
    from app.blueprints import chat_routes
    def fake_stream(*args, **kwargs):
        captured['system_prompt'] = kwargs.get('system_prompt') or (args[0] if args else None)
        # Yield a single chunk and exit.
        yield 'ok'
    monkeypatch.setattr(chat_routes, 'stream_groq_response', fake_stream, raising=False)

    authed_client.post('/chat/send', json={'session_id': sid, 'message': 'hi'})

    assert captured.get('system_prompt'), 'no system prompt was captured'
    assert 'Be warm and Welsh' in captured['system_prompt']
    assert '--- Project instructions ---' in captured['system_prompt']
```

(Names of `stream_groq_response` / `/chat/new` / `/chat/send` may differ — substitute whatever the codebase actually exposes. The shape of the test is: patch the LLM-call site, send a message, assert the system_prompt arg contains Project context.)

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_chat_system_prompt_injection.py -v`
Expected: fail — the injection isn't wired.

- [ ] **Step 3: Inject Project context in `chat_routes.py:1183`**

Find the existing line `base_system = SYSTEM_INSTRUCTIONS + "\n" + get_developer_instructions(stream_model)` at line 1183. Just after the existing additions (line 1201 — `charter`), add:

```python
# Project context — if this session belongs to a Project, prepend the
# Project's instructions and file knowledge to the system prompt.
project_id = session_data.get('project_id') if 'session_data' in locals() else None
if not project_id:
    # Fall back: fetch the session if we don't have it cached.
    # (Most code paths in this handler already have session_data; if not,
    # add a minimal db read here.)
    pass

if project_id:
    from app.project_service import ProjectService
    from app.project_context import build_project_context_block
    psvc = ProjectService(db)
    project = psvc.get(user_id, project_id)
    if project:
        # Get file texts from the subcollection.
        file_texts = psvc.list_files(user_id, project_id)
        # Merge metadata + text for the context builder.
        files_by_id = {f['file_id']: f for f in file_texts}
        files_for_context = []
        for meta in project.get('files', []):
            fid = meta.get('file_id')
            text = files_by_id.get(fid, {}).get('extracted_text', '')
            files_for_context.append({'filename': meta.get('filename', 'file'), 'extracted_text': text})
        addendum = build_project_context_block(
            instructions=project.get('instructions', ''),
            files=files_for_context,
        )
        if addendum:
            base_system = base_system + "\n\n" + addendum
```

Refactor recommendation (do during this task, before committing): the above block also needs to run at the second injection site (`chat_routes.py:3154`). Extract it into a helper `_apply_project_context(user_id, session_data, base_system) -> str` at the top of `chat_routes.py` and call it from both sites. Tests stay green; one place to change later.

- [ ] **Step 4: Apply at the second site (line 3154)**

Use the same helper at the second injection point. Confirm `session_data` (or whatever variable holds the session doc) is in scope; if not, fetch it via `chat_session.get_session(user_id, session_id)`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_chat_system_prompt_injection.py -v`
Expected: PASS.

Also run: `pytest tests/test_chat_routes_stream_meta.py -v` to verify no regressions in adjacent chat-routes tests.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/chat_routes.py tests/test_chat_system_prompt_injection.py
git commit -m "feat(projects): inject project instructions + files into system prompt"
```

---

### Task 3.3: File upload + delete routes

**Files:**
- Modify: `app/blueprints/project_routes.py`
- Test: `tests/test_project_file_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_project_file_routes.py`:

```python
"""Project file upload/list/delete routes."""
import io
import pytest


def test_upload_file_extracts_and_attaches(authed_client):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    resp = authed_client.post(
        f'/projects/{pid}/files',
        data={'file': (io.BytesIO(b'hello world from a project file'), 'note.txt')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201
    body = resp.json
    assert body['file']['filename'] == 'note.txt'
    assert body['file']['file_id']

    # Project doc reflects the new file in its files array.
    p = authed_client.get(f'/projects/{pid}').json['project']
    assert len(p['files']) == 1
    assert p['files'][0]['filename'] == 'note.txt'


def test_upload_rejects_when_at_cap(authed_client, monkeypatch):
    monkeypatch.setattr('app.project_service.MAX_FILES_PER_PROJECT', 1)
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    authed_client.post(
        f'/projects/{pid}/files',
        data={'file': (io.BytesIO(b'one'), 'a.txt')},
        content_type='multipart/form-data',
    )
    resp = authed_client.post(
        f'/projects/{pid}/files',
        data={'file': (io.BytesIO(b'two'), 'b.txt')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 409


def test_delete_file_removes_metadata_and_subcollection(authed_client):
    pid = authed_client.post('/projects', json={'name': 'X'}).json['project']['id']
    up = authed_client.post(
        f'/projects/{pid}/files',
        data={'file': (io.BytesIO(b'hi'), 'a.txt')},
        content_type='multipart/form-data',
    )
    fid = up.json['file']['file_id']
    resp = authed_client.delete(f'/projects/{pid}/files/{fid}')
    assert resp.status_code == 204
    p = authed_client.get(f'/projects/{pid}').json['project']
    assert p['files'] == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_project_file_routes.py -v`
Expected: fail — routes don't exist.

- [ ] **Step 3: Add file routes to `project_routes.py`**

Append to `app/blueprints/project_routes.py`:

```python
import os
import re
import uuid

from werkzeug.utils import secure_filename

from app.config import Config
from app.text_extractor import TextExtractorUtility


MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB per file


@project_bp.route('/projects/<project_id>/files', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@login_required
def upload_project_file(project_id):
    uid = session['user']['uid']
    svc = _service()
    if not svc.get(uid, project_id):
        return jsonify({'error': 'Project not found'}), 404

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file provided'}), 400

    # Size check via stream length (Werkzeug stores it; defer to actual write check too).
    f.stream.seek(0, os.SEEK_END)
    size_bytes = f.stream.tell()
    f.stream.seek(0)
    if size_bytes > MAX_FILE_BYTES:
        return jsonify({'error': f'File too large (>10 MB)'}), 413

    filename = secure_filename(f.filename) or f'file-{uuid.uuid4().hex}'
    sanitized_user_id = re.sub(r'[^\w\-]', '_', uid)
    project_dir = os.path.join(
        os.path.abspath(Config.UPLOAD_FOLDER),
        sanitized_user_id, 'projects', project_id,
    )
    os.makedirs(project_dir, exist_ok=True)
    file_id = uuid.uuid4().hex
    storage_path_abs = os.path.join(project_dir, f'{file_id}-{filename}')
    f.save(storage_path_abs)
    storage_path_rel = os.path.relpath(storage_path_abs, os.path.abspath(Config.UPLOAD_FOLDER))

    # Extract text via existing utility.
    try:
        extracted_text, _debug = TextExtractorUtility.extract_text(storage_path_abs)
    except Exception as e:
        logger.warning(f"Text extraction failed for {storage_path_abs}: {e}")
        extracted_text = ''

    try:
        meta = svc.add_file(
            uid, project_id,
            file_id=file_id, filename=filename, mime_type=f.mimetype or 'application/octet-stream',
            size_bytes=size_bytes, storage_path=storage_path_rel,
            extracted_text=extracted_text or '',
        )
    except ProjectService.LimitExceeded as e:
        # Roll back the file on disk.
        try: os.remove(storage_path_abs)
        except OSError: pass
        return jsonify({'error': str(e)}), 409

    return jsonify({'file': meta}), 201


@project_bp.route('/projects/<project_id>/files/<file_id>', methods=['DELETE'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def delete_project_file(project_id, file_id):
    uid = session['user']['uid']
    svc = _service()
    p = svc.get(uid, project_id)
    if not p:
        return jsonify({'error': 'Project not found'}), 404
    # Find file metadata for storage_path so we can delete the disk file.
    meta = next((f for f in p['files'] if f.get('file_id') == file_id), None)
    if not meta:
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.join(os.path.abspath(Config.UPLOAD_FOLDER), meta['storage_path'])
    try: os.remove(abs_path)
    except OSError as e: logger.warning(f'Failed to remove disk file: {e}')

    svc.remove_file(uid, project_id, file_id)
    return ('', 204)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_project_file_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/project_routes.py tests/test_project_file_routes.py
git commit -m "feat(projects): file upload + delete with text extraction"
```

---

### Task 3.4: Files UI on landing page

**Files:**
- Modify: `app/templates/projects_page.html` (replace the "files come next release" placeholder)
- Modify: `app/static/js/projectsPage.js` (file list + upload)
- Modify: `app/static/css/projects.css` (file rows)

- [ ] **Step 1: Replace the Files placeholder in `projects_page.html`**

Find the `<section>` containing "Files come in the next release" and replace it with:

```html
<section class="project-section">
  <h2 class="project-section-title">Files</h2>
  <div class="project-files-dropzone" id="project-files-dropzone">
    <input type="file" id="project-file-input" multiple style="display:none" />
    <button type="button" class="project-add-files-btn" id="project-add-files-btn">+ Add files</button>
    <span class="project-files-hint">or drop files here (PDF, doc, image, text — 10 MB max each)</span>
  </div>
  <ul id="project-files" class="project-files" role="list"></ul>
</section>
```

- [ ] **Step 2: Wire file list + upload in `projectsPage.js`**

Append to `projectsPage.js`:

```javascript
const fileEls = {
  dropzone: document.getElementById('project-files-dropzone'),
  input: document.getElementById('project-file-input'),
  addBtn: document.getElementById('project-add-files-btn'),
  list: document.getElementById('project-files'),
};

if (fileEls.dropzone) {
  fileEls.addBtn.addEventListener('click', () => fileEls.input.click());
  fileEls.input.addEventListener('change', (e) => uploadFiles(e.target.files));
  fileEls.dropzone.addEventListener('dragover', (e) => { e.preventDefault(); fileEls.dropzone.classList.add('drag-over'); });
  fileEls.dropzone.addEventListener('dragleave', () => fileEls.dropzone.classList.remove('drag-over'));
  fileEls.dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    fileEls.dropzone.classList.remove('drag-over');
    uploadFiles(e.dataTransfer.files);
  });
  loadFiles();
}

async function loadFiles() {
  const resp = await fetch(`/projects/${encodeURIComponent(projectId)}`, { credentials: 'same-origin' });
  if (!resp.ok) return;
  const { project } = await resp.json();
  renderFiles(project.files || []);
}

function renderFiles(files) {
  fileEls.list.innerHTML = '';
  if (!files.length) {
    fileEls.list.innerHTML = '<li class="project-files-empty">No files yet.</li>';
    return;
  }
  files.forEach((f) => {
    const li = document.createElement('li');
    li.className = 'project-file-row';
    li.innerHTML = `
      <span class="filename">${escapeHtml(f.filename)}</span>
      <span class="size">${formatSize(f.size_bytes)}</span>
      <button type="button" class="file-remove" data-file-id="${f.file_id}" aria-label="Remove file">×</button>
    `;
    li.querySelector('.file-remove').addEventListener('click', () => removeFile(f.file_id));
    fileEls.list.appendChild(li);
  });
}

async function uploadFiles(fileList) {
  for (const file of fileList) {
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetch(`/projects/${encodeURIComponent(projectId)}/files`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '' },
      body: fd,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      window.toast?.error?.(`Couldn't upload ${file.name}: ${err.error || resp.status}`);
    }
  }
  await loadFiles();
}

async function removeFile(fileId) {
  if (!confirm('Remove this file from the project?')) return;
  const resp = await fetch(`/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '' },
  });
  if (resp.ok) await loadFiles();
}

function formatSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
```

- [ ] **Step 3: Style** — append to `projects.css`:

```css
.project-files-dropzone {
  display: flex; align-items: center; gap: 12px;
  padding: 14px;
  border: 1px dashed var(--border-primary, rgba(255,255,255,0.15));
  border-radius: 10px;
  margin-bottom: 10px;
}
.project-files-dropzone.drag-over { border-color: var(--project-accent); background: color-mix(in srgb, var(--project-accent) 8%, transparent); }
.project-add-files-btn {
  background: var(--bg-hover, rgba(255,255,255,0.06));
  border: none; color: inherit;
  padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 13px;
}
.project-files-hint { font-size: 12px; color: var(--text-tertiary); }

.project-files { list-style: none; padding: 0; margin: 0; }
.project-file-row {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 10px; border-radius: 8px;
}
.project-file-row:hover { background: var(--bg-hover, rgba(255,255,255,0.04)); }
.project-file-row .filename { flex: 1; font-size: 14px; }
.project-file-row .size { font-size: 12px; color: var(--text-tertiary); }
.project-file-row .file-remove {
  background: transparent; border: none; color: var(--text-tertiary);
  font-size: 16px; cursor: pointer; padding: 2px 6px;
}
.project-file-row .file-remove:hover { color: var(--danger, #ef4444); }
```

- [ ] **Step 4: Manual smoke**

- Upload a PDF on the project page → it appears in the list with size.
- Start a new chat in the project → ask a question that requires the PDF's content → confirm the model answers from the document.
- Remove a file → list updates.

- [ ] **Step 5: Commit**

```bash
git add app/templates/projects_page.html app/static/js/projectsPage.js app/static/css/projects.css
git commit -m "feat(projects): files UI on landing page (upload, list, remove)"
```

---

### Task 3.5: M3 sanity + tag

- [ ] **Step 1: Full golden path**

1. Create project "Copenhagen Trip".
2. Set instructions: "Always respond as a Copenhagen local; suggest food in Nørrebro."
3. Upload a 200 KB PDF with hotel-booking details.
4. Start new chat → ask "What's my checkin date?" → model answers from the PDF.
5. Send a follow-up "Where should I have dinner?" → model uses the Nørrebro instruction.

- [ ] **Step 2: Tag**

```bash
git tag projects-m3
```

---

## Milestone 4 — Polish (Add-to-project menu, chat tag, color picker, delete dialog)

The remaining ChatGPT-Projects-equivalent flourishes.

### Task 4.1: Wire the existing "Add to project" sidebar menu

**Files:**
- Modify: `app/templates/index.html:1490` (existing `#action-add-to-project` placeholder)
- Modify: `app/static/js/projectsModule.js` (open picker)

- [ ] **Step 1: Inspect the existing menu**

Run: `grep -n "action-add-to-project\|submenu" app/templates/index.html | head`. Note the parent menu and how submenus are wired.

- [ ] **Step 2: Convert `#action-add-to-project` into a submenu trigger**

Replace the current `<div id="action-add-to-project" …>` block at line 1490 to use the same submenu pattern as `#action-skills` (line 1496):

```html
<div class="asi" id="action-add-to-project" data-submenu="submenu-projects" style="display:flex;align-items:center;gap:11px;padding:9px 12px;cursor:pointer;color:var(--text-primary, rgba(255,255,255,0.88));font-size:13.5px;user-select:none">
  <span style="opacity:0.8;color:var(--text-primary, #fff);display:flex;flex-shrink:0">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
      <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
    </svg>
  </span>
  <span style="flex:1">Add to project</span>
  <span style="opacity:0.4;display:flex;align-items:center;flex-shrink:0">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
  </span>
</div>
<div id="submenu-projects" style="display:none;width:220px;background:var(--bg-elevated, #2b2a27);border-radius:12px;overflow:hidden;border:0.5px solid var(--border-primary, rgba(255,255,255,0.08));box-shadow:var(--shadow-lg, 0 6px 24px rgba(0,0,0,0.5));position:absolute;bottom:0;left:calc(100% + 6px);z-index:100">
  <div id="submenu-projects-list">
    <!-- Populated by projectsModule.js -->
  </div>
</div>
```

- [ ] **Step 3: Populate the submenu in `projectsModule.js`**

In `projectsModule.js`'s `renderList()` (called every reload), also render the submenu rows:

```javascript
function renderSubmenu() {
  const target = document.getElementById('submenu-projects-list');
  if (!target) return;
  target.innerHTML = '';
  if (state.projects.length === 0) {
    target.innerHTML = '<div style="padding:9px 14px;font-size:13px;opacity:0.5">No projects yet</div>';
    return;
  }
  state.projects.forEach((p) => {
    const item = document.createElement('div');
    item.className = 'asi submenu-project-item';
    item.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer;font-size:13.5px;';
    item.dataset.projectId = p.id;
    item.innerHTML = `${FOLDER_SVG.replace('class="folder-icon"', 'style="width:14px;height:14px;color:'+p.color+'"')} <span>${escapeHtml(p.name)}</span>`;
    item.addEventListener('click', () => addCurrentChatToProject(p.id));
    target.appendChild(item);
  });
}

async function addCurrentChatToProject(projectId) {
  const sid = window.chatManager?.currentSessionId?.();
  if (!sid) return;
  await fetch(`/chat/${encodeURIComponent(sid)}`, {
    method: 'PATCH', credentials: 'same-origin', headers: jsonHeaders(),
    body: JSON.stringify({ project_id: projectId }),
  });
  window.dispatchEvent(new CustomEvent('projects:session-moved', { detail: { sessionId: sid, projectId } }));
  // Optionally redirect to the project landing page.
}
```

Call `renderSubmenu()` after `renderList()` inside `reload()`.

- [ ] **Step 4: Manual smoke**

Open the attach menu on a chat → "Add to project" → submenu opens with project list → click "Copenhagen Trip" → current chat's `project_id` is set → sidebar refreshes (chat hides from time bucket).

- [ ] **Step 5: Commit**

```bash
git add app/templates/index.html app/static/js/projectsModule.js
git commit -m "feat(projects): wire 'Add to project' submenu (was placeholder)"
```

---

### Task 4.2: "in: <project>" tag at top of chat area

**Files:**
- Modify: `app/templates/index.html` (slot)
- Modify: `app/static/js/chatManager` or the chat-view module that knows the active session
- Append: `app/static/css/projects.css` (already styled `.project-context-tag` in M1.2)

- [ ] **Step 1: Add the slot**

In the chat area top, find a stable spot (above the message list, maybe in the existing header bar). Add:

```html
<a id="chat-project-tag" class="project-context-tag" href="#" hidden>
  <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 10h20"/></svg>
  <span class="name"></span>
</a>
```

- [ ] **Step 2: Update on session change**

In the module that handles "active session changed" (likely a method on `chatManager`), after loading session data:

```javascript
const tag = document.getElementById('chat-project-tag');
if (tag) {
  if (session.project_id) {
    // Find project from cached list (loaded by projectsModule).
    const project = window.projects?.getList?.().find((p) => p.id === session.project_id);
    if (project) {
      tag.querySelector('.name').textContent = project.name;
      tag.style.setProperty('--project-accent', project.color);
      tag.href = `/projects/${project.id}`;
      tag.hidden = false;
    } else {
      tag.hidden = true;
    }
  } else {
    tag.hidden = true;
  }
}
```

- [ ] **Step 3: Manual smoke**

Switch to a chat that has `project_id` set → small "▢ Copenhagen Trip" tag appears at the top of the chat area, clicking it goes to the project landing page.

- [ ] **Step 4: Commit**

```bash
git add app/templates/index.html app/static/js/<chat-manager-file>.js
git commit -m "feat(projects): show 'in: <project>' tag on chat header"
```

---

### Task 4.3: Color picker + delete dialog on the landing page

**Files:**
- Modify: `app/static/js/projectsPage.js` (menu actions)
- Append: `app/static/css/projects.css`

- [ ] **Step 1: Add a `⋯` menu with Rename / Change color / Delete**

In `projectsPage.js`, bind `#project-menu`:

```javascript
const menuBtn = document.getElementById('project-menu');
menuBtn?.addEventListener('click', openProjectMenu);

function openProjectMenu(e) {
  closeAnyOpenMenus();
  const m = document.createElement('div');
  m.className = 'project-page-menu-popover';
  m.innerHTML = `
    <button type="button" data-action="rename">Rename</button>
    <button type="button" data-action="color">Change color</button>
    <button type="button" data-action="delete" class="danger">Delete project</button>
  `;
  document.body.appendChild(m);
  positionPopover(m, e.target);
  m.addEventListener('click', (ev) => {
    const action = ev.target.dataset?.action;
    if (action === 'rename') { triggerRename(); m.remove(); }
    if (action === 'color')  { openColorPicker(m, ev.target); }
    if (action === 'delete') { triggerDelete(); m.remove(); }
  });
  document.addEventListener('click', () => m.remove(), { once: true });
}
```

- [ ] **Step 2: Color picker**

```javascript
const PALETTE = ['#d97706','#dc2626','#db2777','#7c3aed','#2563eb','#0891b2','#16a34a','#ca8a04','#475569','#737373'];

function openColorPicker(parent, anchor) {
  const picker = document.createElement('div');
  picker.className = 'project-page-color-picker';
  PALETTE.forEach((c) => {
    const swatch = document.createElement('button');
    swatch.type = 'button';
    swatch.style.background = c;
    swatch.dataset.color = c;
    swatch.addEventListener('click', async (e) => {
      e.stopPropagation();
      await fetch(`/projects/${encodeURIComponent(projectId)}`, {
        method: 'PATCH', credentials: 'same-origin', headers: jsonHeaders(),
        body: JSON.stringify({ color: c }),
      });
      document.documentElement.style.setProperty('--project-accent', c);
      root.style.setProperty('--project-accent', c);
      picker.remove();
      parent.remove();
    });
    picker.appendChild(swatch);
  });
  document.body.appendChild(picker);
  positionPopover(picker, anchor);
}
```

- [ ] **Step 3: Delete dialog with cascade choice**

```javascript
async function triggerDelete() {
  const chatsResp = await fetch(`/chat/history?project_id=${encodeURIComponent(projectId)}&page_size=500`, { credentials: 'same-origin' });
  const chats = chatsResp.ok
    ? Object.values((await chatsResp.json()).categorized_history || {}).flat()
    : [];
  const count = chats.length;

  let cascade;
  if (count === 0) {
    if (!confirm('Delete this project?')) return;
    cascade = 'keep';
  } else {
    const choice = prompt(
      `This project contains ${count} chat${count === 1 ? '' : 's'}.\n\nType "keep" to keep the chats (they return to your main history), or "delete" to also delete them.`,
      'keep',
    );
    if (choice === null) return;
    if (choice !== 'keep' && choice !== 'delete') {
      alert('Cancelled — type exactly "keep" or "delete".');
      return;
    }
    if (choice === 'delete' && !confirm(`Permanently delete ${count} chat${count === 1 ? '' : 's'}?`)) return;
    cascade = choice;
  }
  const resp = await fetch(`/projects/${encodeURIComponent(projectId)}?cascade=${cascade}`, {
    method: 'DELETE', credentials: 'same-origin', headers: jsonHeaders(),
  });
  if (resp.ok) window.location.href = '/';
}
```

- [ ] **Step 4: Helpers — `triggerRename` and `positionPopover`**

```javascript
function triggerRename() {
  const next = window.prompt('Rename project:', els.title.textContent);
  if (!next || next.trim() === els.title.textContent) return;
  fetch(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH', headers: jsonHeaders(), credentials: 'same-origin',
    body: JSON.stringify({ name: next.trim() }),
  }).then((r) => {
    if (r.ok) {
      els.title.textContent = next.trim();
      document.title = `${next.trim()} · XelaSphere`;
    }
  });
}

function positionPopover(node, anchor) {
  const rect = anchor.getBoundingClientRect();
  node.style.position = 'absolute';
  node.style.top = `${rect.bottom + window.scrollY + 4}px`;
  node.style.left = `${rect.right + window.scrollX - node.offsetWidth}px`;
  node.style.zIndex = '500';
}

function closeAnyOpenMenus() {
  document.querySelectorAll('.project-page-menu-popover, .project-page-color-picker').forEach((n) => n.remove());
}
```

- [ ] **Step 5: Styles** — append to `projects.css`:

```css
.project-page-menu-popover {
  background: var(--bg-elevated, #2b2a27);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
  border-radius: 10px;
  padding: 6px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  display: flex; flex-direction: column;
}
.project-page-menu-popover button {
  background: transparent; border: none; color: inherit;
  text-align: left; padding: 8px 12px; border-radius: 6px;
  font-size: 13px; cursor: pointer;
}
.project-page-menu-popover button:hover { background: var(--bg-hover, rgba(255,255,255,0.06)); }
.project-page-menu-popover button.danger { color: var(--danger, #ef4444); }

.project-page-color-picker {
  display: grid; grid-template-columns: repeat(5, 28px); gap: 8px;
  padding: 10px; background: var(--bg-elevated, #2b2a27);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
  border-radius: 10px;
}
.project-page-color-picker button {
  width: 28px; height: 28px; border: none; border-radius: 50%;
  cursor: pointer;
}
```

- [ ] **Step 6: Manual smoke**

Verify rename, change color (sidebar updates after refresh), and delete with both cascade modes.

- [ ] **Step 7: Commit**

```bash
git add app/static/js/projectsPage.js app/static/css/projects.css
git commit -m "feat(projects): landing page ⋯ menu — rename, color picker, delete with cascade"
```

---

### Task 4.4: M4 final sanity + tag

- [ ] **Step 1: End-to-end golden path**

1. Create project "Copenhagen Trip" from sidebar.
2. Open landing page. Set instructions, add 2 PDFs and 1 image.
3. Click "+ New chat in this project" — ask "what hotel am I staying at?" — verify answer comes from the PDF.
4. Send 2 more chats in the project.
5. Return to sidebar — project shows, the three chats are hidden from Today; "+ 3 more in projects" hint shows.
6. From a non-project chat row's attach menu, click "Add to project" → submenu → Copenhagen Trip → chat moves.
7. Open a chat that's in the project → top tag shows "▢ Copenhagen Trip".
8. Project page ⋯ → change color → sidebar reflects new color after reload.
9. Project page ⋯ → Delete → choose "keep" → chats reappear in Today.

- [ ] **Step 2: Run full pytest suite**

Run: `pytest tests/ -v`
Expected: no regressions.

- [ ] **Step 3: Tag M4 (shippable)**

```bash
git tag projects-m4
```

---

## Self-review

Run through this checklist with fresh eyes after writing the plan.

1. **Spec coverage** — every spec section maps to at least one task:
   - Data model → 0.1, 0.2.
   - Create modal copy + flow → 1.1, 1.3.
   - Sidebar Projects section → 1.1, 1.2, 1.3, 1.4.
   - Project landing page → 2.1, 2.2.
   - Files + extraction → 3.3, 3.4.
   - System-prompt injection → 3.1, 3.2.
   - "Add to project" menu → 4.1.
   - "in: <project>" tag → 4.2.
   - Color picker + delete dialog → 4.3.
   - Firestore rules → 0.5.
   - 80K injection budget / 200K storage cap / 20 files / 10 MB / 8K instructions → enforced in `project_service` (200K, 20), `project_context` (80K), `project_routes` (10 MB, 8K).
   - Color palette of 10 → defined in `project_routes.py` (`COLOR_PALETTE`) and `projectsPage.js` (`PALETTE`). Both must stay in sync.

2. **Placeholder scan** — no "TBD"/"TODO"/"fill in". The few hedge-language spots (`<chat-manager-file>.js` in Task 4.2 commit message, "or similar" in Task 1.4 fetch refactor) are unavoidable because they reference codebase-specific names the implementer must confirm with grep. Each such instance includes the grep command to find the real name.

3. **Type consistency** — `project_id` consistent across data layer, routes, and JS. `file_id` consistent. `cascade=keep|delete` consistent in DELETE handler and UI. `INJECTION_BUDGET_CHARS` defined once and imported. `MAX_FILES_PER_PROJECT`, `PER_FILE_TEXT_CAP`, `MAX_FILE_BYTES`, `MAX_INSTRUCTIONS_LEN`, `COLOR_PALETTE` each defined exactly once.

4. **Known gaps to flag for the implementer (not blockers — file as v1.1 follow-ups):**
   - **`count_grouped()` Firestore `!=` query** may require a composite index. If the deploy logs surface an index error, fall back to `total_active - ungrouped_count` via two queries. Already noted inline in Task 0.4.
   - **Color palette duplication** — defined in two places (Python + JS). Cleaner alternative: serve `/api/project-palette` and have both read from there. Skipped for v1 simplicity.
   - **Submenu population timing** — `renderSubmenu()` runs on `projectsModule.init`, which means newly-created projects won't appear in the "Add to project" submenu until the next reload. For v1.1, call `reload()` after every create event so the submenu refreshes.
   - **Mobile** — landing page styles need responsive review; the wide layout works at desktop widths. Quick CSS sweep in v1.1.
   - **File preview in the Files list** — clicking a filename does nothing in v1. Could route to the existing `/uploads/<path>` viewer in v1.1.

These gaps are intentional v1.1 deferrals consistent with the spec's "out of scope" list. None block shipping any milestone above.

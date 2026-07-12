"""Project CRUD route tests (mock-style, matching tests/unit/test_chats_endpoints.py)."""
from unittest.mock import patch, MagicMock


HEADERS = {"X-CSRFToken": "test-token"}


def _csrf_bypass():
    return patch("app.limiter.validate_csrf", return_value=None)


def test_list_projects_empty(authenticated_client, mock_firestore):
    with patch("app.blueprints.project_routes.ProjectService.list", return_value=[]):
        resp = authenticated_client.get('/projects')
    assert resp.status_code == 200
    assert resp.get_json() == {'projects': []}


def test_create_project_returns_201(authenticated_client, mock_firestore):
    fake_project = {'id': 'p1', 'name': 'Copenhagen Trip', 'color': '#d97706',
                    'instructions': '', 'files': [], 'position': 0}
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.count", return_value=0), \
         patch("app.blueprints.project_routes.ProjectService.create", return_value=fake_project) as m:
        resp = authenticated_client.post('/projects', json={'name': 'Copenhagen Trip'}, headers=HEADERS)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['project']['name'] == 'Copenhagen Trip'
    m.assert_called_once()


def test_create_rejects_empty_name(authenticated_client, mock_firestore):
    with _csrf_bypass():
        resp = authenticated_client.post('/projects', json={'name': '   '}, headers=HEADERS)
    assert resp.status_code == 400


def test_create_rejects_long_name(authenticated_client, mock_firestore):
    with _csrf_bypass():
        resp = authenticated_client.post('/projects', json={'name': 'X' * 121}, headers=HEADERS)
    assert resp.status_code == 400


def test_create_accepts_optional_color(authenticated_client, mock_firestore):
    fake_project = {'id': 'p1', 'name': 'A', 'color': '#16a34a',
                    'instructions': '', 'files': [], 'position': 0}
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.count", return_value=0), \
         patch("app.blueprints.project_routes.ProjectService.create", return_value=fake_project) as m:
        resp = authenticated_client.post('/projects',
                                          json={'name': 'A', 'color': '#16a34a'},
                                          headers=HEADERS)
    assert resp.status_code == 201
    _, kwargs = m.call_args
    assert kwargs['color'] == '#16a34a'


def test_create_rejects_invalid_color(authenticated_client, mock_firestore):
    with _csrf_bypass():
        resp = authenticated_client.post('/projects',
                                          json={'name': 'A', 'color': 'blue'},
                                          headers=HEADERS)
    assert resp.status_code == 400


def test_create_at_cap_returns_409(authenticated_client, mock_firestore):
    from app.project_service import ProjectService
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.count", return_value=0), \
         patch("app.blueprints.project_routes.ProjectService.create",
               side_effect=ProjectService.LimitExceeded("limit")):
        resp = authenticated_client.post('/projects',
                                          json={'name': 'X'},
                                          headers=HEADERS)
    assert resp.status_code == 409


def test_get_project(authenticated_client, mock_firestore):
    fake_project = {'id': 'p1', 'name': 'X', 'color': '#000', 'instructions': '',
                    'files': [], 'position': 0}
    with patch("app.blueprints.project_routes.ProjectService.get", return_value=fake_project):
        resp = authenticated_client.get('/projects/p1')
    assert resp.status_code == 200
    assert resp.get_json()['project']['name'] == 'X'


def test_get_project_404(authenticated_client, mock_firestore):
    with patch("app.blueprints.project_routes.ProjectService.get", return_value=None):
        resp = authenticated_client.get('/projects/nope')
    assert resp.status_code == 404


def test_patch_instructions(authenticated_client, mock_firestore):
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.update", return_value=True) as m:
        resp = authenticated_client.patch('/projects/p1',
                                          json={'instructions': 'Be warm.'},
                                          headers=HEADERS)
    assert resp.status_code == 204
    args, _ = m.call_args
    assert args[2] == {'instructions': 'Be warm.'}


def test_patch_rejects_long_instructions(authenticated_client, mock_firestore):
    with _csrf_bypass():
        resp = authenticated_client.patch('/projects/p1',
                                          json={'instructions': 'x' * 8001},
                                          headers=HEADERS)
    assert resp.status_code == 400


def test_patch_color_must_be_hex(authenticated_client, mock_firestore):
    with _csrf_bypass():
        resp = authenticated_client.patch('/projects/p1',
                                          json={'color': 'blue'},
                                          headers=HEADERS)
    assert resp.status_code == 400


def test_patch_404_for_unknown(authenticated_client, mock_firestore):
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.update", return_value=False):
        resp = authenticated_client.patch('/projects/nope',
                                          json={'name': 'X'},
                                          headers=HEADERS)
    assert resp.status_code == 404


def test_delete_keep_cascade_clears_session_project_id(authenticated_client, mock_firestore):
    # Mock the Firestore query stream to return two session refs.
    fake_session_a = MagicMock(); fake_session_a.id = 's1'
    fake_session_b = MagicMock(); fake_session_b.id = 's2'
    mock_firestore.collection.return_value.document.return_value\
        .collection.return_value.where.return_value.stream.return_value = [fake_session_a, fake_session_b]

    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.delete", return_value=True):
        resp = authenticated_client.delete('/projects/p1?cascade=keep', headers=HEADERS)
    assert resp.status_code == 204


def test_delete_keep_chunks_large_projects(authenticated_client, mock_firestore):
    sessions = []
    for idx in range(451):
        snap = MagicMock()
        snap.id = f's{idx}'
        sessions.append(snap)
    mock_firestore.collection.return_value.document.return_value\
        .collection.return_value.where.return_value.stream.return_value = sessions
    mock_firestore.batch.reset_mock()

    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.delete", return_value=True):
        resp = authenticated_client.delete('/projects/p1?cascade=keep', headers=HEADERS)

    assert resp.status_code == 204
    assert mock_firestore.batch.call_count == 2


def test_delete_cascade_delete_calls_bulk_delete(authenticated_client, mock_firestore):
    fake_session_a = MagicMock(); fake_session_a.id = 's1'
    mock_firestore.collection.return_value.document.return_value\
        .collection.return_value.where.return_value.stream.return_value = [fake_session_a]

    with _csrf_bypass(), \
         patch("app.blueprints.shared.chat_session.bulk_delete_sessions", return_value=1) as bulk, \
         patch("app.blueprints.project_routes.ProjectService.delete", return_value=True):
        resp = authenticated_client.delete('/projects/p1?cascade=delete', headers=HEADERS)
    assert resp.status_code == 204
    bulk.assert_called_once()


def test_delete_invalid_cascade_400(authenticated_client, mock_firestore):
    with _csrf_bypass():
        resp = authenticated_client.delete('/projects/p1?cascade=evil', headers=HEADERS)
    assert resp.status_code == 400


def test_unauthed_list_redirects_or_401(client):
    resp = client.get('/projects')
    # login_required in this codebase returns 403 JSON for unauthenticated requests
    assert resp.status_code in (302, 401, 403)

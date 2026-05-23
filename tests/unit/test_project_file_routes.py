"""Project file upload + delete route tests (mock-style)."""
import io
from unittest.mock import patch, MagicMock


HEADERS = {"X-CSRFToken": "test-token"}


def _csrf_bypass():
    return patch("app.limiter.validate_csrf", return_value=None)


def test_upload_file_returns_201_and_extracts_text(authenticated_client, mock_firestore, tmp_path):
    """POST /projects/<id>/files uploads, extracts text, returns metadata."""
    fake_project = {'id': 'p1', 'name': 'X', 'color': '#000', 'instructions': '',
                    'files': [], 'position': 0}
    fake_meta = {'file_id': 'f1', 'filename': 'note.txt', 'mime_type': 'text/plain',
                 'size_bytes': 30, 'storage_path': 'u/p/f.txt', 'text_char_count': 30,
                 'uploaded_at': '2026-05-22T10:00:00'}

    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=fake_project), \
         patch("app.blueprints.project_routes.ProjectService.add_file", return_value=fake_meta) as mock_add, \
         patch("app.text_extractor.TextExtractorUtility.extract_text",
               return_value=("hello world from a project file", {})), \
         patch("app.blueprints.project_routes.os.makedirs"), \
         patch("werkzeug.datastructures.FileStorage.save"):
        resp = authenticated_client.post(
            '/projects/p1/files',
            data={'file': (io.BytesIO(b'hello world from a project file'), 'note.txt')},
            content_type='multipart/form-data',
            headers=HEADERS,
        )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['file']['filename'] == 'note.txt'
    mock_add.assert_called_once()


def test_upload_rejects_no_file(authenticated_client, mock_firestore):
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get",
               return_value={'id': 'p1', 'files': []}):
        resp = authenticated_client.post(
            '/projects/p1/files', data={}, content_type='multipart/form-data',
            headers=HEADERS,
        )
    assert resp.status_code == 400


def test_upload_404_when_project_missing(authenticated_client, mock_firestore):
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=None):
        resp = authenticated_client.post(
            '/projects/nope/files',
            data={'file': (io.BytesIO(b'x'), 'a.txt')},
            content_type='multipart/form-data',
            headers=HEADERS,
        )
    assert resp.status_code == 404


def test_upload_rejects_oversize(authenticated_client, mock_firestore):
    """File > 10 MB is rejected with 413."""
    fake_project = {'id': 'p1', 'files': []}
    big = b'x' * (10 * 1024 * 1024 + 1)
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=fake_project):
        resp = authenticated_client.post(
            '/projects/p1/files',
            data={'file': (io.BytesIO(big), 'big.bin')},
            content_type='multipart/form-data',
            headers=HEADERS,
        )
    assert resp.status_code == 413


def test_upload_at_cap_returns_409(authenticated_client, mock_firestore):
    """When add_file raises LimitExceeded, route returns 409."""
    from app.project_service import ProjectService
    fake_project = {'id': 'p1', 'files': []}
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=fake_project), \
         patch("app.blueprints.project_routes.ProjectService.add_file",
               side_effect=ProjectService.LimitExceeded("limit")), \
         patch("app.text_extractor.TextExtractorUtility.extract_text",
               return_value=("hi", {})), \
         patch("app.blueprints.project_routes.os.makedirs"), \
         patch("werkzeug.datastructures.FileStorage.save"), \
         patch("app.blueprints.project_routes.os.remove"):
        resp = authenticated_client.post(
            '/projects/p1/files',
            data={'file': (io.BytesIO(b'hi'), 'a.txt')},
            content_type='multipart/form-data',
            headers=HEADERS,
        )
    assert resp.status_code == 409


def test_delete_file_returns_204(authenticated_client, mock_firestore):
    fake_project = {'id': 'p1', 'files': [{
        'file_id': 'f1', 'filename': 'a.txt', 'storage_path': 'a.txt',
    }]}
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=fake_project), \
         patch("app.blueprints.project_routes.ProjectService.remove_file", return_value=True) as m, \
         patch("os.remove"):
        resp = authenticated_client.delete('/projects/p1/files/f1', headers=HEADERS)
    assert resp.status_code == 204
    m.assert_called_once_with('test_user_123', 'p1', 'f1')


def test_delete_file_404_when_project_missing(authenticated_client, mock_firestore):
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=None):
        resp = authenticated_client.delete('/projects/nope/files/f1', headers=HEADERS)
    assert resp.status_code == 404


def test_delete_file_404_when_file_missing(authenticated_client, mock_firestore):
    fake_project = {'id': 'p1', 'files': []}
    with _csrf_bypass(), \
         patch("app.blueprints.project_routes.ProjectService.get", return_value=fake_project):
        resp = authenticated_client.delete('/projects/p1/files/nope', headers=HEADERS)
    assert resp.status_code == 404

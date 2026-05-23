"""POST /chat/new persists project_id on the new session."""
from unittest.mock import patch


HEADERS = {"X-CSRFToken": "test-token"}


def test_chat_new_forwards_project_id(authenticated_client, mock_firestore):
    """When body has project_id, it's passed to chat_session.create_session."""
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.shared.chat_session.create_session",
               return_value="new-sid-123") as m:
        resp = authenticated_client.post(
            '/chat/new',
            json={'session_name': 'Hotel post', 'project_id': 'p-abc'},
            headers=HEADERS,
        )
    assert resp.status_code == 201
    _, kwargs = m.call_args
    assert kwargs.get('project_id') == 'p-abc'


def test_chat_new_without_project_id_passes_none(authenticated_client, mock_firestore):
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.shared.chat_session.create_session",
               return_value="new-sid-123") as m:
        resp = authenticated_client.post(
            '/chat/new',
            json={'session_name': 'Hello'},
            headers=HEADERS,
        )
    assert resp.status_code == 201
    _, kwargs = m.call_args
    assert kwargs.get('project_id') in (None,)

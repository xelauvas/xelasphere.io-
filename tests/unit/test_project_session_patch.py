"""Sessions accept project_id via the existing PATCH /chat/<id> route.

Mock-style tests matching the pattern in tests/unit/test_chats_endpoints.py.
"""
from unittest.mock import patch

from app.chat_session import ALLOWED_PATCH_FIELDS


def test_project_id_in_allowed_patch_fields():
    assert 'project_id' in ALLOWED_PATCH_FIELDS


def test_patch_session_passes_project_id_to_data_layer(authenticated_client, mock_firestore):
    """PATCH /chat/<id> with {project_id: 'p-abc'} forwards project_id to update_session_metadata."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch(
            "app.chat_session.ChatSession.update_session_metadata",
            return_value=True,
        ) as mock_update:
        resp = authenticated_client.patch(
            "/chat/session_abc",
            json={"project_id": "p-abc"},
            headers=headers,
        )
    assert resp.status_code == 204
    args, _ = mock_update.call_args
    # call signature: (user_id, session_id, fields_dict)
    assert args[1] == "session_abc"
    assert args[2] == {"project_id": "p-abc"}


def test_patch_session_can_clear_project_id_with_null(authenticated_client, mock_firestore):
    """PATCH /chat/<id> with {project_id: None} forwards None to update_session_metadata."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch(
            "app.chat_session.ChatSession.update_session_metadata",
            return_value=True,
        ) as mock_update:
        resp = authenticated_client.patch(
            "/chat/session_abc",
            json={"project_id": None},
            headers=headers,
        )
    assert resp.status_code == 204
    args, _ = mock_update.call_args
    assert args[2] == {"project_id": None}

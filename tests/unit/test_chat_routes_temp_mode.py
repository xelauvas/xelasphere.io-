"""Tests that wire the existing incognito flag to actually skip writes."""
from unittest.mock import patch


def test_chat_new_temp_returns_temp_prefixed_session_id(authenticated_client, mock_firestore):
    """A temporary chat returns a session_id with the temp_ prefix
    (mirrors the existing guest_ pattern).
    """
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None):
        resp = authenticated_client.post(
            "/chat/new",
            json={"session_name": "test", "temporary": True},
            headers=headers,
        )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["session_id"].startswith("temp_"), body
    assert body["status"] == "temporary"


def test_chat_new_temp_does_not_call_create_session(authenticated_client, mock_firestore):
    """A temporary session must never write a Firestore session document.

    Mirrors the existing guest_mode behavior: f"guest_{...}" session_id
    sidesteps create_session entirely. We assert the existing
    chat_session.create_session function is NOT called.
    """
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.chat_session.ChatSession.create_session") as mock_create:
        resp = authenticated_client.post(
            "/chat/new",
            json={"session_name": "test", "temporary": True},
            headers=headers,
        )
    assert resp.status_code == 201
    mock_create.assert_not_called()


def test_chat_new_non_temp_still_calls_create_session(authenticated_client, mock_firestore):
    """Regression guard: non-temporary path unchanged — still writes
    the Firestore session document.
    """
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.chat_session.ChatSession.create_session",
               return_value="session_abc") as mock_create:
        resp = authenticated_client.post(
            "/chat/new",
            json={"session_name": "test", "temporary": False},
            headers=headers,
        )
    assert resp.status_code == 201
    mock_create.assert_called_once()

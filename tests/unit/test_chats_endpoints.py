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

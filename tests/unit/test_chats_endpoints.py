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

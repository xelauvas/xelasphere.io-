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


def test_delete_conversation_memory_item_happy_path(authenticated_client):
    """Per-row delete: Flask wrapper bridges to sidecar DELETE.

    CSRF is decorated via ``@csrf_required`` which calls
    ``validate_csrf``. In tests we patch ``validate_csrf`` to a no-op
    so the test exercises the route logic, not the CSRF wiring (which
    is tested separately).
    """
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._sidecar_delete_drawer",
               return_value=True) as mock_call:
        resp = authenticated_client.delete(
            "/user/conversation-memory/drawer_xxx",
            headers=headers,
        )
    assert resp.status_code == 200
    mock_call.assert_called_once_with("test_user_123", "drawer_xxx")


def test_delete_conversation_memory_item_sidecar_failure_returns_503(authenticated_client):
    headers = {"X-CSRFToken": "test-token"}

    def boom(*a, **kw):
        raise ConnectionError("sidecar down")
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._sidecar_delete_drawer",
               side_effect=boom):
        resp = authenticated_client.delete(
            "/user/conversation-memory/drawer_xxx",
            headers=headers,
        )
    assert resp.status_code == 503

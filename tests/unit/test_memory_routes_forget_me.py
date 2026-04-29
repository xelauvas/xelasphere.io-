"""Tests for DELETE /user/forget-me — the multi-store cascade."""
from unittest.mock import patch


def test_forget_me_happy_cascade(authenticated_client, mock_firestore):
    """All five stores get wiped; chat_sessions stays intact."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._sidecar_delete_palace",
               return_value=True) as mock_palace, \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=12) as mock_outbox, \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               return_value=5) as mock_fs:
        resp = authenticated_client.delete("/user/forget-me", headers=headers)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["partial_failures"] == []
    mock_palace.assert_called_once_with("test_user_123")
    mock_outbox.assert_called_once_with("test_user_123")
    # Three subcollection wipes: memory_v2, memory_events, smart_suggestions
    assert mock_fs.call_count == 3


def test_forget_me_partial_failure_palace(authenticated_client, mock_firestore):
    """Sidecar palace-delete fails; Firestore + outbox still wiped."""
    headers = {"X-CSRFToken": "test-token"}

    def boom(*a, **kw):
        raise ConnectionError("sidecar down")

    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._sidecar_delete_palace",
               side_effect=boom), \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=0), \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               return_value=0):
        resp = authenticated_client.delete("/user/forget-me", headers=headers)

    assert resp.status_code == 200  # Best-effort cascade — never abort
    body = resp.get_json()
    assert "palace" in body["partial_failures"]


def test_forget_me_does_not_touch_chat_sessions(authenticated_client, mock_firestore):
    """chat_sessions subcollection is NOT in the cascade — separate scope."""
    headers = {"X-CSRFToken": "test-token"}
    wiped = []

    def track_wipe(uid, subcol):
        wiped.append(subcol)
        return 0

    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._sidecar_delete_palace",
               return_value=True), \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=0), \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               side_effect=track_wipe):
        authenticated_client.delete("/user/forget-me", headers=headers)
    assert "chat_sessions" not in wiped
    assert set(wiped) == {"memory_v2", "memory_events", "smart_suggestions"}


def test_forget_me_partial_failure_firestore_subcollection(
    authenticated_client, mock_firestore
):
    """One Firestore subcollection wipe fails; others still complete."""
    headers = {"X-CSRFToken": "test-token"}
    call_count = {"n": 0}

    def fail_second_call(uid, subcol):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError(f"firestore down for {subcol}")
        return 3

    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._sidecar_delete_palace",
               return_value=True), \
         patch("app.blueprints.memory_routes._wipe_outbox_for_user",
               return_value=0), \
         patch("app.blueprints.memory_routes._wipe_firestore_subcollection",
               side_effect=fail_second_call):
        resp = authenticated_client.delete("/user/forget-me", headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    # The second subcollection wipe failed (memory_events per the cascade order)
    assert len(body["partial_failures"]) == 1

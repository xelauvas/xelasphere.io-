"""Tests for /user/memory-toggles GET + POST.

Three boolean toggles stored as a single Firestore doc:
users/{uid}/preferences/memory
"""
from unittest.mock import patch


def test_get_memory_toggles_defaults_when_doc_missing(authenticated_client, mock_firestore):
    """If no prefs doc exists yet, all three toggles default to true."""
    resp = authenticated_client.get("/user/memory-toggles")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "master": True,
        "ref_v2": True,
        "ref_mempalace": True,
    }


def test_post_memory_toggles_persists_to_firestore(authenticated_client, mock_firestore):
    """POST calls Firestore .set() with the merged update payload.

    (We can't round-trip read/write because the mock_firestore fixture
    is non-persisting MagicMock chains; we verify the WRITE call shape
    directly, which is the tighter unit test anyway.)
    """
    headers = {"X-CSRFToken": "test-token"}
    captured = {}

    def fake_set(payload, merge=False):
        captured["payload"] = payload
        captured["merge"] = merge

    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._toggle_doc_ref") as mock_ref:
        mock_doc = mock_ref.return_value
        mock_doc.set.side_effect = fake_set
        resp = authenticated_client.post(
            "/user/memory-toggles",
            json={"ref_mempalace": False, "ref_v2": True},
            headers=headers,
        )
    assert resp.status_code == 200
    assert captured == {
        "payload": {"ref_mempalace": False, "ref_v2": True},
        "merge": True,
    }


def test_post_memory_toggles_invalid_field_rejected(authenticated_client, mock_firestore):
    """Unknown fields don't make it into the Firestore .set() payload."""
    headers = {"X-CSRFToken": "test-token"}
    captured = {}

    def fake_set(payload, merge=False):
        captured["payload"] = payload

    with patch("app.limiter.validate_csrf", return_value=None), \
         patch("app.blueprints.memory_routes._toggle_doc_ref") as mock_ref:
        mock_doc = mock_ref.return_value
        mock_doc.set.side_effect = fake_set
        resp = authenticated_client.post(
            "/user/memory-toggles",
            json={"master": True, "rogue_field": "lol"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert "rogue_field" not in captured["payload"]
    assert captured["payload"] == {"master": True}


def test_post_memory_toggles_empty_payload_rejected(authenticated_client, mock_firestore):
    """POST with no recognized fields returns 400."""
    headers = {"X-CSRFToken": "test-token"}
    with patch("app.limiter.validate_csrf", return_value=None):
        resp = authenticated_client.post(
            "/user/memory-toggles",
            json={"unknown_field": True},
            headers=headers,
        )
    assert resp.status_code == 400

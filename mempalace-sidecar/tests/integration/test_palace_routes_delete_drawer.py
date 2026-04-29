"""Tests for DELETE /palace/{user_id}/drawers/{drawer_id} route.

Uses the shared ``client`` + ``auth`` integration fixtures (in
``tests/integration/conftest.py``) which drive the app lifespan so
``app.state.registry`` and the embedding model are ready before any
route fires.
"""
import pytest

pytestmark = pytest.mark.integration


async def test_delete_drawer_returns_200_when_present(client, auth):
    """Ingest a turn, look up its drawer_id via the chroma collection
    (mirrors how the Flask wrapper resolves IDs at call time), then
    delete via the new endpoint.
    """
    ingest_resp = await client.post(
        "/palace/test_user/ingest",
        json={
            "turn_id": "t1",
            "text": "test statement to delete",
            "wing": "conversations",
            "room": "session",
            "session_id": "s1",
            "pair_id": "p1",
            "role": "user",
            "ts": 1700000000.0,
        },
        headers=auth,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text

    # Derive the drawer_id deterministically (mirrors PalaceHandle.add_drawer).
    from mempalace_sidecar.palace_handle import _derive_drawer_id
    drawer_id = _derive_drawer_id(turn_id="t1", wing="conversations", room="session")

    resp = await client.delete(
        f"/palace/test_user/drawers/{drawer_id}",
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["drawer_id"] == drawer_id


async def test_delete_drawer_idempotent_on_missing(client, auth):
    """DELETE on non-existent drawer returns 200 (idempotent), not 404 —
    the underlying handle.delete_drawer is idempotent and a 404 here would
    just be a UX papercut.
    """
    # Pre-create the palace so the handle opens cleanly
    await client.post(
        "/palace/test_user/ingest",
        json={"turn_id": "t1", "text": "x", "wing": "conversations",
              "room": "session", "session_id": "s1", "pair_id": "p1",
              "role": "user", "ts": 1.0},
        headers=auth,
    )
    resp = await client.delete(
        "/palace/test_user/drawers/nonexistent_drawer_id",
        headers=auth,
    )
    assert resp.status_code == 200


async def test_delete_drawer_unauthorized_without_token(client):
    """No X-Sidecar-Token header → 401 (auth middleware blocks)."""
    resp = await client.delete("/palace/test_user/drawers/some_id")
    assert resp.status_code == 401


async def test_delete_drawer_rejects_wrong_token(client):
    resp = await client.delete(
        "/palace/test_user/drawers/some_id",
        headers={"X-Sidecar-Token": "wrong"},
    )
    assert resp.status_code == 401

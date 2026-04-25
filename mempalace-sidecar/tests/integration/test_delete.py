"""Integration tests for ``DELETE /palace/{user_id}`` (plan M0.8.1).

Four tests — all mandatory per the dispatch brief's teeth-bearing
list:

- ``test_delete_palace_pops_backend_cache`` — the cache-eviction
  contract from spec §5.3. Plan line 2666: "The integration test
  for DELETE MUST include ``assert str(palace_path) not in
  mempalace.palace._DEFAULT_BACKEND._clients`` after the delete
  completes." Same teeth as ``test_registry_eviction_bounds_backend_cache``
  at M0.3.4.

- ``test_delete_palace_records_audit_row`` — audit trail lands
  before the rmtree (spec §4.4 order-of-operations). Opens
  ``audit.sqlite`` directly post-DELETE, asserts the row exists
  with matching fields.

- ``test_delete_then_reopen_creates_fresh_palace`` — no orphan
  drawer bleed-through. DELETE user, ingest for same user, assert
  the new palace has exactly 1 drawer (the new ingest).

- ``test_delete_on_already_deleted_user_returns_404`` — retry
  safety for both normal completion and timeout-mid-rmtree (both
  leave no palace dir). Second DELETE on already-deleted user
  returns 404. The original brief called this test
  "test_delete_idempotent_on_retry_after_timeout"; renamed per
  Q4 follow-up — name now matches the contract actually tested.

All four fail at RED because ``routes/lifecycle.py::delete_palace``
is a stub returning 501.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


async def test_delete_palace_pops_backend_cache(
    client, auth, tmp_path: Path
) -> None:
    """Plan line 2666 teeth-bearing contract. After DELETE
    completes, the library's ``_DEFAULT_BACKEND._clients`` dict
    MUST NOT contain the deleted palace's path — otherwise we leak
    a Chroma PersistentClient per deletion, and over months the
    sidecar OOMs.

    Same check as M0.3.4's ``test_registry_eviction_bounds_backend_cache``
    but driven through the HTTP DELETE route instead of direct
    registry eviction.
    """
    from mempalace.palace import _DEFAULT_BACKEND

    # Establish a palace: ingest one drawer for the target user.
    r = await client.post(
        "/palace/alice/ingest",
        headers=auth,
        json={
            "turn_id": "a" * 32,
            "session_id": "s1",
            "pair_id": "p" * 32,
            "role": "user",
            "text": "establishing palace existence",
            "ts": 1_700_000_000.0,
        },
    )
    assert r.status_code == 200, r.text

    palace_path = str(tmp_path / "palaces" / "alice")
    assert palace_path in _DEFAULT_BACKEND._clients, (
        "precondition: ingest should have put alice's palace_path in "
        "the library backend cache"
    )

    # DELETE.
    r = await client.request(
        "DELETE",
        "/palace/alice",
        headers=auth,
        json={"requested_by": "admin@xelasphere.io", "reason": "test"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "deleted"
    assert body["bytes_removed"] > 0

    # Teeth: backend cache pop happened.
    assert palace_path not in _DEFAULT_BACKEND._clients, (
        "library backend cache still holds deleted palace's client — "
        "handle.close() must pop _DEFAULT_BACKEND._clients before the "
        "palace dir is removed"
    )


async def test_delete_palace_records_audit_row(
    client, auth, tmp_path: Path
) -> None:
    """Audit trail lands before rmtree per spec §4.4. After DELETE,
    ``audit.sqlite`` has exactly one row with the caller's
    requested_by, reason, and a positive before_bytes (a non-empty
    palace was deleted).
    """
    # Seed the palace.
    for i in range(3):
        r = await client.post(
            "/palace/bob/ingest",
            headers=auth,
            json={
                "turn_id": f"b{'0' * 30}{i:02d}",
                "session_id": "s2",
                "pair_id": f"q{'0' * 30}{i:02d}",
                "role": "user",
                "text": f"bob turn {i}",
                "ts": 1_700_000_100.0 + i,
            },
        )
        assert r.status_code == 200, r.text

    r = await client.request(
        "DELETE",
        "/palace/bob",
        headers=auth,
        json={
            "requested_by": "admin@xelasphere.io",
            "reason": "gdpr request #4512",
        },
    )
    assert r.status_code == 200, r.text

    audit_path = tmp_path / "audit.sqlite"
    assert audit_path.exists(), f"audit.sqlite not created at {audit_path}"

    with sqlite3.connect(str(audit_path)) as conn:
        rows = conn.execute(
            "SELECT user_id, requested_by, reason, before_bytes "
            "FROM deletion_audit"
        ).fetchall()

    assert len(rows) == 1, f"expected 1 audit row, got {len(rows)}"
    user_id, requested_by, reason, before_bytes = rows[0]
    assert user_id == "bob"
    assert requested_by == "admin@xelasphere.io"
    assert reason == "gdpr request #4512"
    assert before_bytes > 0, (
        f"before_bytes should be positive for a non-empty palace, "
        f"got {before_bytes}"
    )


async def test_delete_then_reopen_creates_fresh_palace(
    client, auth
) -> None:
    """DELETE + ingest for same user yields a fresh palace with no
    orphan drawers from the previous palace.

    A broken DELETE that leaves stale ChromaDB files on disk (e.g.,
    forgets to rmtree, or the rmtree targets the wrong dir) would
    fail here: a second ingest would find the old drawers still
    searchable.
    """
    # First palace: 2 drawers.
    for i in range(2):
        r = await client.post(
            "/palace/carol/ingest",
            headers=auth,
            json={
                "turn_id": f"c{'0' * 30}{i:02d}",
                "session_id": "old-session",
                "pair_id": f"r{'0' * 30}{i:02d}",
                "role": "user",
                "text": f"old content {i}",
                "ts": 1_700_000_200.0 + i,
            },
        )
        assert r.status_code == 200

    # DELETE.
    r = await client.request(
        "DELETE",
        "/palace/carol",
        headers=auth,
        json={"requested_by": "carol", "reason": "user-initiated wipe"},
    )
    assert r.status_code == 200, r.text

    # Fresh ingest after DELETE.
    r = await client.post(
        "/palace/carol/ingest",
        headers=auth,
        json={
            "turn_id": "c" * 32,
            "session_id": "new-session",
            "pair_id": "n" * 32,
            "role": "user",
            "text": "fresh content after deletion",
            "ts": 1_700_001_000.0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    # Inspect via /drawers — should see exactly 1 drawer (the new
    # ingest), not 3 (2 old + 1 new).
    r = await client.get("/palace/carol/drawers?limit=10", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 1, (
        f"expected 1 drawer post-DELETE+ingest, got {page['total']}; "
        f"orphan drawers from deleted palace bleeding through"
    )


async def test_delete_user_a_does_not_touch_user_b(
    client, auth, tmp_path: Path
) -> None:
    """Cross-user isolation (production-critical invariant).

    DELETE on alice's palace MUST NOT affect bob's palace state —
    bob's drawers, bob's kg.sqlite, bob's backend-cache entry, bob's
    writer_lock, all untouched. A future "optimization" that
    accidentally shared state across users (e.g., reusing a single
    Chroma client across palaces, a global audit ref that points at
    any user's dir, a misscoped rmtree pattern) would fail this
    test.

    The weaker version of this test — "after DELETE alice, can bob
    still ingest?" — would miss a bug where bob's post-delete
    ingest succeeds but his PRE-existing drawers are gone. This
    test checks bob's full state survives: pre-existing drawers +
    post-delete fresh ingest + backend-cache entry all intact.
    """
    from mempalace.palace import _DEFAULT_BACKEND

    # Seed alice with 2 drawers and bob with 3.
    for name, count in (("alice", 2), ("bob", 3)):
        for i in range(count):
            r = await client.post(
                f"/palace/{name}/ingest",
                headers=auth,
                json={
                    "turn_id": f"{name[0]}{'0' * 30}{i:02d}",
                    "session_id": "s",
                    "pair_id": f"{name[0]}p{'0' * 29}{i:02d}",
                    "role": "user",
                    "text": f"{name} turn {i}",
                    "ts": 1_700_000_500.0 + i,
                },
            )
            assert r.status_code == 200, r.text

    alice_path = str(tmp_path / "palaces" / "alice")
    bob_path = str(tmp_path / "palaces" / "bob")
    # Precondition: both palaces in backend cache.
    assert alice_path in _DEFAULT_BACKEND._clients
    assert bob_path in _DEFAULT_BACKEND._clients

    # DELETE alice.
    r = await client.request(
        "DELETE",
        "/palace/alice",
        headers=auth,
        json={"requested_by": "admin", "reason": "isolation test"},
    )
    assert r.status_code == 200, r.text

    # Alice-side teardown: palace gone from backend cache, dir gone.
    assert alice_path not in _DEFAULT_BACKEND._clients
    assert not (tmp_path / "palaces" / "alice").exists()

    # Bob-side preserved: backend cache entry untouched, palace dir
    # intact, drawers queryable.
    assert bob_path in _DEFAULT_BACKEND._clients, (
        "bob's backend client was disturbed by alice's DELETE — "
        "cross-user isolation violated"
    )
    r = await client.get("/palace/bob/drawers?limit=10", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 3, (
        f"bob should still have his 3 drawers after alice's DELETE; "
        f"got total={page['total']}"
    )

    # Bob can still ingest post-alice-delete.
    r = await client.post(
        "/palace/bob/ingest",
        headers=auth,
        json={
            "turn_id": "b" * 32,
            "session_id": "s",
            "pair_id": "B" * 32,
            "role": "user",
            "text": "bob's post-alice-delete ingest",
            "ts": 1_700_001_000.0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    # Final state: bob at 4 drawers, alice 404.
    r = await client.get("/palace/bob/drawers?limit=10", headers=auth)
    assert r.json()["total"] == 4

    r = await client.request(
        "DELETE",
        "/palace/alice",
        headers=auth,
        json={"requested_by": "admin", "reason": "second delete"},
    )
    assert r.status_code == 404


async def test_delete_on_already_deleted_user_returns_404(
    client, auth
) -> None:
    """Retry-safety contract. Whether the first DELETE completed
    normally OR timed out mid-rmtree, the palace dir is gone, and
    the user is effectively deleted. A second DELETE on that user
    MUST return 404 rather than silently recreate + delete a fresh
    empty palace (which would muddy the audit trail with
    before_bytes=0 rows).

    Covers both concrete paths the reaper contract cares about:
    clean-finish + timeout-mid-rmtree both observably leave the
    same "no palace dir" state, so the second DELETE takes the
    same code path either way.
    """
    # Seed.
    r = await client.post(
        "/palace/dave/ingest",
        headers=auth,
        json={
            "turn_id": "d" * 32,
            "session_id": "s",
            "pair_id": "e" * 32,
            "role": "user",
            "text": "dave turn",
            "ts": 1_700_000_300.0,
        },
    )
    assert r.status_code == 200

    # First DELETE — should succeed.
    r1 = await client.request(
        "DELETE",
        "/palace/dave",
        headers=auth,
        json={"requested_by": "dave", "reason": "wipe"},
    )
    assert r1.status_code == 200, r1.text

    # Second DELETE — should 404.
    r2 = await client.request(
        "DELETE",
        "/palace/dave",
        headers=auth,
        json={"requested_by": "dave", "reason": "wipe again"},
    )
    assert r2.status_code == 404, (
        f"second DELETE on already-deleted user must return 404, "
        f"got {r2.status_code}: {r2.text}"
    )

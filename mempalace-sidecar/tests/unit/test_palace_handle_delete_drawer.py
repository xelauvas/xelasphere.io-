"""Tests for PalaceHandle.delete_drawer.

Per-drawer surgical delete: remove from chroma collection AND
turns.sqlite atomically. Idempotent — missing drawer doesn't raise.
"""
from pathlib import Path

import pytest

from mempalace_sidecar.palace_handle import PalaceHandle


@pytest.fixture
def palace_path(tmp_path: Path) -> Path:
    return tmp_path / "palace"


@pytest.fixture
def handle(palace_path):
    h = PalaceHandle.open(palace_path)
    try:
        yield h
    finally:
        h.close()


def test_delete_drawer_removes_from_chroma_and_turns_sqlite(handle):
    """After delete, item is gone from BOTH stores."""
    receipt = handle.add_drawer(
        turn_id="t1",
        text="I am a test user statement.",
        wing="conversations",
        room="session",
        session_id="s1",
        pair_id="p1",
        role="user",
        ts=1700000000.0,
    )
    drawer_id = receipt.drawer_id

    # Verify present in chroma + turns.sqlite
    chroma_got_before = handle._collection.get(ids=[drawer_id])
    assert len(chroma_got_before.get("ids", [])) == 1
    sqlite_count_before = handle.turns.execute(
        "SELECT COUNT(*) FROM turns WHERE drawer_id = ?", (drawer_id,)
    ).fetchone()[0]
    assert sqlite_count_before == 1

    handle.delete_drawer(drawer_id)

    chroma_got_after = handle._collection.get(ids=[drawer_id])
    assert len(chroma_got_after.get("ids", [])) == 0
    sqlite_count_after = handle.turns.execute(
        "SELECT COUNT(*) FROM turns WHERE drawer_id = ?", (drawer_id,)
    ).fetchone()[0]
    assert sqlite_count_after == 0


def test_delete_drawer_missing_id_is_idempotent(handle):
    """Deleting a non-existent drawer must not raise."""
    handle.delete_drawer("drawer_does_not_exist_xyz")


def test_delete_drawer_does_not_touch_other_drawers(handle):
    """Deleting one drawer leaves siblings intact."""
    r1 = handle.add_drawer(turn_id="t1", text="first", wing="conversations",
                           room="session", session_id="s1", pair_id="p1",
                           role="user", ts=1.0)
    r2 = handle.add_drawer(turn_id="t2", text="second", wing="conversations",
                           room="session", session_id="s1", pair_id="p2",
                           role="user", ts=2.0)
    handle.delete_drawer(r1.drawer_id)
    chroma_got = handle._collection.get(ids=[r2.drawer_id])
    assert len(chroma_got.get("ids", [])) == 1
    sqlite_count = handle.turns.execute(
        "SELECT COUNT(*) FROM turns WHERE drawer_id = ?", (r2.drawer_id,)
    ).fetchone()[0]
    assert sqlite_count == 1


def test_delete_drawer_empty_id_is_noop(handle):
    """Empty/None drawer_id is a no-op (defensive)."""
    handle.delete_drawer("")

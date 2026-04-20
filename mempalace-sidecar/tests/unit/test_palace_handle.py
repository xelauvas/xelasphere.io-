"""Unit tests for PalaceHandle (spec §5.3, plan M0.3.0).

These tests exercise the library-isolation wrapper against real mempalace
3.3.1 machinery on a tmp_path. "Unit" here means "per-handle, no sidecar
HTTP layer"; they still hit ChromaDB on disk because mocking Chroma at
the depth required to validate the idempotency contract would test the
mock, not the contract.

The integration test file (``tests/integration/test_palace_handle_real.py``)
covers only the embedding-model reuse assertion which requires an
explicit warmup-style opener.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import mempalace.palace as mp_palace
from mempalace_sidecar.palace_handle import PalaceHandle
from mempalace_sidecar.schemas import AddDrawerResult


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def palace_path(tmp_path: Path) -> Path:
    return tmp_path / "palace"


@pytest.fixture
def handle(palace_path: Path):
    h = PalaceHandle.open(palace_path)
    try:
        yield h
    finally:
        # Best-effort close — tests that exercise close explicitly will have
        # already closed, and close() is idempotent by contract.
        h.close()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _expected_drawer_id(turn_id: str, wing: str = "conversations", room: str = "session") -> str:
    source_file = f"turn:{turn_id}"
    return (
        f"drawer_{wing}_{room}_"
        + hashlib.sha256((source_file + str(0)).encode()).hexdigest()[:24]
    )


def _add(handle, *, turn_id: str, session_id: str = "sess", pair_id: str = "p", role: str = "user",
         text: str = "hello world", ts: float = 1_700_000_000.0,
         wing: str | None = None, room: str | None = None) -> AddDrawerResult:
    kwargs = {
        "turn_id": turn_id,
        "session_id": session_id,
        "pair_id": pair_id,
        "role": role,
        "text": text,
        "ts": ts,
    }
    if wing is not None:
        kwargs["wing"] = wing
    if room is not None:
        kwargs["room"] = room
    return handle.add_drawer(**kwargs)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_open_creates_palace_dir_and_child_state(palace_path: Path):
    assert not palace_path.exists()
    h = PalaceHandle.open(palace_path)
    try:
        # Directory + child state materialize.
        assert palace_path.is_dir()
        assert (palace_path / "turns.sqlite").is_file()
        assert (palace_path / "kg.sqlite").is_file()
        assert (palace_path / "meta.json").is_file()
        assert (palace_path / "config").is_dir()

        meta = json.loads((palace_path / "meta.json").read_text())
        assert meta["schema_version"] == 1
        assert "created_at" in meta

        # turns table exists with the literal schema from spec §5.3.
        conn = sqlite3.connect(str(palace_path / "turns.sqlite"))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
            assert cols == {
                "turn_id", "drawer_id", "session_id", "pair_id",
                "role", "ts", "filed_at",
            }
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='turns'"
            )}
            # The two spec-mandated indexes (+ sqlite's implicit PK autoindex).
            assert "turns_session_ts" in indexes
            assert "turns_pair" in indexes
        finally:
            conn.close()
    finally:
        h.close()


def test_add_drawer_idempotent_by_turn_id(handle):
    r1 = _add(handle, turn_id="T1", text="first call body")
    assert r1.status == "accepted"
    assert handle.col.count() == 1

    r2 = _add(handle, turn_id="T1", text="second call body (must not be filed)")
    assert r2.status == "duplicate"
    # Same drawer_id returned (the one stored in turns.sqlite on the first call).
    assert r2.drawer_id == r1.drawer_id
    # Critically: library was not called a second time.
    assert handle.col.count() == 1


def test_add_drawer_deterministic_drawer_id(tmp_path: Path):
    """Two fresh handles on the same palace compute the same drawer_id for
    the same turn_id — confirming the ``source_file=f"turn:{turn_id}"``
    encoding trick (spec §5.3) holds across process state.
    """
    p = tmp_path / "palace-det"
    h1 = PalaceHandle.open(p)
    try:
        r1 = _add(h1, turn_id="T-det")
    finally:
        h1.close()

    h2 = PalaceHandle.open(p)
    try:
        # The turn_id is already in turns.sqlite from h1, so this returns
        # "duplicate" — but it returns the SAME drawer_id, because both
        # handles derive it from the same ``source_file``.
        r2 = _add(h2, turn_id="T-det")
    finally:
        h2.close()

    assert r1.drawer_id == r2.drawer_id == _expected_drawer_id("T-det")


def test_session_drawers_returns_by_session(handle):
    # 3 turns in session A, 2 in session B, inserted interleaved.
    _add(handle, turn_id="A1", session_id="A", ts=1.0)
    _add(handle, turn_id="B1", session_id="B", ts=2.0)
    _add(handle, turn_id="A2", session_id="A", ts=3.0)
    _add(handle, turn_id="B2", session_id="B", ts=4.0)
    _add(handle, turn_id="A3", session_id="A", ts=5.0)

    drawers_a = handle.session_drawers("A")
    assert len(drawers_a) == 3

    # Reverse-time order: A3 (ts=5) → A2 (ts=3) → A1 (ts=1).
    # Each drawer dict carries the library's own metadata; cross-reference
    # via the source_file = f"turn:{turn_id}" encoding.
    got_turn_ids = [d["metadata"]["source_file"] for d in drawers_a]
    assert got_turn_ids == ["turn:A3", "turn:A2", "turn:A1"]

    drawers_b = handle.session_drawers("B")
    assert len(drawers_b) == 2
    got_turn_ids_b = [d["metadata"]["source_file"] for d in drawers_b]
    assert got_turn_ids_b == ["turn:B2", "turn:B1"]


def test_semantic_search_returns_search_memories_shape(handle, monkeypatch):
    """semantic_search must unwrap the library's dict envelope and return
    the inner ``results`` list. Mocked so we don't exercise ChromaDB's
    embedding path — we're testing the unwrap contract.
    """
    fixed = {
        "query": "anything",
        "filters": {"wing": None, "room": None},
        "total_before_filter": 2,
        "results": [
            {"text": "doc a", "wing": "conversations", "room": "session",
             "similarity": 0.9},
            {"text": "doc b", "wing": "conversations", "room": "session",
             "similarity": 0.7},
        ],
    }

    calls: list = []

    def fake_search_memories(query, palace_path, wing=None, room=None, n_results=5,
                             max_distance=0.0):
        calls.append({"query": query, "palace_path": palace_path,
                      "wing": wing, "room": room, "n_results": n_results})
        return fixed

    # Patch at the place palace_handle imported from.
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        fake_search_memories,
    )

    out = handle.semantic_search("anything", k=5)
    assert out == fixed["results"]
    assert len(calls) == 1
    assert calls[0]["query"] == "anything"
    assert calls[0]["n_results"] == 5
    # palace_path passed as a string, per library signature.
    assert isinstance(calls[0]["palace_path"], str)


def test_list_drawers_offset_pagination(handle):
    # Insert 25 drawers.
    for i in range(25):
        _add(
            handle,
            turn_id=f"T{i:02d}",
            session_id="S",
            pair_id=f"p{i}",
            role="user" if i % 2 == 0 else "assistant",
            text=f"drawer body {i}",
            ts=float(i),
        )
    assert handle.col.count() == 25

    page1 = handle.list_drawers(limit=10, offset=0)
    page2 = handle.list_drawers(limit=10, offset=10)
    page3 = handle.list_drawers(limit=10, offset=20)

    assert len(page1["drawers"]) == 10
    assert len(page2["drawers"]) == 10
    assert len(page3["drawers"]) == 5

    assert page1["total"] == 25
    assert page2["total"] == 25
    assert page3["total"] == 25

    assert page1["next_offset"] == 10
    assert page2["next_offset"] == 20
    assert page3["next_offset"] is None


def test_list_drawers_total_is_none_when_filtered(handle):
    """Honesty contract: total is filtered-inaccurate, so for any filtered
    query we return total=None rather than an unfiltered wrong count. The
    Flask adapter renders "showing N of many" rather than a lie.

    next_offset still works: when the returned page is full, more may
    exist; when short, we're at the end.
    """
    # Insert 10 drawers in wing A and 5 in wing B.
    for i in range(10):
        _add(handle, turn_id=f"A{i:02d}", session_id="S", pair_id=f"a{i}",
              role="user", text=f"A-body {i}", ts=float(i), wing="wingA", room="r")
    for i in range(5):
        _add(handle, turn_id=f"B{i:02d}", session_id="S", pair_id=f"b{i}",
              role="user", text=f"B-body {i}", ts=float(100 + i), wing="wingB", room="r")
    assert handle.col.count() == 15

    # Filtered: total must be None (honest).
    filtered = handle.list_drawers(wing="wingA", limit=10, offset=0)
    assert filtered["total"] is None
    assert len(filtered["drawers"]) == 10
    # Full page → more may exist → next_offset is offset+limit.
    assert filtered["next_offset"] == 10

    # Short page → end of pagination → next_offset is None.
    short_page = handle.list_drawers(wing="wingB", limit=10, offset=0)
    assert short_page["total"] is None
    assert len(short_page["drawers"]) == 5
    assert short_page["next_offset"] is None

    # Unfiltered path still returns the real total.
    unfiltered = handle.list_drawers(limit=10, offset=0)
    assert unfiltered["total"] == 15
    assert unfiltered["next_offset"] == 10


def test_close_pops_backend_cache(palace_path: Path):
    """Teeth-bearing spec §5.3 cache-eviction contract: close() MUST remove
    the library's backend client entry. The key the library uses is the
    resolved absolute path string.
    """
    h = PalaceHandle.open(palace_path)

    # After open, the library's backend cache holds our palace.
    key = str(h.palace_path)
    # Library stores the resolved/absolute path; verify our key is the one
    # that ended up in the cache.
    assert any(Path(k).resolve() == Path(key).resolve()
               for k in mp_palace._DEFAULT_BACKEND._clients), \
        f"expected a client keyed by {key!r} in _clients: " \
        f"{list(mp_palace._DEFAULT_BACKEND._clients.keys())}"

    h.close()

    # After close, no entry in the backend cache references this palace path.
    assert not any(Path(k).resolve() == Path(key).resolve()
                   for k in mp_palace._DEFAULT_BACKEND._clients), \
        "_DEFAULT_BACKEND._clients still holds an entry for the closed palace"


def test_close_is_idempotent(handle):
    """Spec §5.3 lifecycle: second and subsequent close() calls are no-ops.
    Must NOT raise (KnowledgeGraph.close on an already-closed sqlite
    connection would raise without a guard).
    """
    handle.close()
    # Second call — should be a no-op, not raise.
    handle.close()
    # Third call for good measure.
    handle.close()


def test_no_mcp_server_import():
    """Spec §5.3 "Why not bridge to mempalace.mcp_server": the mcp_server
    module uses module-level global palace state and would cause cross-user
    data leaks in a multi-tenant sidecar. Grep the source; fail on any
    reference.
    """
    pkg_root = Path(__file__).resolve().parents[2] / "mempalace_sidecar"
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        text = py.read_text()
        # Catch both ``import mempalace.mcp_server`` and
        # ``from mempalace.mcp_server import ...`` and ``mempalace.mcp_server.foo``.
        if "mempalace.mcp_server" in text or "from mempalace import mcp_server" in text:
            offenders.append(str(py))
    assert offenders == [], \
        f"mempalace.mcp_server references forbidden per spec §5.3; found in: {offenders}"


def test_turns_authoritative_when_library_id_derivation_changes(handle, monkeypatch):
    """Empirical proof that ``turns.sqlite`` — not the library — is the
    idempotency authority.

    Simulate a future ``mempalace`` release that changes how drawer_ids are
    derived from ``source_file``: we monkey-patch ``miner.add_drawer`` to
    salt the ID so the library would file a DIFFERENT drawer on the second
    call. If turns.sqlite is authoritative, the second call is blocked
    before the library is reached, and the collection count stays at 1.
    """
    # First call: normal path, accepted.
    r1 = _add(handle, turn_id="T-auth", text="original")
    assert r1.status == "accepted"
    assert handle.col.count() == 1

    # Second call: monkey-patch the library's add_drawer via the bound
    # reference palace_handle uses. If handle.add_drawer short-circuits on
    # turns.sqlite, this patched function never runs.
    patched_calls: list = []

    def salted_add_drawer(col, wing, room, content, source_file, chunk_index, agent):
        patched_calls.append(source_file)
        # Produce a DIFFERENT ID than the deterministic one.
        salted_id = "drawer_SALTED_DIFFERENT_ID_" + source_file[-8:]
        col.upsert(documents=[content], ids=[salted_id],
                   metadatas=[{"wing": wing, "room": room,
                               "source_file": source_file,
                               "salted": True}])
        return True

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_add_drawer",
        salted_add_drawer,
    )

    r2 = _add(handle, turn_id="T-auth", text="second (must not file)")

    # Two assertions, both load-bearing:
    # 1. status == "duplicate" — blocked by turns.sqlite PK gate, not by
    #    library-level dedup.
    assert r2.status == "duplicate"
    # 2. collection.count() == 1 — library was never called (patched_calls
    #    would have recorded a call if it had been).
    assert handle.col.count() == 1
    assert patched_calls == [], \
        "handle.add_drawer called the library on a duplicate turn_id; " \
        "turns.sqlite is NOT the authority"

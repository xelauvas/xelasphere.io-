"""Integration tests for PalaceRegistry (M0.3.4).

These open real PalaceHandles against tmp_path — real Chroma,
sentence-transformers, sqlite. Marked ``integration`` so unit-only runs
skip them.

Two teeth-bearing tests (non-skippable):

* ``test_registry_eviction_bounds_backend_cache`` — asserts spec §5.3
  cache-eviction contract holds through LRU evictions.
* ``test_eviction_drains_readers`` — asserts spec §5.4 two-phase
  eviction: the reader-lock-drain step must complete before
  handle.close() touches library state.

Plus a straightforward ``test_opens_real_palace`` that verifies the
palace directory + sidecar-owned child databases land on disk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


async def test_opens_real_palace(tmp_path: Path) -> None:
    """First open of a fresh user creates the palace directory and all
    the sidecar-owned child databases (kg.sqlite, turns.sqlite,
    meta.json)."""
    from mempalace_sidecar.registry import PalaceRegistry

    r = PalaceRegistry(
        data_dir=tmp_path, max_bytes=2**31, idle_ttl_sec=3600
    )
    try:
        e = await r.open("alice")
        assert e.handle is not None
        assert e.path.exists()
        assert (e.path / "meta.json").exists()
        assert (e.path / "kg.sqlite").exists()
        assert (e.path / "turns.sqlite").exists()
        assert e.schema_version == 1
        # size_bytes MUST reflect a real disk walk, not a placeholder 0.
        # A just-opened palace has at minimum meta.json + empty sqlite
        # files, so size_bytes > 0 unless the walk is broken.
        assert e.size_bytes > 0, (
            "size_bytes should reflect the palace's disk footprint; "
            "_dir_size_bytes appears to be missing or returning 0"
        )
    finally:
        # Close all resident handles so Chroma releases file locks; we
        # don't want test teardown to trip on open handles across
        # tmp_path rmtree on Windows/CI edge cases.
        await r.close_all()


async def test_registry_eviction_bounds_backend_cache(tmp_path: Path) -> None:
    """Teeth-bearing (spec §5.3): when the registry evicts a palace, the
    library's ``_DEFAULT_BACKEND._clients`` dict MUST have that palace's
    path popped. If a refactor drops ``handle.close()`` from the eviction
    path, this test fails — there's no way to mock around it.

    Asserts on the real library singleton; no mocks past the dispatch to
    PalaceHandle.open.
    """
    from mempalace.palace import _DEFAULT_BACKEND

    from mempalace_sidecar.registry import PalaceRegistry

    # Small cap so two palaces can't both fit. Real palaces take ~200 KB
    # at minimum (empty chroma.sqlite3 etc.), so 1024 bytes forces
    # eviction on the second open.
    r = PalaceRegistry(
        data_dir=tmp_path, max_bytes=1024, idle_ttl_sec=3600
    )
    try:
        e1 = await r.open("alice")
        assert str(e1.path) in _DEFAULT_BACKEND._clients, (
            "Expected alice's PersistentClient to be cached after open"
        )
        alice_path = str(e1.path)

        # Second open triggers LRU eviction of alice. Phase 1 pops her
        # from self._cache; Phase 2 (after registry lock release)
        # closes her handle, which pops _DEFAULT_BACKEND._clients.
        e2 = await r.open("bob")
        assert "alice" not in r._cache
        assert "bob" in r._cache
        assert alice_path not in _DEFAULT_BACKEND._clients, (
            "alice's backend client leaked — handle.close() not called "
            "on eviction"
        )
        assert str(e2.path) in _DEFAULT_BACKEND._clients
    finally:
        await r.close_all()


async def test_eviction_drains_readers(tmp_path: Path) -> None:
    """Teeth-bearing (spec §5.4 "Eviction is two-phase"):

    Setup — alice is resident. A reader task holds alice's reader_lock
    and calls ``handle.list_drawers()`` twice, pausing between calls on
    an asyncio Event. In parallel an evicter task forces alice's
    eviction by opening bob (size cap trips). The evicter awaits bob's
    open to completion, which drives Phase 1 to finish; Phase 2
    (close-under-writer_lock) then blocks on alice's reader finishing.

    Assertions:
      1. reader.list_drawers() completes both calls without exception.
      2. After both tasks join, alice is gone from r._cache AND
         alice_path is gone from _DEFAULT_BACKEND._clients.

    If a future refactor merges the phases (closes inside the registry
    lock or without acquiring writer_lock), the reader's second call
    would race against a GC'd Chroma client and raise — this test
    would flip red.
    """
    from mempalace.palace import _DEFAULT_BACKEND

    from mempalace_sidecar.registry import PalaceRegistry

    r = PalaceRegistry(
        data_dir=tmp_path, max_bytes=1024, idle_ttl_sec=3600
    )
    try:
        a = await r.open("alice")
        alice_path = str(a.path)
        alice_handle = a.handle
        assert alice_path in _DEFAULT_BACKEND._clients

        reader_started = asyncio.Event()
        reader_may_finish = asyncio.Event()
        reader_errors: list[BaseException] = []

        async def slow_reader() -> None:
            lock = r._lock_for("alice")
            async with lock.reader_lock:
                reader_started.set()
                try:
                    # First real library call while reader_lock is held.
                    _ = alice_handle.list_drawers(limit=1)
                    # Yield so the evicter gets a chance to run; this is
                    # the window where Phase 2 is blocked on our
                    # writer_lock.
                    await reader_may_finish.wait()
                    # Second call — if Phase 2 sneaked past without
                    # respecting the writer_lock, the backend client
                    # would be gone and this would raise.
                    _ = alice_handle.list_drawers(limit=1)
                except BaseException as exc:  # noqa: BLE001
                    reader_errors.append(exc)

        async def evicter() -> None:
            await reader_started.wait()
            # Force eviction — bob's open triggers Phase 1, then Phase
            # 2 tries to acquire alice's writer_lock (which blocks on
            # the reader's reader_lock).
            evict_task = asyncio.create_task(r.open("bob"))
            # Give Phase 2 a moment to contend on the writer_lock. Then
            # release the reader so Phase 2 can finally proceed.
            await asyncio.sleep(0.05)
            reader_may_finish.set()
            await evict_task

        await asyncio.gather(slow_reader(), evicter())
        assert reader_errors == [], (
            f"Reader raised during eviction — two-phase contract broken: "
            f"{reader_errors!r}"
        )
        # By now evicter has returned — Phase 2 is complete.
        assert "alice" not in r._cache
        assert alice_path not in _DEFAULT_BACKEND._clients, (
            "alice's backend client remained after eviction — "
            "handle.close() not invoked on the eviction path"
        )
    finally:
        await r.close_all()

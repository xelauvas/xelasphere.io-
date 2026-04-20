"""Unit tests for PalaceRegistry (M0.3.1 through M0.3.5).

These exercise the cache-level contract without engaging the real
``PalaceHandle.open`` path — that's the integration suite's job
(``tests/integration/test_registry_real.py``). What we pin here:

* ``PalaceEntry`` field order and typing (spec §5.4).
* ``PalaceRegistry.open`` caches-then-reuses, and forces size-weighted
  LRU eviction when the cap is exceeded.
* The byte-accounting invariant (``_total_bytes == sum of cache sizes``)
  holds after every op — Hypothesis interleaves opens, size updates, and
  explicit evictions.
* ``sweep_idle`` closes entries whose ``last_used_at`` is older than
  ``idle_ttl_sec``, under the two-phase eviction contract.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st

from mempalace_sidecar.registry import PalaceEntry, PalaceRegistry


# ---------------------------------------------------------------------------
# M0.3.1 — PalaceEntry dataclass
# ---------------------------------------------------------------------------


def test_palace_entry_fields(tmp_path: Path) -> None:
    """Spec §5.4: 7 positional fields in fixed order. Hypothesis + older
    helpers construct PalaceEntry positionally, so the order is a
    contract, not a convenience."""
    now = time.monotonic()
    e = PalaceEntry(
        "u1",                       # user_id
        object(),                   # handle — sentinel for unit tests
        tmp_path / "u1",            # path
        1024,                       # size_bytes
        1,                          # schema_version
        now,                        # opened_at
        now,                        # last_used_at
    )
    assert e.user_id == "u1"
    assert e.handle is not None
    assert e.path == tmp_path / "u1"
    assert e.size_bytes == 1024
    assert e.schema_version == 1
    assert e.opened_at == now
    assert e.last_used_at == now


# ---------------------------------------------------------------------------
# M0.3.2 — PalaceRegistry open/close + size-weighted LRU
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> PalaceRegistry:
    return PalaceRegistry(
        data_dir=tmp_path, max_bytes=1024, idle_ttl_sec=3600
    )


async def test_registry_opens_and_caches(
    registry: PalaceRegistry, tmp_path: Path
) -> None:
    """First open constructs an entry; second open returns the cached
    instance. ``is`` equality matters — if we returned a fresh object,
    callers that rely on identity (e.g. PalaceHandle reuse) would break."""
    e = await registry.open("u1")
    assert e.user_id == "u1"
    assert e.path == tmp_path / "palaces" / "u1"
    e2 = await registry.open("u1")
    assert e is e2


async def test_registry_evicts_by_size(
    monkeypatch: pytest.MonkeyPatch, registry: PalaceRegistry
) -> None:
    """Force each palace to report 600 bytes; two would total 1200 >
    1024 cap, so the LRU victim (u1) gets evicted when u2 arrives."""

    async def fake_construct(uid: str) -> PalaceEntry:
        path = registry._palaces_dir / uid
        path.mkdir(parents=True, exist_ok=True)
        now = time.monotonic()
        # MagicMock stands in for PalaceHandle — the registry MUST call
        # handle.close() during Phase 2 eviction, which we assert below.
        handle = MagicMock()
        return PalaceEntry(uid, handle, path, 600, 1, now, now)

    monkeypatch.setattr(registry, "_construct_entry", fake_construct)

    e1 = await registry.open("u1")
    await registry.open("u2")
    assert "u1" not in registry._cache
    assert "u2" in registry._cache
    # Two-phase eviction contract: handle.close() MUST run in Phase 2.
    e1.handle.close.assert_called_once()


async def test_registry_eviction_calls_close_under_writer_lock(
    monkeypatch: pytest.MonkeyPatch, registry: PalaceRegistry
) -> None:
    """Phase 2 acquires the evicted user's writer_lock before
    handle.close() — so a reader holding the reader_lock would delay
    close() until release. This test asserts the writer_lock was touched
    during eviction by monkeypatching its acquire/release."""
    writer_acquire_hits = {"count": 0}

    async def fake_construct(uid: str) -> PalaceEntry:
        path = registry._palaces_dir / uid
        path.mkdir(parents=True, exist_ok=True)
        now = time.monotonic()
        return PalaceEntry(uid, MagicMock(), path, 600, 1, now, now)

    monkeypatch.setattr(registry, "_construct_entry", fake_construct)

    # Open u1 so a lock exists for u1 before eviction fires.
    await registry.open("u1")
    lock_u1 = registry._lock_for("u1")

    real_acquire = lock_u1.writer_lock.acquire

    async def counting_acquire() -> None:
        writer_acquire_hits["count"] += 1
        await real_acquire()

    monkeypatch.setattr(lock_u1.writer_lock, "acquire", counting_acquire)

    # Open u2 — triggers LRU eviction of u1; Phase 2 must acquire u1's
    # writer_lock at least once before handle.close() runs.
    await registry.open("u2")
    assert writer_acquire_hits["count"] >= 1


# ---------------------------------------------------------------------------
# M0.3.3 — Hypothesis property test: byte accounting stays consistent
# ---------------------------------------------------------------------------


# Named op tuples. Each kind gets its own strategy so Hypothesis can
# interleave them randomly across a sequence.
_open_op = st.tuples(
    st.just("open"),
    st.text(min_size=1, max_size=3, alphabet="abcde"),
    st.integers(min_value=0, max_value=500),
)
_set_size_op = st.tuples(
    st.just("set_size"),
    st.text(min_size=1, max_size=3, alphabet="abcde"),
    st.integers(min_value=0, max_value=500),
)
_evict_op = st.tuples(
    st.just("evict"),
    st.text(min_size=1, max_size=3, alphabet="abcde"),
    st.integers(min_value=0, max_value=0),  # unused for evict
)


@hyp_settings(max_examples=50, deadline=None)
@given(
    ops=st.lists(
        st.one_of(_open_op, _set_size_op, _evict_op),
        min_size=0,
        max_size=20,
    )
)
async def test_total_bytes_accurate(
    ops: list[tuple[str, str, int]], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Invariant (spec §5.4): ``_total_bytes() == sum(size_bytes for e
    in _cache.values())`` after every op.

    Strategy: Hypothesis interleaves opens, size updates, and explicit
    evictions. If an implementation caches the sum separately and
    forgets to update on a size mutation or on an eviction, the
    invariant fails mid-sequence.
    """
    # Unique tmp_path per Hypothesis example — fixtures aren't re-run
    # across examples, so tmp_path_factory is the right escape hatch.
    tmp = tmp_path_factory.mktemp("hyp")
    r = PalaceRegistry(data_dir=tmp, max_bytes=10_000, idle_ttl_sec=3600)

    for kind, uid, size in ops:
        if kind == "open":
            async def _ctor(
                u: str, _size: int = size
            ) -> PalaceEntry:
                path = r._palaces_dir / u
                path.mkdir(parents=True, exist_ok=True)
                now = time.monotonic()
                return PalaceEntry(
                    u, MagicMock(), path, _size, 1, now, now
                )

            r._construct_entry = _ctor  # type: ignore[assignment]
            await r.open(uid)
        elif kind == "set_size":
            # Simulate a size-byte refresh on an existing entry — e.g.
            # an ingest that grew the palace. If the uid isn't resident,
            # this is a no-op from the registry's POV.
            entry = r._cache.get(uid)
            if entry is not None:
                entry.size_bytes = size
        elif kind == "evict":
            # Explicit eviction via the public LRU path. No-op if not
            # resident.
            async with r._registry_lock:
                if uid in r._cache:
                    e = r._cache.pop(uid)
                    await r._close_evicted(uid, e)

        expected = sum(e.size_bytes for e in r._cache.values())
        assert r._total_bytes() == expected, (
            f"total_bytes drift after op {kind}({uid}, {size}): "
            f"expected {expected}, got {r._total_bytes()}"
        )


# ---------------------------------------------------------------------------
# M0.3.5 — sweep_idle: idle-TTL eviction
# ---------------------------------------------------------------------------


async def test_idle_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idle-TTL sweep closes entries whose last_used_at is older than
    ``idle_ttl_sec``. Time is monkey-patched so the test is deterministic."""
    r = PalaceRegistry(
        data_dir=tmp_path, max_bytes=2**31, idle_ttl_sec=5
    )
    clock = [100.0]
    monkeypatch.setattr(
        "mempalace_sidecar.registry.time.monotonic", lambda: clock[0]
    )

    async def fake_construct(uid: str) -> PalaceEntry:
        path = r._palaces_dir / uid
        path.mkdir(parents=True, exist_ok=True)
        return PalaceEntry(
            uid, MagicMock(), path, 0, 1, clock[0], clock[0]
        )

    r._construct_entry = fake_construct  # type: ignore[assignment]

    await r.open("u1")
    entry = r._cache["u1"]

    clock[0] += 4
    evicted = await r.sweep_idle()
    assert evicted == 0
    assert "u1" in r._cache

    clock[0] += 2  # total 6 seconds since last use > ttl 5
    evicted = await r.sweep_idle()
    assert evicted == 1
    assert "u1" not in r._cache
    # Two-phase eviction: handle.close must have been called.
    entry.handle.close.assert_called_once()


async def test_sweep_idle_returns_zero_when_all_hot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No idle users → sweep is a no-op and returns 0."""
    r = PalaceRegistry(
        data_dir=tmp_path, max_bytes=2**31, idle_ttl_sec=3600
    )

    async def fake_construct(uid: str) -> PalaceEntry:
        path = r._palaces_dir / uid
        path.mkdir(parents=True, exist_ok=True)
        now = time.monotonic()
        return PalaceEntry(uid, MagicMock(), path, 0, 1, now, now)

    r._construct_entry = fake_construct  # type: ignore[assignment]

    await r.open("u1")
    await r.open("u2")
    assert await r.sweep_idle() == 0
    assert set(r._cache) == {"u1", "u2"}

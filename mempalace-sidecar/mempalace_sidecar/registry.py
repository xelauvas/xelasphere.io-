"""PalaceRegistry — per-user palace cache with size-weighted LRU.

Spec §5.4 is the authoritative contract. Three invariants the review
path specifically verifies:

1. **Two-phase eviction.** Phase 1 (inside ``_registry_lock``) selects
   candidates and removes them from ``self._cache`` so no new request
   finds them. Phase 2 (after the registry lock is released) acquires
   each evicted user's ``writer_lock`` and calls ``handle.close()``.
   The writer-lock acquire drains in-flight readers before close touches
   library state — critical because ``handle.close()`` pops
   ``_DEFAULT_BACKEND._clients`` which a concurrent reader's stack
   frame may reference. Never merge the phases "for simplicity"; the
   integration test ``test_eviction_drains_readers`` fails red on
   single-phase variants.

2. **Per-user ``aiorwlock.RWLock`` = palace-scope write/read serialization.**
   The registry does NOT acquire these locks on behalf of callers — that's
   each route's job (ingest/search/wakeup acquire writer_lock/reader_lock
   as appropriate). The registry DOES acquire the writer_lock during
   Phase 2 of eviction to drain readers before handle.close().

3. **Library-isolation boundary.** This module does NOT import
   ``mempalace.*``. It consumes ``PalaceHandle`` which is the only
   module allowed to import the library (spec §5.3). A direct
   ``mempalace`` import here is a boundary violation.

See spec §5.4 for the LRU policy, ``docs/superpowers/plans/2026-04-20-mempalace-integration.md``
M0.3.1 through M0.3.5 for the implementation timeline, and
``palace_handle.py`` for the library surface this registry manages.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiorwlock import RWLock

from .palace_handle import PalaceHandle

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class PalaceEntry:
    """One resident palace + the bookkeeping we need to LRU / TTL it.

    Seven positional fields, in fixed order — spec §5.4, M0.3.1 test
    ``test_palace_entry_fields``, and the Hypothesis property test all
    construct PalaceEntry positionally, so reordering is a
    breaking-change to three call sites. Keep stable.

    ``handle`` is typed ``Any`` rather than ``PalaceHandle | None``
    because unit tests pass a ``MagicMock`` or ``object()`` sentinel;
    runtime callers always pass a real ``PalaceHandle``. A stricter
    annotation would force test shim authors to satisfy a full mock
    protocol for no registry-side benefit.
    """

    user_id: str
    handle: Any
    path: Path
    size_bytes: int
    schema_version: int
    opened_at: float
    last_used_at: float


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PalaceRegistry:
    """Per-user palace cache with size-weighted LRU + idle-TTL eviction.

    Concurrency model (spec §5.4):

    * ``self._registry_lock`` (asyncio.Lock) guards every mutation of
      ``self._cache`` and ``self._locks``. Acquired for very short
      critical sections only — never held across ``handle.close()``.
    * Per-user ``aiorwlock.RWLock`` in ``self._locks[user_id]``. Readers
      (search, list_drawers, wake-up) acquire ``.reader_lock``; writers
      (ingest, KG add_triple) acquire ``.writer_lock``. The registry
      itself acquires writer_lock only during Phase 2 of eviction.

    Two-phase eviction is non-negotiable. See module docstring and
    ``_evict_to_fit_unlocked``.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        max_bytes: int,
        idle_ttl_sec: int,
    ) -> None:
        self._data_dir: Path = Path(data_dir)
        self._palaces_dir: Path = self._data_dir / "palaces"
        self._palaces_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes: int = max_bytes
        self._idle_ttl_sec: int = idle_ttl_sec
        self._cache: "OrderedDict[str, PalaceEntry]" = OrderedDict()
        self._locks: dict[str, RWLock] = {}
        self._registry_lock: asyncio.Lock = asyncio.Lock()
        # One-line shutdown hedge: close_all() sets this True and any
        # subsequent open() raises instead of racing into an empty cache.
        # Today close_all is only called at shutdown and test-teardown,
        # and route-traffic draining is implicit — but "implicit" is how
        # shutdown bugs get shipped. Explicit flag removes the race.
        self._shutting_down: bool = False

    # ------------------------------------------------------------------
    # Lock bookkeeping
    # ------------------------------------------------------------------

    def lock_for(self, user_id: str) -> RWLock:
        """Lazy-create the per-user RWLock — race-free via dict.setdefault.

        Called from route handlers that need quick access to the per-user
        lock. Spec reviewer + quality reviewer both flagged the earlier
        "benign race" docstring as real: two concurrent cold lookups
        could each construct a fresh RWLock and each install its own
        copy, producing two mutex instances for one user. M0.4's ingest
        path is the first concurrent writer — two writers holding
        different "exclusive" locks is silent data corruption, not a
        "benign" race.

        Fix: ``self._locks.setdefault(user_id, new_lock)`` is
        GIL-atomic on CPython (single bytecode op), so two racing
        callers each construct their own RWLock but only one wins the
        install — and both callers receive the winning reference back.
        The loser's instance is garbage-collected. No registry-lock
        acquire needed on the hot path; no torn-read race on the cold
        path.
        """
        lock = self._locks.get(user_id)
        if lock is not None:
            return lock
        # Cold path. setdefault is GIL-atomic on CPython; racing
        # callers both get the same reference back.
        return self._locks.setdefault(user_id, RWLock())

    # ------------------------------------------------------------------
    # Public API: open + sweep
    # ------------------------------------------------------------------

    async def open(self, user_id: str) -> PalaceEntry:
        """Return the cached ``PalaceEntry`` for ``user_id``, opening a
        fresh PalaceHandle if cold.

        Two-phase eviction: Phase 1 runs under ``_registry_lock`` and
        populates ``to_close_after``. Phase 2 runs after the lock is
        released — that's the whole point.

        Raises ``RuntimeError`` if ``close_all`` has been called —
        shutdown hedge prevents a new open from racing into a freshly-
        cleared cache while existing handles are still being closed.
        """
        if self._shutting_down:
            raise RuntimeError(
                f"PalaceRegistry is shutting down; cannot open {user_id!r}"
            )
        to_close_after: list[tuple[str, PalaceEntry]] = []
        async with self._registry_lock:
            if user_id in self._cache:
                # Hot path: move to MRU end, refresh last_used_at, return
                # the existing entry. Identity matters — callers that
                # stash the entry reference don't want a new object on
                # the second open.
                self._cache.move_to_end(user_id)
                entry = self._cache[user_id]
                entry.last_used_at = time.monotonic()
            else:
                # Cold path: construct + insert + evict-to-fit. Phase 1
                # ONLY: we mutate the cache dict and collect the victim
                # list; we do NOT call close() while holding the
                # registry lock.
                entry = await self._construct_entry(user_id)
                self._cache[user_id] = entry
                # Ensure the per-user lock exists so Phase 2 can acquire
                # it (otherwise we'd need to create it after release,
                # and a concurrent reader could beat us to creating a
                # different instance).
                self.lock_for(user_id)
                to_close_after = self._evict_to_fit_unlocked()

        # Phase 2: registry lock released. For each evicted user, grab
        # the writer_lock (blocks on in-flight readers), then close.
        for evicted_uid, evicted_entry in to_close_after:
            await self._close_evicted(evicted_uid, evicted_entry)
        return entry

    async def sweep_idle(self) -> int:
        """Evict palaces whose ``last_used_at`` is older than
        ``idle_ttl_sec``. Returns the number evicted.

        Same two-phase pattern as ``open``: pop from cache under the
        registry lock, close under each evicted user's writer_lock
        after release. Spec §5.4; teeth-bearing via
        ``test_idle_eviction``.
        """
        now = time.monotonic()
        evicted: list[tuple[str, PalaceEntry]] = []
        async with self._registry_lock:
            idle_uids = [
                uid
                for uid, e in self._cache.items()
                if (now - e.last_used_at) > self._idle_ttl_sec
            ]
            for uid in idle_uids:
                entry = self._cache.pop(uid)
                evicted.append((uid, entry))

        for uid, entry in evicted:
            await self._close_evicted(uid, entry)
        return len(evicted)

    async def close_all(self) -> None:
        """Close every resident handle. Intended for shutdown (M0.10)
        and test teardown — the latter wants deterministic handle
        release so Chroma file locks don't survive ``tmp_path`` rmtree.

        Sets ``self._shutting_down = True`` inside the registry lock.
        Any ``open`` call that observes the flag raises rather than
        racing into the freshly-cleared cache.

        Not guarded by the registry lock across the close loop; we
        snapshot the entries, clear the cache, then close outside the
        lock. Same two-phase discipline as ``open`` and ``sweep_idle``.
        """
        async with self._registry_lock:
            self._shutting_down = True
            entries = list(self._cache.items())
            self._cache.clear()

        for uid, entry in entries:
            await self._close_evicted(uid, entry)

    # ------------------------------------------------------------------
    # Byte accounting
    # ------------------------------------------------------------------

    def _total_bytes(self) -> int:
        """Sum of resident palace sizes.

        Computed fresh each call — we deliberately do NOT cache this in
        a ``self._total`` int because every write (ingest, add_drawer)
        would have to remember to update it, and a missed update is
        the classic LRU-cap bug. The Hypothesis property test
        ``test_total_bytes_accurate`` asserts the invariant
        ``_total_bytes() == sum(size_bytes)`` after every op; the
        sum-on-demand implementation satisfies it by construction.
        """
        return sum(e.size_bytes for e in self._cache.values())

    # ------------------------------------------------------------------
    # Eviction: Phase 1 (cache mutation) and Phase 2 (handle.close)
    # ------------------------------------------------------------------

    def _evict_to_fit_unlocked(self) -> list[tuple[str, PalaceEntry]]:
        """Phase 1 of size-weighted LRU eviction. Called with
        ``_registry_lock`` HELD.

        Pops LRU entries from ``self._cache`` until either:
          * total bytes <= max_bytes, or
          * only one entry remains (we never evict down to zero — the
            caller just inserted it and a zero-size cache would thrash).

        Returns the list of ``(user_id, entry)`` tuples whose handles
        still need closing. The caller is responsible for Phase 2
        (close under writer_lock) — see ``_close_evicted``.

        Contract: this method does NOT call ``handle.close()`` and does
        NOT acquire any per-user lock. Attempting to do either here
        would either (a) hold the registry lock across a blocking
        close, or (b) deadlock if the writer_lock acquisition waited
        on a reader who was waiting on the registry lock.
        """
        to_close: list[tuple[str, PalaceEntry]] = []
        while (
            self._total_bytes() > self._max_bytes
            and len(self._cache) > 1
        ):
            # OrderedDict iteration order is insertion order; LRU end
            # is the front (oldest). popitem(last=False) gives the
            # LRU victim directly.
            uid, evicted = self._cache.popitem(last=False)
            to_close.append((uid, evicted))
        return to_close

    async def _close_evicted(
        self, uid: str, evicted_entry: PalaceEntry
    ) -> None:
        """Phase 2 of eviction. Called AFTER ``_registry_lock`` is
        released.

        Acquires the evicted user's ``writer_lock`` (which blocks until
        any in-flight readers finish), then calls ``handle.close()``.
        The writer-lock wait is what makes the two-phase pattern safe:
        a reader mid-call on this handle will finish their library
        invocation before close() runs.

        Idempotency: ``handle.close()`` is safe to call twice (spec
        §5.3 ``_closed`` guard). If a future refactor ends up calling
        ``_close_evicted`` twice on the same entry, no state is
        corrupted.

        Exception policy: we log and swallow. An exception in
        ``handle.close()`` leaks the backend cache entry (bad) but
        propagating it would break the rest of the eviction batch
        (worse). The leak is observable in
        ``_DEFAULT_BACKEND._clients`` and surfaces as memory growth in
        integration tests — a loud failure mode once M0.11 adds the
        cache-size metric.
        """
        # INVARIANT R1: _registry_lock MUST NOT be held here — the
        # two-phase eviction contract (§5.4) mandates Phase 2 runs
        # AFTER the registry lock is released so the writer_lock
        # acquire below can't deadlock against a concurrent reader
        # waiting on _registry_lock. Mechanised via assert rather
        # than docstring-only so CI catches any refactor that moves
        # this call back inside the lock.
        assert not self._registry_lock.locked(), (
            "two-phase eviction violated: _close_evicted called while "
            "_registry_lock is held (see spec §5.4)"
        )
        handle = getattr(evicted_entry, "handle", None)
        if handle is None:
            return

        lock = self._locks.get(uid)
        if lock is None:
            # No per-user lock ever registered — nothing holds a
            # reader reference on this palace, so close directly.
            # This happens in test paths where ``_construct_entry`` is
            # mocked and ``open`` short-circuits before
            # ``lock_for``; production paths always register the lock.
            self._close_handle_safely(handle)
            return

        async with lock.writer_lock:
            self._close_handle_safely(handle)

    @staticmethod
    def _close_handle_safely(handle: Any) -> None:
        """Call ``handle.close()`` swallowing exceptions. Defensive
        because M0.3 is the last place an unexpected library raise can
        derail eviction — by M0.11 we'll have metrics on this.

        Narrow catch: ``handle.close()`` can raise ``sqlite3.Error``
        (KG or turns connection close), ``OSError`` (file handle cleanup),
        ``AttributeError`` / ``KeyError`` (half-constructed handle with
        missing attrs). Bare ``Exception`` would also swallow programmer
        errors like ``TypeError`` that should surface — narrowed in
        response to M0.3.1-5 quality review.
        """
        try:
            handle.close()
        except (sqlite3.Error, OSError, AttributeError, KeyError) as exc:
            log.warning(
                "handle.close() raised during registry eviction: %s", exc
            )

    # ------------------------------------------------------------------
    # Entry construction — opens a real PalaceHandle (M0.3.4)
    # ------------------------------------------------------------------

    async def _construct_entry(self, user_id: str) -> PalaceEntry:
        """Open a fresh palace and build a PalaceEntry around it.

        Overridden by unit tests that want a MagicMock handle and a
        synthetic size — the test-time signature is the same, so the
        registry doesn't know whether it's talking to a real palace or
        a mock.

        Spec §5.6 dimension 1 (Chroma on-disk format migration) runs
        inside ``PalaceHandle.open`` — the registry stays ignorant of
        format versioning. Same for ``meta.json`` schema_version
        tracking.
        """
        path = self._palaces_dir / user_id
        # PalaceHandle.open is synchronous; on first-ever open for a
        # new palace it pays ~50-200 ms for Chroma PersistentClient
        # init (spec §5.3 "Lifecycle"). In M0.5 the concurrency layer
        # wraps this in asyncio.to_thread when latency matters; M0.3
        # accepts the inline cost — open is a once-per-user event.
        handle = PalaceHandle.open(path)
        size = _dir_size_bytes(path)
        now = time.monotonic()
        return PalaceEntry(
            user_id=user_id,
            handle=handle,
            path=path,
            size_bytes=size,
            schema_version=int(handle.meta.get("schema_version", 1)),
            opened_at=now,
            last_used_at=now,
        )


# ---------------------------------------------------------------------------
# Disk walker
# ---------------------------------------------------------------------------


def _dir_size_bytes(p: Path) -> int:
    """Recursive disk footprint in bytes. Broken symlinks and races
    against concurrent deletes are silently skipped — size is used for
    LRU cap enforcement, and an off-by-one from a deleted file won't
    push us into the next eviction tier.
    """
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except FileNotFoundError:
                # File deleted between walk() and stat() — skip.
                continue
            except OSError as exc:  # pragma: no cover - defensive
                log.warning("stat(%s) failed: %s", fp, exc)
                continue
    return total


__all__ = ["PalaceEntry", "PalaceRegistry"]

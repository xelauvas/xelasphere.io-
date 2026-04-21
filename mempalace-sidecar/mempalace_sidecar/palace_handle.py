"""PalaceHandle — library isolation boundary for ``mempalace==3.3.1``.

Spec §5.3 is the authoritative contract. This module is the ONLY place
in the sidecar that imports from ``mempalace``. Every route in M0.4-M0.8
calls methods on a ``PalaceHandle`` instance; the library can be swapped,
forked, or rewritten by editing this file alone.

Three cross-cutting invariants:

1. ``turns.sqlite`` is the authoritative idempotency store (spec §4.1).
   The library-derived drawer_id is a deterministic function of
   ``source_file=f"turn:{turn_id}"`` + ``chunk_index=0`` — we compute it
   locally and never rely on the library to dedupe for us.

2. Every ``close()`` MUST pop the library's backend client cache
   (spec §5.3 "cache-eviction contract"). The library exposes no public
   eviction API, so we reach into ``_DEFAULT_BACKEND._clients`` and
   remove the entry. Failing to do so leaks one PersistentClient per
   ever-opened palace.

3. ``close()`` is idempotent by CONTRACT (not optimization) — second and
   subsequent calls are no-ops. Eviction-during-eviction (size-driven
   eviction concurrent with idle-sweep) is a real race; a ``self._closed``
   guard makes redundant close()s safe.

See ``docs/superpowers/specs/2026-04-20-mempalace-integration-design.md``
section 5.3 for the full contract, the method-mapping table, the
``turns.sqlite`` schema, and the concurrency model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

# Library imports. The sidecar MUST touch mempalace in exactly one place;
# every symbol we use from the library is aliased here, making future
# library changes surface as import-time errors in this file alone.
#
# Deliberately NOT imported: the library's MCP-server dispatch layer. Its
# handlers use module-level global palace state and would cause cross-user
# data leaks in a multi-tenant sidecar. See spec §5.3 "Why not bridge to
# the MCP dispatcher"; enforced by ``test_no_mcp_server_import``.
from mempalace.config import MempalaceConfig
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.miner import add_drawer as _library_add_drawer
from mempalace.palace import _DEFAULT_BACKEND, get_collection
from mempalace.searcher import (
    build_where_filter,
    search_memories as _library_search_memories,
)

from .schemas import AddDrawerResult

log = logging.getLogger(__name__)


# Current meta.json schema_version. Incremented by migrations in M0.9.
CURRENT_SCHEMA_VERSION = 1

# Default drawer placement for conversation turns. See M0.5 for the
# wake-up composition that consumes these.
DEFAULT_WING = "conversations"
DEFAULT_ROOM = "session"
DEFAULT_AGENT = "xelasphere"


# turns.sqlite DDL — literal schema from spec §5.3. Don't add columns.
# The indexes are spec-mandated and used by ``session_drawers`` (ordered
# session lookup) and the M0.4.2 pair-id retrieval path respectively.
_TURNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    drawer_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    ts REAL NOT NULL,
    filed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_session_ts ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS turns_pair ON turns(pair_id);
"""


def _derive_drawer_id(*, turn_id: str, wing: str, room: str) -> str:
    """Compute the drawer_id the library will assign for a given turn.

    This mirrors ``mempalace.miner.add_drawer`` exactly:

        f"drawer_{wing}_{room}_{sha256(source_file + str(chunk_index))[:24]}"

    where ``source_file = f"turn:{turn_id}"`` and ``chunk_index = 0``.
    Computing it ourselves means we can populate ``turns.sqlite`` with
    the deterministic ID even before the library completes its write,
    and we can detect future upstream hash-scheme changes (via
    ``test_turns_authoritative_when_library_id_derivation_changes``).
    """
    source_file = f"turn:{turn_id}"
    digest = hashlib.sha256((source_file + str(0)).encode()).hexdigest()[:24]
    return f"drawer_{wing}_{room}_{digest}"


class PalaceHandle:
    """Per-user wrapper around the mempalace functional API.

    Constructed via :classmethod:`open`; closed exactly once by the
    registry's eviction path (spec §5.4 two-phase eviction). All method
    bodies are synchronous Python — the sidecar's async routes wrap calls
    in ``asyncio.to_thread`` when real parallelism is required (spec §5.3
    "Concurrency model").

    **Concurrency invariant — caller holds the per-user write lock for
    mutating methods.** ``add_drawer`` (and future M0.7 KG writes via
    ``self.kg.add_triple``) must be invoked under the registry's
    per-user ``aiorwlock.writer_lock`` (spec §5.4). Read-only methods
    (``recent_drawers``, ``session_drawers``, ``semantic_search``,
    ``topic_search``, ``search``, ``list_drawers``, and future M0.7
    ``self.kg.query_entity`` / ``self.kg.timeline`` reads) may run
    under the reader lock. This class does NOT
    serialise concurrent writes itself — `self.turns` is opened with
    ``check_same_thread=False`` so it can be touched from asyncio thread
    pool workers, but sqlite3 connection-level serialisation only
    prevents SQLite corruption, not logical transaction interleaving.
    A future registry edit that allowed overlapping writes on one palace
    would produce ``sqlite3.OperationalError: cannot start a transaction
    within a transaction`` and a corrupt ``turns`` table. This is the
    single most important cross-module invariant in the sidecar.
    """

    def __init__(
        self,
        palace_path: Path,
        collection: Any,
        kg: KnowledgeGraph,
        config: MempalaceConfig,
        turns: sqlite3.Connection,
        meta: dict,
    ) -> None:
        self.palace_path: Path = Path(palace_path)
        # Cached Chroma collection. Reused across every method on this
        # handle; not re-resolved per-call.
        self._collection = collection
        self.kg: KnowledgeGraph = kg
        self.config: MempalaceConfig = config
        self.turns: sqlite3.Connection = turns
        self.meta: dict = meta
        # size_bytes is refreshed on ingest + on eviction sweep in M0.3.x.
        # M0.3.0 doesn't populate it; registry code does.
        self.size_bytes: int = 0
        self._closed: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def open(cls, palace_path: str | Path) -> "PalaceHandle":
        """Open (or create) a palace at ``palace_path``.

        Creates the palace directory + all child state (``kg.sqlite``,
        ``turns.sqlite``, ``config/``, ``meta.json``) if missing. Cheap
        once the library's Chroma client cache is warm (~1-5 ms);
        first-ever open for a new palace pays the PersistentClient init
        cost (~50-200 ms). Embedding model weights are process-global
        (see spec §5.3 "Embedding-model scope").
        """
        palace_path = Path(palace_path)
        palace_path.mkdir(parents=True, exist_ok=True)

        # meta.json: read if present, create if new. The spec reserves
        # schema_version + created_at for v1; migrations (M0.9) will add
        # more fields here.
        meta_path = palace_path / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError as e:
                # Corrupt meta.json is a 500-worthy error upstream. We
                # raise here so the caller (registry) can decide policy;
                # silently overwriting would destroy migration state.
                raise RuntimeError(
                    f"corrupt meta.json at {meta_path}: {e}"
                ) from e
        else:
            meta = {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "created_at": time.time(),
            }
            meta_path.write_text(json.dumps(meta))

        # Chroma on-disk format migration (spec §5.6 dimension 1). Done
        # BEFORE opening the collection, because migrating an already-open
        # client is undefined behavior.
        _maybe_migrate_chromadb_format(palace_path)

        # Open or create the drawers collection. get_collection(create=True)
        # is the right entry point — it populates _DEFAULT_BACKEND._clients
        # which close() later has to pop.
        collection = get_collection(str(palace_path), create=True)

        # Knowledge graph — library-owned OOP class, keyed on a sqlite path
        # under the palace directory. ``KnowledgeGraph.__init__`` creates
        # the parent dir and chmods it 0o700; no double-chmod needed on
        # our side.
        kg = KnowledgeGraph(db_path=str(palace_path / "kg.sqlite"))

        # Per-user config (people_map, topic_wings, entity_languages). Must
        # NOT fall back to ~/.mempalace/ — that's process-global and would
        # cause cross-tenant leaks.
        config_dir = palace_path / "config"
        config_dir.mkdir(exist_ok=True)
        config = MempalaceConfig(config_dir=config_dir)

        # turns.sqlite — sidecar-owned, literal schema from spec §5.3.
        # check_same_thread=False because M0.5 will call handle methods
        # across asyncio tasks via asyncio.to_thread, which hands the
        # connection to different worker threads.
        turns = sqlite3.connect(
            str(palace_path / "turns.sqlite"),
            check_same_thread=False,
        )
        turns.executescript(_TURNS_SCHEMA)
        turns.commit()

        return cls(palace_path, collection, kg, config, turns, meta)

    def close(self) -> None:
        """Close all owned state and pop the library backend cache.

        Idempotent by contract — second and subsequent calls are no-ops.
        The ``self._closed`` guard is load-bearing for two reasons:

        1. ``self.turns.close()`` on an already-closed sqlite3 connection
           raises ``sqlite3.ProgrammingError: Cannot operate on a closed
           database.`` The guard prevents the second-close path from
           hitting it.
        2. Eviction-during-eviction (size eviction + idle-sweep targeting
           the same user concurrently) is a real race. The registry's
           two-phase pattern (spec §5.4) narrows the window, but the
           guard is the last line of defense.

        ``KnowledgeGraph.close()`` is separately idempotent in the library
        (verified: ``mempalace.knowledge_graph.KnowledgeGraph.close``
        guards on ``self._connection is not None``), so the guard doesn't
        strictly protect step 1, only step 2. We keep it for the race.

        Close order (spec §5.3, amended 2026-04-21):
            1. KG sqlite  (library-owned; self-idempotent)
            2. turns.sqlite  (sidecar-owned; NOT idempotent without guard)
            3. _DEFAULT_BACKEND._clients.pop  (library's Chroma cache)
            4. client.close()  (explicit release of chromadb SQLite file locks)

        Step 4 was added at M0.8 after chromadb 1.5.x contract drift
        surfaced: the library ships ``PersistentClient.close()``
        specifically to release SQLite file locks, and relying on
        GC to finalize the client (the pre-M0.8 approach) leaves
        locks held long enough for a subsequent open-to-the-same-path
        to hit SQLITE_READONLY_DBMOVED. See the module comment near
        the pop/close call for the full diagnostic trail.
        """
        if self._closed:
            return

        # Step 1: KG sqlite. Narrow catch — sqlite3.Error covers every
        # operational/programming failure; AttributeError covers the
        # partially-constructed-handle case (ctor raised before self.kg
        # was assigned).
        try:
            self.kg.close()
        except (sqlite3.Error, AttributeError) as e:  # pragma: no cover - defensive
            log.warning("kg.close() raised during PalaceHandle.close: %s", e)

        # Step 2: turns.sqlite.
        try:
            self.turns.close()
        except (sqlite3.Error, AttributeError) as e:  # pragma: no cover - defensive
            log.warning("turns.close() raised during PalaceHandle.close: %s", e)

        # Step 3: library backend cache pop + explicit client.close().
        # MUST run last — if it ran first, a concurrent reader with
        # self._collection in its stack frame would reference a client
        # whose file handles we just released. Two-phase eviction (spec
        # §5.4) guarantees no such reader exists, but keeping the pop
        # last is defensive.
        #
        # Use str(self.palace_path) to match what get_collection
        # inserted; the library keys _clients by whatever string the
        # caller passed.
        #
        # **chromadb 1.5.8 requires an explicit client.close() to
        # release SQLite file locks** (discovered M0.8 via DBMOVED
        # error code 1032 on the DELETE-then-reopen path). chromadb's
        # docstring: "This is particularly important for PersistentClient
        # to avoid SQLite file locking issues." Without this call,
        # removing the client from _clients + relying on Python GC is
        # insufficient — chromadb's Rust backend retains state that
        # causes SQLITE_READONLY_DBMOVED on a fresh PersistentClient
        # opened for the same path after rmtree. Upstream issue in
        # mempalace.backends.chroma worth filing (ChromaBackend should
        # call client.close() before popping); fixed at our boundary
        # for now.
        client = _DEFAULT_BACKEND._clients.pop(str(self.palace_path), None)
        if client is not None:
            # ``hasattr`` guard is forward-compat: if a future chromadb
            # version removes or renames ``close()``, we silently fall
            # back to the pre-M0.8 GC-only lifecycle rather than
            # log-spamming every close path. This matches the behavior
            # on chromadb 0.6.x where ``client.close()`` didn't exist
            # and the pop-and-GC pattern was sufficient.
            if hasattr(client, "close"):
                try:
                    client.close()
                except Exception as e:
                    # Unexpected: ``close()`` exists but raised. Log
                    # at error level with exc_info so an operator can
                    # file a chromadb issue with a useful traceback.
                    log.error(
                        "chromadb client.close() raised during "
                        "PalaceHandle.close: %s",
                        e, exc_info=True,
                    )
            # else: no ``close`` method (older chromadb or hypothetical
            # future rename). Silent — GC fallback is the pre-M0.8
            # behavior that worked fine at that version; log-spamming
            # on every close would be operational noise.

        self._closed = True

    # ------------------------------------------------------------------
    # Drawer writes
    # ------------------------------------------------------------------

    def add_drawer(
        self,
        *,
        turn_id: str,
        session_id: str,
        pair_id: str,
        role: str,
        text: str,
        ts: float,
        wing: str = DEFAULT_WING,
        room: str = DEFAULT_ROOM,
        agent: str = DEFAULT_AGENT,
    ) -> AddDrawerResult:
        """File a conversation turn as a drawer, idempotent by turn_id.

        Ordering (spec §5.3, §4.1):

        1. Check ``turns.sqlite`` for the turn_id. If present, short-circuit
           and return ``AddDrawerResult(status="duplicate", drawer_id=...)``.
           The library is NOT called. This is the empirical property
           verified by ``test_turns_authoritative_when_library_id_derivation_changes``:
           if the library ever changes how it derives drawer_ids from
           source_file, our dedup still works because turns.sqlite is the
           authority.

        2. Derive the drawer_id locally. Must match the library's formula
           (``sha256(source_file + str(chunk_index))[:24]``) because the
           library will use that ID when it upserts, and we store it in
           turns.sqlite for reverse lookup.

        3. Call the library's ``miner.add_drawer`` to file the drawer.
           Library return value is ignored beyond truthiness checks — the
           authoritative side-effect is the Chroma upsert.

        4. Insert into turns.sqlite with ``INSERT OR IGNORE`` — belt-and-
           suspenders defense against the tiny race where two concurrent
           calls with the same turn_id both pass the step-1 check. OR
           IGNORE makes the second row insert a no-op; the already-filed
           drawer in Chroma is upserted idempotently by the library.
        """
        cur = self.turns.execute(
            "SELECT drawer_id FROM turns WHERE turn_id = ?",
            (turn_id,),
        )
        row = cur.fetchone()
        if row is not None:
            return AddDrawerResult(status="duplicate", drawer_id=row[0])

        drawer_id = _derive_drawer_id(turn_id=turn_id, wing=wing, room=room)
        source_file = f"turn:{turn_id}"

        # Library call. Signature per mempalace 3.3.1:
        #   add_drawer(collection, wing, room, content, source_file,
        #              chunk_index, agent) -> bool
        _library_add_drawer(
            self._collection, wing, room, text, source_file, 0, agent,
        )

        self.turns.execute(
            "INSERT OR IGNORE INTO turns "
            "(turn_id, drawer_id, session_id, pair_id, role, ts, filed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (turn_id, drawer_id, session_id, pair_id, role, ts, time.time()),
        )
        self.turns.commit()
        # Crash-safety note: if this line (commit) or the INSERT above had
        # raised BEFORE returning, Chroma would hold the drawer but turns
        # would have no row. An outer retry with the same turn_id would
        # pass the step-1 SELECT guard and re-call _library_add_drawer,
        # which upserts on the DETERMINISTIC drawer_id derived in step 2.
        # Same drawer_id → Chroma upsert overwrites the identical row
        # with identical content. Safe by coincidence of the ID-derivation
        # contract, not by explicit transaction semantics. If that contract
        # ever weakens, this path needs a WAL or 2PC scheme.

        return AddDrawerResult(status="accepted", drawer_id=drawer_id)

    # ------------------------------------------------------------------
    # Retrieval — the four wake-up layers + general search + list
    # ------------------------------------------------------------------

    def recent_drawers(self, limit: int = 10) -> list[dict]:
        """Our L0: the ``limit`` most recently filed drawers.

        Chroma has no ``ORDER BY``; we over-fetch 4× and sort in Python
        on the library-written ``filed_at`` ISO string metadata. That
        field sorts lexicographically for ISO 8601, so string compare
        matches time compare. For large palaces (>10k drawers) this gets
        expensive — M0.5's integration test flags p95 regressions.
        """
        over = max(limit * 4, 40)
        got = self._collection.get(limit=over, include=["documents", "metadatas"])
        rows = [
            {"text": d, "metadata": m}
            for d, m in zip(got.get("documents") or [], got.get("metadatas") or [])
        ]
        # filed_at is an ISO-8601 string written by miner.add_drawer.
        # Lexicographic reverse sort == chronological reverse sort for
        # well-formed ISO strings.
        rows.sort(key=lambda r: r["metadata"].get("filed_at", ""), reverse=True)
        return rows[:limit]

    def session_drawers(self, session_id: str, limit: int = 10) -> list[dict]:
        """Our L1: drawers from a specific conversation session.

        Cheap path: sqlite index lookup on ``turns_session_ts`` then a
        single Chroma ``get(ids=...)``. Library has no concept of
        "session" — that's a XelaSphere-domain concept owned by
        turns.sqlite.
        """
        rows = self.turns.execute(
            "SELECT drawer_id FROM turns WHERE session_id = ? "
            "ORDER BY ts DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        drawer_ids = [r[0] for r in rows]
        if not drawer_ids:
            return []
        got = self._collection.get(
            ids=drawer_ids,
            include=["documents", "metadatas"],
        )

        # Chroma ``get(ids=...)`` does NOT preserve the input ID order;
        # it returns rows in storage order. Rebuild the session-order
        # sequence by looking up each ID in the result.
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []
        ids = got.get("ids") or []
        by_id = {i: {"text": d, "metadata": m} for i, d, m in zip(ids, docs, metas)}
        return [by_id[i] for i in drawer_ids if i in by_id]

    def semantic_search(self, query: str, k: int = 5) -> list[dict]:
        """Our L3: hybrid BM25 + vector + closet-boost search.

        Bridged to ``searcher.search_memories`` which returns a dict
        envelope ``{"query", "filters", "total_before_filter", "results"}``.
        We unwrap to the ``results`` list so callers deal with a
        homogeneous ``list[dict]`` across all four layers.
        """
        result = _library_search_memories(
            query, str(self.palace_path), n_results=k,
        )
        return result.get("results", [])

    # Our L2: v1 behaves identically to semantic_search. Topic-filter
    # logic is deferred — see spec §10 Open Question.
    def topic_search(self, query: str, k: int = 5) -> list[dict]:
        return self.semantic_search(query, k=k)

    def search(
        self,
        query: str,
        *,
        wing: str | None = None,
        k: int = 5,
    ) -> list[dict]:
        """Tool-call search (M0.6). Thin bridge to ``search_memories``
        with the ``wing`` pass-through.
        """
        result = _library_search_memories(
            query, str(self.palace_path), wing=wing, n_results=k,
        )
        return result.get("results", [])

    def list_drawers(
        self,
        *,
        wing: str | None = None,
        room: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """GDPR-admin listing with offset pagination.

        Returns ``{"drawers", "total", "next_offset"}``. ``next_offset``
        is ``None`` when this page reaches the end of the collection.
        """
        # ``build_where_filter(None, None) == {}``, which Chroma rejects
        # ("Expected where to have exactly one operator, got {}"). Only
        # pass ``where`` when we have a real filter.
        where = build_where_filter(wing, room)
        kwargs: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "include": ["documents", "metadatas"],
        }
        if where:
            kwargs["where"] = where

        got = self._collection.get(**kwargs)
        drawers = [
            {"text": d, "metadata": m}
            for d, m in zip(
                got.get("documents") or [], got.get("metadatas") or []
            )
        ]

        # Honest totals — when a wing/room filter is applied we CANNOT
        # return a correct total without a second Chroma call (col.count()
        # has no filter argument). Rather than returning a wrong number
        # (unfiltered total) that the Flask adapter would render as
        # "showing 10 of 2843" when the filtered set is actually 47 rows,
        # we return total=None for filtered queries and let the Flask
        # adapter render "showing 10 of many" or omit the count. The
        # unfiltered path returns the real count. M0.6 may revisit if a
        # filtered-count matters enough to pay the extra library call.
        if where:
            total: int | None = None
        else:
            total = self._collection.count()

        # next_offset semantics: None means "no more pages." When we know
        # total, compute precisely. When total is unknown (filtered case),
        # signal "more may exist" if we returned a full page — the caller
        # keeps paging until they get a short page, which signals end.
        if total is not None:
            next_offset = offset + limit if offset + limit < total else None
        else:
            next_offset = offset + limit if len(drawers) == limit else None

        return {
            "drawers": drawers,
            "total": total,
            "next_offset": next_offset,
        }

    # ------------------------------------------------------------------
    # Knowledge graph (M0.7)
    # ------------------------------------------------------------------
    # Deferred: kg_add_triple / kg_query_entity / kg_timeline were removed
    # from M0.3.0 and will land in M0.7 with EXPLICIT typed signatures.
    # Reason: `*args, **kwargs` passthroughs occupy the library-isolation
    # namespace without providing isolation — if the upstream library
    # renames a KnowledgeGraph parameter, downstream callers break just
    # as hard as if they imported KnowledgeGraph directly. M0.7 will
    # introduce methods that mirror the current library API explicitly,
    # so a rename shows up HERE first and can be absorbed by updating
    # one file. Callers between now and M0.7 use `self.kg` directly;
    # that's permitted because M0.3-M0.6 don't touch KG.

    # ------------------------------------------------------------------
    # Escape hatch — raw Chroma collection access
    # ------------------------------------------------------------------

    @property
    def col(self):
        """Raw Chroma collection. Use only when no named method covers
        the use case (e.g. M0.8 ``DELETE /palace/{uid}`` enumerates +
        deletes via ``col.get`` + ``col.delete``). Prefer a named method.
        """
        return self._collection


# ---------------------------------------------------------------------------
# Chroma on-disk format migration
# ---------------------------------------------------------------------------


def _maybe_migrate_chromadb_format(palace_path: Path) -> None:
    """Migrate the palace's Chroma on-disk format if it predates the
    currently-installed chromadb. Safe no-op for brand-new palaces.

    Spec §5.6 dimension 1: Chroma on-disk format migration is library-
    managed. We only trigger ``migrate()`` when:

    1. ``chroma.sqlite3`` already exists (else there's nothing to
       migrate — brand-new palace path).
    2. ``detect_chromadb_version`` reports a KNOWN-OLD version
       (``"0.6.x"``). Fresh 1.x palaces return ``"1.x"`` which matches
       the installed 1.5.8 well enough that we don't re-migrate.

    ``mempalace.migrate.detect_chromadb_version`` signature takes a
    ``db_path`` (the sqlite file), NOT a palace dir — a quirk worth
    calling out. See deviations note in the M0.3.0 dispatch report.
    """
    db_path = palace_path / "chroma.sqlite3"
    if not db_path.exists():
        return

    try:
        from mempalace.migrate import detect_chromadb_version, migrate
    except ImportError:  # pragma: no cover - library always ships this
        return

    try:
        on_disk = detect_chromadb_version(str(db_path))
    except Exception as e:
        log.warning(
            "chromadb version detection failed for %s: %s — skipping migrate",
            db_path, e,
        )
        return

    if on_disk == "0.6.x":
        log.info("migrating palace at %s from chromadb 0.6.x", palace_path)
        migrate(str(palace_path), dry_run=False, confirm=True)


__all__ = [
    "PalaceHandle",
    "AddDrawerResult",
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_WING",
    "DEFAULT_ROOM",
    "DEFAULT_AGENT",
]

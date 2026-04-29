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
import os
import re
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


# Cross-topic noise floor for semantic_search / topic_search (L2/L3).
# Items with similarity below this threshold are filtered out so
# off-topic queries don't pollute the LLM's context with low-relevance
# project memory. Calibrated from Marcus persona dogfood (2026-04-29):
# legitimate recall hits scored 0.33-0.46, cross-topic noise scored
# 0.17-0.21. The 0.25 floor sits between noise and signal.
# Tunable in prod via MEMPALACE_SEMANTIC_SCORE_THRESHOLD.
DEFAULT_SEMANTIC_SCORE_THRESHOLD = 0.25


def _semantic_score_threshold() -> float:
    raw = os.environ.get("MEMPALACE_SEMANTIC_SCORE_THRESHOLD")
    if raw is None:
        return DEFAULT_SEMANTIC_SCORE_THRESHOLD
    try:
        return float(raw)
    except (TypeError, ValueError):
        log.warning(
            "Invalid MEMPALACE_SEMANTIC_SCORE_THRESHOLD=%r; using default %.2f",
            raw, DEFAULT_SEMANTIC_SCORE_THRESHOLD,
        )
        return DEFAULT_SEMANTIC_SCORE_THRESHOLD


# Retrieval buffer multiplier — fetch ``k * multiplier`` candidates from
# the library so post-filter trim back to ``k`` doesn't truncate when
# items are dropped by the score floor or the hedge filter. Surfaced
# 2026-04-29 alongside PR #107: at chroma fetch=k=5, two slots were
# pre-deploy short hedges; PR #107 dropped them but the result truncated
# to 3 with no backfill — burying the real ``sub-3:00`` answer at chroma
# rank 4. With multiplier=3 + cap=50, fetch=15 candidates, 10 may drop,
# the 5 best survivors fill k.
DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER = 3

# Absolute cap on chroma fetch size — caller-controlled k can in theory
# be unbounded, but multiplier × k must not blow up retrieval cost. 50
# is a soft ceiling; bump if larger k usage proves real.
DEFAULT_RETRIEVAL_BUFFER_CAP = 50


def _retrieval_buffer_multiplier() -> int:
    raw = os.environ.get("MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER")
    if raw is None:
        return DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER
    try:
        n = int(raw)
        if n < 1:
            log.warning(
                "MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER=%r must be >= 1; "
                "using default %d", raw, DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER,
            )
            return DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER
        return n
    except (TypeError, ValueError):
        log.warning(
            "Invalid MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER=%r; using default %d",
            raw, DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER,
        )
        return DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER


# Read-time hedge filter (companion to PR #103 write-time filter in
# ``app/mempalace/adapter.py``). PR #103 prevents new "I don't know"
# assistant turns from being indexed; this filter drops pre-deploy hedges
# that are still in older palaces and would otherwise outrank real
# establishment turns at retrieval time. Surfaced by Marcus persona bench
# 2026-04-29: "What's my A-race target time again?" returned a hedge at
# similarity 0.705 ahead of the real "sub-3:00" turn at 0.436.
#
# Patterns and length threshold mirror PR #103 exactly. Filter applies
# only to assistant turns — text whose body starts with the
# ``ACTION: chat`` marker (the legacy adapter's prefix). User turns,
# even short ones with hedge phrasing, carry establishment-grade signal
# and are kept.
_HEDGE_LEN_THRESHOLD = 250
_HEDGE_ASSISTANT_PREFIX = "ACTION: chat"

_HEDGE_PATTERNS = [
    re.compile(r"\bi don'?t have (any|the)?\s*info", re.IGNORECASE),
    re.compile(r"(you|i) haven'?t (been |yet )?(told|mentioned|said|provided)", re.IGNORECASE),
    re.compile(r"\bcould you (please )?(provide|clarify|share|tell)", re.IGNORECASE),
    re.compile(r"\bwithout (more )?(context|information|details)", re.IGNORECASE),
    re.compile(r"\bi don'?t know\b", re.IGNORECASE),
    re.compile(r"\bi'?m (sorry|not sure)\b", re.IGNORECASE),
]


def _is_assistant_hedge_result(item: dict) -> bool:
    """True iff a search result looks like a short assistant hedge.

    Three conditions, all required:
      1. ``item['text']`` begins with the ``ACTION: chat`` marker (the
         legacy assistant-turn prefix). User turns lack this prefix.
      2. The full text length is below ``_HEDGE_LEN_THRESHOLD``.
      3. The text matches at least one hedge pattern.

    Returns False on any malformed item — defensive against schema drift.
    """
    text = item.get("text") if isinstance(item, dict) else None
    if not isinstance(text, str):
        return False
    if not text.startswith(_HEDGE_ASSISTANT_PREFIX):
        return False
    if len(text) >= _HEDGE_LEN_THRESHOLD:
        return False
    return any(p.search(text) for p in _HEDGE_PATTERNS)


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

        # Sidecar schema migration (spec §5.6 dimension 3, M0.9).
        #
        # **Ordering is load-bearing (spec §5.6 amendment
        # 2026-04-21).** Dim1 (above) runs BEFORE dim3 (here) because
        # dim1 modifies chroma.sqlite3 in place and dim3 uses
        # copy_function=os.link (hardlinks). If dim1 ran after dim3,
        # the in-place write would propagate through the hardlink to
        # the .replaced_<ts> snapshot, breaking rollback integrity.
        # Future open-path additions that modify palace files in
        # place MUST slot before this apply_migrations call, or use
        # copy-on-write semantics (check os.stat(f).st_nlink; if > 1,
        # unlink before write).
        from mempalace_sidecar.migrations import ALL_MIGRATIONS
        from mempalace_sidecar.migrations.runner import apply_migrations

        current_v = int(meta.get("schema_version", 0))
        if current_v > CURRENT_SCHEMA_VERSION:
            # Spec §5.6 step 2 — schema too new for this sidecar.
            # Route handlers map RuntimeError → HTTP 500 via
            # FastAPI's default exception handling.
            raise RuntimeError(
                f"palace at {palace_path} has schema_version="
                f"{current_v} > sidecar CURRENT_SCHEMA_VERSION="
                f"{CURRENT_SCHEMA_VERSION}; sidecar too old"
            )
        if current_v < CURRENT_SCHEMA_VERSION:
            apply_migrations(
                palace_path,
                target_version=CURRENT_SCHEMA_VERSION,
                migrations=ALL_MIGRATIONS,
            )
            # Re-read meta.json — the runner rewrote it during the
            # migration's finalise step and the in-memory dict above
            # is now stale.
            meta = json.loads(meta_path.read_text())

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
        We unwrap to the ``results`` list and apply two predicates:

        - relevance-score floor (``_semantic_score_threshold``, default 0.25)
          so off-topic queries don't pollute the LLM's context with
          low-similarity project memory.
        - read-time hedge filter (companion to PR #103) drops short
          assistant ``I don't know`` / ``could you clarify`` responses
          that pre-date the write-time filter and still outrank real
          turns. Items missing the ``similarity`` field are treated as
          0.0 and dropped.

        Buffer-fetch contract: we ask the library for ``k * multiplier``
        candidates (capped at ``DEFAULT_RETRIEVAL_BUFFER_CAP``) so the
        post-filter trim back to ``k`` doesn't truncate when items get
        dropped. The library may still return fewer candidates than
        requested if the palace is small — caller may receive < k.
        """
        multiplier = _retrieval_buffer_multiplier()
        n_results = min(k * multiplier, DEFAULT_RETRIEVAL_BUFFER_CAP)
        result = _library_search_memories(
            query, str(self.palace_path), n_results=n_results,
        )
        items = result.get("results", []) or []
        threshold = _semantic_score_threshold()
        filtered = [
            r for r in items
            if (r.get("similarity") or 0.0) >= threshold
            and not _is_assistant_hedge_result(r)
        ]
        return filtered[:k]

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

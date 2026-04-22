"""MempalaceAdapter — stable public surface (M0.17 + M0.18).

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4178-4292 (Task M0.17.4). Spec §3: this module is the only one
the rest of the Flask app imports; everything else in `app/mempalace/`
is an implementation detail.

**M0.17 Deviations (shipped at M0.17 Stage 4c fold):**

  - **D2.** `record_turn` catches `sqlite3.IntegrityError` before the
    generic `Exception` handler, labels `inserts_dropped
    ("duplicate_turn_id")`. Idempotency pinned NON-idempotent per spec
    §4.1 line 183 (sidecar-side `turns.sqlite` is the authority).
  - **D3.** Mode-off metric fires at the adapter site (spec §3 line 137).
  - **D4.** `wake_up` passes `session_id` through to the sidecar body.
  - **H5 (doc-only).** `wake_up` returns sidecar response unmodified in
    SHADOW mode; orchestrator is responsible for not injecting.

**M0.18 Deviations pinned at pre-resolution (land at Stage 3 GREEN):**

  - **D1 (Option γ — parallel-methods with extracted body helpers).**
    `wake_up_sync` and `search_sync` exist as sync variants for Flask
    sync-worker callers that cannot `await` the async methods. Option γ
    extracts body construction into `_wake_up_body` and `_search_body`
    helpers, shared by async and sync variants. Mode-gate + try/except
    structure duplicates (4 lines each) but is small enough that
    invariant-comment discipline keeps drift manageable. The
    alternative — async-via-runner using `asyncio.run` — was rejected
    because `httpx.AsyncClient` in `self._client` binds to its
    construction-time loop (M0.14 doctrine); a fresh loop per sync call
    violates the binding. SyncSidecarClient avoids the issue entirely
    (sync transport). Only `wake_up` and `search` ship sync variants at
    M0.18 (only these are called from Flask sync routes); `delete_palace`
    and `list_drawers` remain async-only until a sync caller surfaces.

  - **Adapter teardown.** `teardown()` method added for test isolation
    (reset_adapter pattern in `app/mempalace/__init__.py`) and graceful
    shutdown. Closes `shadow_sink` (which owns a daemon thread), then
    any sync/async resources the adapter holds references to. Idempotent
    per M0.3 convention.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from app.mempalace.feature_flag import MempalaceMode, mode
from app.mempalace.redaction.rules import scrub


@dataclass
class DeletionReceipt:
    """Return shape for ``MempalaceAdapter.delete_palace``.

    Mirrors the sidecar's DeletionReceipt response (see
    ``mempalace-sidecar/mempalace_sidecar/schemas.py:124``). Defined
    Flask-side so callers don't have to import sidecar schemas. Status
    narrowed to ``Literal["deleted"]`` to match sidecar contract — the
    sidecar only emits this one value; widening to ``str`` at the
    Flask boundary would drop type-safety across the UDS.
    """

    status: Literal["deleted"]
    bytes_removed: int


class MempalaceAdapter:
    """The only module the rest of the Flask app imports (spec §3).

    Stable public surface: ``feature_flag`` / ``client`` / ``outbox`` /
    ``shadow_sink`` / ``redaction`` live as implementation details;
    call-sites depend only on this class.
    """

    def __init__(
        self,
        *,
        client,
        outbox,
        shadow_sink,
        redaction_rules: list[dict],
        metrics=None,
        sync_client=None,
    ) -> None:
        self._client = client
        self._sync_client = sync_client
        self._outbox = outbox
        self._shadow = shadow_sink
        self._redact = redaction_rules
        self._metrics = metrics
        self._closed = False

    # --- body-construction helpers (shared by async + sync variants per D1) ---

    @staticmethod
    def _wake_up_body(query: str, session_id: str | None, k: int) -> dict:
        """Build the sidecar /wake-up request body.

        Shared between `wake_up` (async) and `wake_up_sync` (sync) per D1
        Option γ. session_id omitted when None (sidecar WakeupRequest.
        session_id is Optional[str] = None; cleaner to omit key than
        send null).
        """
        body: dict = {"query": query, "k": k}
        if session_id is not None:
            body["session_id"] = session_id
        return body

    @staticmethod
    def _search_body(query: str, wing: str | None, k: int) -> dict:
        """Build the sidecar /search request body.

        Shared between `search` (async) and `search_sync` (sync) per D1
        Option γ. wing passed through as Optional[str] matching sidecar
        SearchRequest.wing semantic.
        """
        return {"query": query, "wing": wing, "k": k}

    # --- reads (async variants — from any async caller) ---

    async def wake_up(
        self,
        user_id: str,
        query: str,
        session_id: str | None = None,
        k: int = 5,
    ):
        """Fetch wake-up context from the sidecar (async variant).

        In OFF mode returns None without calling the sidecar. In SHADOW
        and ACTIVE modes, POSTs to /palace/{user_id}/wake-up and returns
        the sidecar's response (dict with l0/l1/l2/l3 + meta). On any
        exception (sidecar down, circuit open, timeout, etc.) returns
        None so the caller can skip prompt injection cleanly.

        ``k`` parameterizes the wake-up layer size, matching sidecar
        ``WakeupRequest.k`` (schemas.py:108). Exposed at the adapter
        surface so callers can tune for context-hungry vs cost-
        constrained models; internally consistent with ``search()``
        which also exposes k.

        For sync callers (Flask sync workers), use ``wake_up_sync``
        which shares body construction via ``_wake_up_body`` but routes
        through SyncSidecarClient to avoid httpx.AsyncClient loop-
        binding issues (M0.14 doctrine).

        **SHADOW-mode caller contract.** Orchestrator MUST NOT inject
        the returned context in SHADOW mode (spec §4.2). Shadow
        observability of the wake-up result happens via a separate
        `log_shadow` call; the fetch itself is preserved to surface
        sidecar latency/health data during shadow rollout.
        """
        if mode() == MempalaceMode.OFF:
            return None
        body = self._wake_up_body(query, session_id, k)
        try:
            return await self._client.post_json(
                f"/palace/{user_id}/wake-up", body
            )
        except Exception:
            return None

    async def search(
        self,
        user_id: str,
        query: str,
        wing: str | None = None,
        k: int = 5,
    ):
        """Tool-call search over the user's palace (async variant).

        In OFF mode returns []. In SHADOW/ACTIVE, POSTs to
        /palace/{user_id}/search and returns the list-of-drawers
        (sidecar returns list[Drawer]). On any exception returns [].

        For sync callers, use ``search_sync`` which shares body
        construction via ``_search_body`` but routes through
        SyncSidecarClient.
        """
        if mode() == MempalaceMode.OFF:
            return []
        try:
            return await self._client.post_json(
                f"/palace/{user_id}/search",
                self._search_body(query, wing, k),
            )
        except Exception:
            return []

    # --- reads (sync variants — from Flask sync workers per D1) ---

    def wake_up_sync(
        self,
        user_id: str,
        query: str,
        session_id: str | None = None,
        k: int = 5,
    ):
        """Fetch wake-up context from the sidecar (sync variant).

        D1 sync bridge: Flask sync workers cannot ``await`` async methods,
        and ``asyncio.run(adapter.wake_up(...))`` would violate httpx.
        AsyncClient's construction-time loop binding (M0.14 doctrine).
        This variant routes through ``self._sync_client`` (a SyncSidecarClient
        instance) using shared body construction from ``_wake_up_body``.

        Semantic mirrors async ``wake_up``: OFF → None, SHADOW/ACTIVE →
        sidecar response, exception → None.

        **Invariant for future editors.** Any behavioral change to
        ``wake_up`` must be mirrored here (the body-construction helper
        is shared, but mode-gating and exception handling are not).
        Grep for both `wake_up` and `wake_up_sync` when editing either.
        """
        raise NotImplementedError("M0.18 Stage 3 GREEN lands wake_up_sync")

    def search_sync(
        self,
        user_id: str,
        query: str,
        wing: str | None = None,
        k: int = 5,
    ):
        """Tool-call search over the user's palace (sync variant).

        D1 sync bridge for Flask sync workers. Routes through
        ``self._sync_client`` using shared body construction from
        ``_search_body``.

        Semantic mirrors async ``search``: OFF → [], SHADOW/ACTIVE →
        list-of-drawers, exception → [].

        **Invariant for future editors.** Any behavioral change to
        ``search`` must be mirrored here. Grep for both when editing.
        """
        raise NotImplementedError("M0.18 Stage 3 GREEN lands search_sync")

    # --- writes ---

    def record_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str,
        pair_id: str,
        role: str,
        text: str,
        attachments: list[dict] | None = None,
        request_id: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Enqueue a turn to the outbox (active + shadow) or drop (off).

        OFF mode drops with ``inserts_dropped("mode_off")`` per spec §3
        line 137. Redaction runs before outbox insert (spec §4.1 line
        189). IntegrityError on duplicate turn_id is a caller bug (D2
        labeling); other exceptions are DB-down (generic handler).
        """
        if mode() == MempalaceMode.OFF:
            # D3: mode-off drop fires here, at the single funnel for
            # all record_turn paths. Orchestrator call-sites must not
            # be expected to remember this counter.
            if self._metrics is not None:
                self._metrics.inserts_dropped("mode_off")
            return
        if ts is None:
            ts = time.time()
        scrubbed = scrub(text, self._redact, metrics=self._metrics)
        try:
            self._outbox.insert(
                turn_id=turn_id,
                pair_id=pair_id,
                user_id=user_id,
                session_id=session_id,
                role=role,
                text=scrubbed,
                ts=ts,
                request_id=request_id,
            )
        except sqlite3.IntegrityError:
            # D2: duplicate-turn_id is a caller bug or intentional
            # replay. record_turn is NOT idempotent by contract (spec
            # §4.1 line 183 assigns idempotency authority to sidecar
            # turns.sqlite); drop-count with distinct label so ops
            # alerts on DB-health don't misfire on this condition.
            if self._metrics is not None:
                self._metrics.inserts_dropped("duplicate_turn_id")
        except Exception:
            # Outbox DB down (or any other unexpected exception): log-
            # only fallback per spec §4.5. Never propagate to caller.
            if self._metrics is not None:
                self._metrics.inserts_dropped("outbox_db_down")

    def log_shadow(self, record: dict) -> None:
        """Enqueue a shadow-mode observation. No-op in OFF/ACTIVE.

        Spec §3 line 138: shadow-mode results are logged via shadow_sink
        but not injected into prompts. Orchestrator calls log_shadow
        explicitly with the per-turn observation payload.
        """
        if mode() != MempalaceMode.SHADOW:
            return
        self._shadow.enqueue(record)

    # --- admin ---

    async def delete_palace(
        self, user_id: str, requested_by: str, reason: str
    ) -> DeletionReceipt:
        """Two-phase delete per spec §4.4.

        Phase 1 (Flask-side): abandon outstanding outbox rows for this
        user so the flusher doesn't resurrect a palace we're about to
        delete. If outbox abandon fails, proceed anyway — the user's
        intent is data-deletion, and a Flask-side outbox failure must
        not block the sidecar-side delete.

        Phase 2 (sidecar-side): real HTTP DELETE (plan review fix #6).
        Body carries requested_by + reason for audit.
        """
        try:
            self._outbox.abandon_user(user_id)
        except Exception:
            # Proceed to sidecar DELETE regardless. Flusher retry on
            # DB recovery against a deleted palace produces 404 —
            # acceptable because user-data-deletion already landed.
            pass
        resp = await self._client.delete_json(
            f"/palace/{user_id}",
            body={"requested_by": requested_by, "reason": reason},
        )
        return DeletionReceipt(
            status=resp["status"], bytes_removed=resp["bytes_removed"]
        )

    async def list_drawers(
        self,
        user_id: str,
        wing: str | None = None,
        room: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ):
        """GET-based paginated drawer listing.

        Plan review fix #5 (real GET, not POST). Offset-based
        pagination — ChromaDB has no cursor primitive (spec §5.2).

        ``limit`` default aligns to sidecar's ``Query(20, ge=1, le=200)``
        at routes/palace.py:371 (plan-predated-100 corrected at M0.17
        Stage 4c fold). Caller-visible behavior change — any site
        relying on the default expecting >20 results must pass ``limit=``
        explicitly. Adapter-side bounds mirror sidecar validation so
        out-of-range inputs fail at the Flask boundary rather than
        producing a confusing 422 from the sidecar.

        Both ``wing`` and ``room`` are optional filters matching sidecar
        ``Query`` parameters. Query string is built via
        ``urllib.parse.urlencode`` so special characters in wing/room
        are escaped correctly rather than corrupting the query.
        """
        if not (1 <= limit <= 200):
            raise ValueError(
                f"limit must be in [1, 200]; got {limit}. "
                "Sidecar contract: routes/palace.py:371 Query(ge=1, le=200)."
            )
        if offset < 0:
            raise ValueError(f"offset must be >= 0; got {offset}")
        params: dict = {"limit": limit, "offset": offset}
        if wing is not None:
            params["wing"] = wing
        if room is not None:
            params["room"] = room
        qs = urlencode(params)
        return await self._client.get_json(
            f"/palace/{user_id}/drawers?{qs}"
        )

    # --- lifecycle ---

    def teardown(self) -> None:
        """Release resources held by this adapter.

        Closes the shadow_sink (stops its daemon writer thread). Does NOT
        close the injected `client` / `sync_client` / `outbox` — those
        are caller-owned resources with their own lifecycle per prior-
        milestone conventions (M0.14 client.aclose, M0.15 outbox close).

        Idempotent per M0.3 convention (second call is a no-op).

        Primary use: `reset_adapter()` in `app/mempalace/__init__.py`
        for test isolation across adapter rebuilds. Secondary use:
        graceful shutdown if a shutdown hook is added later.
        """
        raise NotImplementedError("M0.18 Stage 3 GREEN lands teardown")


__all__ = ["MempalaceAdapter", "DeletionReceipt"]

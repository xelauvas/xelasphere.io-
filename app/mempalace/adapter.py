"""MempalaceAdapter — stable public surface (M0.17).

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4178-4292 (Task M0.17.4). Spec §3: this module is the only one
the rest of the Flask app imports; everything else in `app/mempalace/`
is an implementation detail.

**Deviations pinned at M0.17 pre-resolution:**

  - **D2.** `record_turn` catches `sqlite3.IntegrityError` before the
    generic `Exception` handler, labels `inserts_dropped
    ("duplicate_turn_id")`. Preserves the `"outbox_db_down"` label for
    actual DB failures so alert expressions on DB health don't misfire
    on orchestrator turn_id-generation bugs.

    **Idempotency pinned: `record_turn` is NOT idempotent by contract.**
    Spec §4.1 line 183 assigns idempotency authority to the sidecar-
    side `turns.sqlite`, not the Flask adapter. Duplicate-turn_id at
    adapter level is either a caller bug (orchestrator generated the
    same uuidv7 twice) or an intentional replay; counting as a drop
    is accurate in both cases.

  - **D3.** Mode-off metric fires at the adapter site (not "elsewhere").
    Spec §3 line 137 contract: OFF-mode `record_turn` increments
    `inserts_dropped{reason="mode_off"}`. Adapter is the single funnel
    for every `record_turn` path; orchestrator call-sites must not be
    expected to remember this counter.

  - **D4.** `wake_up` passes `session_id` through to the sidecar body
    so the sidecar's structured wake-up log at `routes/palace.py:243`
    can correlate turns. `WakeupRequest.session_id` is `Optional[str]`;
    the body omits the field when the caller didn't pass a value
    (cleaner than sending null).

  - **H5 (documentation-only).** `wake_up` returns the sidecar response
    unmodified in SHADOW mode. The orchestrator is responsible for not
    injecting the result into the system prompt in SHADOW mode (spec
    §4.2). The adapter does NOT suppress the fetch in SHADOW mode
    because shadow-mode observability of wake-up behavior is explicit
    via `log_shadow`; suppressing the fetch would hide real sidecar
    latency/health data during the shadow rollout phase.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from app.mempalace.feature_flag import MempalaceMode, mode
from app.mempalace.redaction.rules import scrub


@dataclass
class DeletionReceipt:
    """Return shape for ``MempalaceAdapter.delete_palace``.

    Mirrors the sidecar's DeletionReceipt response (see
    ``mempalace-sidecar/mempalace_sidecar/schemas.py:124``). Defined
    Flask-side so callers don't have to import sidecar schemas.
    """

    status: str
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
    ) -> None:
        self._client = client
        self._outbox = outbox
        self._shadow = shadow_sink
        self._redact = redaction_rules
        self._metrics = metrics

    # --- reads ---

    async def wake_up(
        self, user_id: str, query: str, session_id: str | None = None
    ):
        """Fetch wake-up context from the sidecar.

        In OFF mode returns None without calling the sidecar. In SHADOW
        and ACTIVE modes, POSTs to /palace/{user_id}/wake-up and returns
        the sidecar's response (dict with l0/l1/l2/l3 + meta). On any
        exception (sidecar down, circuit open, timeout, etc.) returns
        None so the caller can skip prompt injection cleanly.

        **SHADOW-mode caller contract.** Orchestrator MUST NOT inject
        the returned context in SHADOW mode (spec §4.2). Shadow
        observability of the wake-up result happens via a separate
        `log_shadow` call; the fetch itself is preserved to surface
        sidecar latency/health data during shadow rollout.
        """
        if mode() == MempalaceMode.OFF:
            return None
        body: dict = {"query": query, "k": 5}
        # D4: session_id flows through to sidecar body when present.
        # Sidecar's WakeupRequest.session_id is Optional[str] = None;
        # omitting the key is cleaner than sending null.
        if session_id is not None:
            body["session_id"] = session_id
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
        """Tool-call search over the user's palace.

        In OFF mode returns []. In SHADOW/ACTIVE, POSTs to
        /palace/{user_id}/search and returns the list-of-drawers
        (sidecar returns list[Drawer]). On any exception returns [].
        """
        if mode() == MempalaceMode.OFF:
            return []
        try:
            return await self._client.post_json(
                f"/palace/{user_id}/search",
                {"query": query, "wing": wing, "k": k},
            )
        except Exception:
            return []

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
        limit: int = 100,
        offset: int = 0,
    ):
        """GET-based paginated drawer listing.

        Plan review fix #5 (real GET, not POST). Offset-based
        pagination — ChromaDB has no cursor primitive (spec §5.2).
        Query string construction: wing omitted when None.
        """
        params: list[str] = []
        if wing is not None:
            params.append(f"wing={wing}")
        params.append(f"limit={limit}")
        params.append(f"offset={offset}")
        qs = "&".join(params)
        return await self._client.get_json(
            f"/palace/{user_id}/drawers?{qs}"
        )


__all__ = ["MempalaceAdapter", "DeletionReceipt"]

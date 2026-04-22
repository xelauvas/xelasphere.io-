"""M0.17 Stage 1 RED — MempalaceAdapter scaffold.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4178-4292 (Task M0.17.4). The stable public surface the rest of
the Flask app imports. Spec §3 names this module as the only one the
rest of the app depends on; everything else is an implementation detail.

Stage 1 RED shape: class exists; methods raise NotImplementedError so
tests fail predictably. Stage 3 GREEN lands real behavior with the
deviations pinned at M0.17 pre-resolution:

  - **D2.** `record_turn` catches `sqlite3.IntegrityError` before
    generic `Exception`, labels `inserts_dropped("duplicate_turn_id")`.
    Preserves `"outbox_db_down"` label for actual DB failures.
    Idempotency pinned: `record_turn` is NOT idempotent by contract;
    spec §4.1 assigns idempotency authority to the sidecar-side
    turns.sqlite, not the Flask adapter. Duplicate-turn_id at adapter
    level is a caller bug or intentional replay; drop-count is accurate
    in both cases.

  - **D3.** Mode-off metric fires at adapter-site, not "elsewhere."
    Spec §3 line 137 contract: new record_turn calls in OFF mode
    increment inserts_dropped{reason="mode_off"}. Adapter is the single
    funnel for every record_turn path; orchestrator call-sites must
    not be expected to remember this counter.

  - **D4.** wake_up passes session_id through to the sidecar body.
    Sidecar logs session_id in structured wake-up log (routes/palace.py:243)
    for correlation. Observability win for multi-session debugging.
    WakeupRequest.session_id is Optional; sidecar accepts None.

  - **H5 (documentation-only).** wake_up returns sidecar response
    unmodified in SHADOW mode. Orchestrator is responsible for not
    injecting in SHADOW mode (per spec §4.2 orchestrator flow). The
    adapter's wake_up docstring names this caller-responsibility
    explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeletionReceipt:
    """Return shape for `MempalaceAdapter.delete_palace`.

    Mirrors the sidecar's `DeletionReceipt` response (see
    `mempalace-sidecar/mempalace_sidecar/schemas.py:124`). Defined
    Flask-side so callers don't have to import sidecar schemas.
    """

    status: str
    bytes_removed: int


class MempalaceAdapter:
    """The only module the rest of the Flask app imports (spec §3).

    Stable public surface per spec §3 line 82: feature_flag / client /
    outbox / shadow_sink / redaction live as implementation details;
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
        raise NotImplementedError("M0.17 Stage 3 GREEN lands MempalaceAdapter.__init__")

    async def wake_up(
        self, user_id: str, query: str, session_id: str | None = None
    ):
        """Fetch wake-up context from the sidecar.

        **Caller contract in SHADOW mode (H5).** Returns the sidecar's
        wake-up response unmodified in every non-OFF mode. In SHADOW
        mode, the orchestrator MUST NOT inject the result into the
        system prompt (spec §4.2). The adapter does not suppress the
        fetch in SHADOW mode because shadow-logging of wake-up results
        is an orchestrator responsibility via `log_shadow`.
        """
        raise NotImplementedError("M0.17 Stage 3 GREEN lands wake_up")

    async def search(
        self, user_id: str, query: str, wing: str | None = None, k: int = 5
    ):
        raise NotImplementedError("M0.17 Stage 3 GREEN lands search")

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
        raise NotImplementedError("M0.17 Stage 3 GREEN lands record_turn")

    def log_shadow(self, record: dict) -> None:
        raise NotImplementedError("M0.17 Stage 3 GREEN lands log_shadow")

    async def delete_palace(
        self, user_id: str, requested_by: str, reason: str
    ) -> DeletionReceipt:
        raise NotImplementedError("M0.17 Stage 3 GREEN lands delete_palace")

    async def list_drawers(
        self,
        user_id: str,
        wing: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        raise NotImplementedError("M0.17 Stage 3 GREEN lands list_drawers")


__all__ = ["MempalaceAdapter", "DeletionReceipt"]

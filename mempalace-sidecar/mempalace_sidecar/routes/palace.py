"""Palace-scoped HTTP routes — POST /palace/{user_id}/ingest (M0.4.2).

Idempotency comes from ``PalaceHandle.turns.sqlite`` (spec §4.1, §5.3) —
NOT from the ``Idempotency-Key`` HTTP header. The header is advisory on
the wire: logged for request correlation (operator grep across the
Flask outbox → sidecar ingest boundary) and optionally asserted against
``body.turn_id`` for client-programming-error detection. No caching by
header, no branching on header for dedup decisions. Two competing
idempotency mechanisms fight silently when they disagree; the design
pass (see deltas 2026-04-20 "M0.3 library isolation") settled the
one-source-of-truth rule.

Concurrency invariant: the ROUTE acquires the per-user writer_lock
before calling ``handle.add_drawer``, and releases it after. The handle
itself does NOT acquire the lock — see the class docstring in
``palace_handle.py`` for why that's load-bearing for every future
PalaceHandle caller.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from mempalace_sidecar.schemas import IngestRequest, IngestResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/palace", tags=["palace"])


def _registry(request: Request):
    """Pull the PalaceRegistry off app.state.

    Wired by ``main._lifespan`` (spec §5.4). Surfacing this via a tiny
    helper keeps the route body focused on the ingest flow and makes
    the registry dependency visible to future middleware that wants to
    introspect it (e.g. M0.10 shutdown drain checks resident palaces
    via this attribute).
    """
    return request.app.state.registry


@router.post("/{user_id}/ingest", response_model=IngestResponse)
async def ingest(
    user_id: str, body: IngestRequest, request: Request
) -> IngestResponse:
    """Accept a single conversation turn and file it as a drawer.

    Flow (spec §4.1 amended write-path ordering):

      1. Optional ``Idempotency-Key`` header parsing. If present AND
         differs from ``body.turn_id``, return 400 — the header is a
         contract-assertion channel for catching outbox bugs, not a
         dedup mechanism. Either way, we stash it in the structured
         log for correlation with Flask's outbox.

      2. ``registry.open(user_id)`` — opens (or fetches cached) the
         per-user palace. May trigger eviction of a different user;
         that's fine, it runs under its own lock.

      3. ``async with lock.writer_lock:`` — serialises this write
         against any other writer for the same user_id (spec §5.3
         invariant: "caller holds the per-user write lock for mutating
         methods"). Parallel readers on the same user still proceed
         under the reader_lock.

      4. ``handle.add_drawer(...)`` — wrapped in ``asyncio.to_thread``
         to keep the event loop responsive while the synchronous
         SELECT + library call + INSERT executes. The call is usually
         fast (local sqlite + Chroma upsert), but "usually fast" isn't
         a concurrency story — blocking the loop on every ingest would
         stall every other route.

      5. Return ``IngestResponse(status=result.status)`` — ``accepted``
         means a fresh row landed in turns.sqlite; ``duplicate`` means
         the turn_id was already present. No 3rd status exists.
    """
    registry = _registry(request)

    # Idempotency-Key header — advisory only (plan M0.4 critical rule 1).
    # We log it either way; we gate 400 only on mismatch.
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is not None and idempotency_key != body.turn_id:
        log.warning(
            "Idempotency-Key header disagrees with body.turn_id",
            extra={
                "idempotency_key": idempotency_key,
                "turn_id": body.turn_id,
                "user_id": user_id,
            },
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Idempotency-Key header does not match body.turn_id; "
                "they must be identical when the header is present"
            ),
        )
    # Structured log on every ingest — the header value (or None) goes
    # into the request context so an operator correlating a retried
    # turn across Flask-outbox and sidecar-ingest can match them up.
    log.info(
        "ingest",
        extra={
            "user_id": user_id,
            "turn_id": body.turn_id,
            "session_id": body.session_id,
            "idempotency_key": idempotency_key,
        },
    )

    entry = await registry.open(user_id)
    lock = registry._lock_for(user_id)
    async with lock.writer_lock:
        # handle.add_drawer is synchronous (spec §5.3 "Concurrency
        # model"). Wrap in to_thread so the event loop stays responsive
        # for other users while this user's write executes. See
        # deviations note — plan pseudo-code called it directly; the
        # stronger contract is to always to_thread synchronous library
        # calls that can touch disk.
        result = await asyncio.to_thread(
            entry.handle.add_drawer,
            turn_id=body.turn_id,
            session_id=body.session_id,
            pair_id=body.pair_id,
            role=body.role,
            text=body.text,
            ts=body.ts,
        )
    return IngestResponse(status=result.status)


__all__ = ["router"]

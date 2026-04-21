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
import time

from fastapi import APIRouter, HTTPException, Query, Request

from mempalace_sidecar import metrics
from mempalace_sidecar.routes._common import _hash_uid, _registry
from mempalace_sidecar.schemas import (
    Drawer,
    DrawersPage,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    WakeupContext,
    WakeupMeta,
    WakeupRequest,
)
from mempalace_sidecar.wakeup import build_wakeup, to_drawer

log = logging.getLogger(__name__)

router = APIRouter(prefix="/palace", tags=["palace"])


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
    settings = request.app.state.settings
    uid_hash = _hash_uid(user_id, settings.user_id_hash_salt)

    # Idempotency-Key header — advisory only (plan M0.4 critical rule 1).
    # We log it either way; we gate 400 only on mismatch.
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is not None and idempotency_key != body.turn_id:
        log.warning(
            "Idempotency-Key header disagrees with body.turn_id",
            extra={
                "idempotency_key": idempotency_key,
                "turn_id": body.turn_id,
                "user_id": uid_hash,
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
    # user_id is hashed per spec §8.4 pseudonymous-logging contract.
    log.info(
        "ingest",
        extra={
            "user_id": uid_hash,
            "turn_id": body.turn_id,
            "session_id": body.session_id,
            "idempotency_key": idempotency_key,
        },
    )

    # registry.open has its own _registry_lock + two-phase eviction
    # discipline; no need to take the per-user writer_lock around it.
    # The writer_lock only gates the write step (add_drawer) itself.
    #
    # Metrics discipline (spec §8.1): every exit path from this
    # handler bumps ``ingest_total`` with the right status label
    # exactly once. Success → ``accepted`` | ``duplicate``. Exception
    # from add_drawer → ``error``, counter bumped BEFORE the raise
    # reaches the client (``test_ingest_increments_error_on_raise``
    # asserts the ordering).
    try:
        entry = await registry.open(user_id)
    except RuntimeError as exc:
        if "shutting down" in str(exc).lower():
            raise HTTPException(status_code=503, detail="sidecar shutting down") from exc
        raise  # surface as 500; other RuntimeErrors are unexpected
    lock = registry.lock_for(user_id)
    try:
        async with lock.writer_lock:
            # handle.add_drawer is synchronous (spec §5.3 "Concurrency
            # model"). Wrap in to_thread so the event loop stays
            # responsive for other users while this user's write
            # executes.
            result = await asyncio.to_thread(
                entry.handle.add_drawer,
                turn_id=body.turn_id,
                session_id=body.session_id,
                pair_id=body.pair_id,
                role=body.role,
                text=body.text,
                ts=body.ts,
            )
    except Exception as exc:
        metrics.ingest_total.labels(status="error").inc()
        log.exception(
            "ingest_failed",
            extra={"user_id": uid_hash, "turn_id": body.turn_id},
        )
        # Convert to HTTPException so the 500 flows through FastAPI's
        # ExceptionMiddleware cleanly. A bare ``raise`` would propagate
        # through BaseHTTPMiddleware (auth / in-flight / logging) and
        # bypass ServerErrorMiddleware's 500-conversion — a known
        # Starlette quirk. The client needs to see 500 JSON, not a
        # connection error.
        raise HTTPException(
            status_code=500, detail="ingest failed"
        ) from exc

    metrics.ingest_total.labels(status=result.status).inc()

    # Fire-and-forget KG enqueue (plan M0.7). Runs OUTSIDE the
    # writer_lock — the enqueue is a single sqlite INSERT to a
    # separate sidecar-level DB, not a per-palace write, and it
    # must never block the ingest write path. Shape is
    # ``await queue.enqueue(...)`` (not ``asyncio.create_task``)
    # per dispatch enqueue-shape note: the operation is fast
    # enough to await inline, and catching its exceptions here is
    # cleaner than orphaning a task (which would require a
    # done-callback to log failures and would make shutdown noisy).
    #
    # Any exception (including the silent IntegrityError path in
    # enqueue) MUST NOT fail ingest — that contract is why the
    # wrapper swallows everything. If the queue is unhealthy, ingest
    # still succeeds and the KG work is simply skipped for this
    # turn (degraded mode, not broken mode).
    kg_queue = getattr(request.app.state, "kg_queue", None)
    if kg_queue is not None:
        try:
            await kg_queue.enqueue(user_id, [result.drawer_id])
        except Exception:
            log.exception(
                "kg_enqueue_failed",
                extra={"user_id": uid_hash, "turn_id": body.turn_id},
            )

    return IngestResponse(status=result.status)


@router.post("/{user_id}/wake-up", response_model=WakeupContext)
async def wake_up(
    user_id: str,
    body: WakeupRequest,
    request: Request,
    warmup: bool = False,
) -> WakeupContext:
    """Assemble the four-layer wake-up context (L0/L1/L2/L3) for a
    user turn. HOT READ path — every turn hits this before the LLM.

    Flow (spec §4.2 read path A):

      1. ``registry.open(user_id, track_cold=True)`` — returns the
         cached ``PalaceEntry`` or opens a fresh palace if cold. The
         ``was_cold`` flag propagates into ``WakeupMeta.hot`` so the
         caller (Flask adapter, M0.17) can render latency expectations.

      2. If ``?warmup=true``, return an empty ``WakeupContext`` —
         the purpose of the request was to move the palace into the
         cache, NOT to run the composition. Useful for pre-turn
         background warmup from the Flask adapter.

      3. Otherwise acquire ``registry.lock_for(user_id).reader_lock``
         (NOT writer_lock — wake-up is a READ) and call
         ``build_wakeup``. Parallel wake-ups for the same user run
         concurrently under the reader_lock; concurrent writers
         (ingest) serialise against readers via the writer_lock.

      4. Any ``RuntimeError`` containing "shutting down" from the
         registry maps to HTTP 503 (mirrors the ingest pattern).

    user_id is logged pseudonymously per spec §8.4 (sha256+salt).
    """
    registry = _registry(request)
    settings = request.app.state.settings
    uid_hash = _hash_uid(user_id, settings.user_id_hash_salt)

    log.info(
        "wake_up",
        extra={
            "user_id": uid_hash,
            "session_id": body.session_id,
            "query_len": len(body.query),
            "warmup_only": warmup,
        },
    )

    try:
        entry, was_cold = await registry.open(user_id, track_cold=True)
    except RuntimeError as exc:
        if "shutting down" in str(exc).lower():
            raise HTTPException(
                status_code=503, detail="sidecar shutting down"
            ) from exc
        raise  # other RuntimeErrors surface as 500

    # Hit / miss counters (spec §8.1). Bumped here — not inside the
    # registry — because the registry's own open API is used
    # elsewhere (KG worker, shutdown drain) where route-level
    # cache-hit semantics don't apply.
    if was_cold:
        metrics.palace_cache_misses_total.inc()
    else:
        metrics.palace_cache_hits_total.inc()
    hot_label = "0" if was_cold else "1"

    if warmup:
        # Connection-warmup short-circuit. Return an empty context
        # with meta.hot reflecting whether THIS open was the cold
        # one. No composition work done.
        return WakeupContext(
            user_id=user_id,
            meta=WakeupMeta(hot=(not was_cold), embed_ms=0.0, total_ms=0.0),
        )

    lock = registry.lock_for(user_id)
    # READ path — reader_lock. Parallel wake-ups for the same user
    # MUST run concurrently (that's the whole point of the RWLock).
    # If this ever becomes ``writer_lock``, the concurrency story
    # for back-to-back turns is destroyed.
    t0 = time.monotonic()
    async with lock.reader_lock:
        ctx = await build_wakeup(
            user_id=user_id,
            query=body.query,
            handle=entry.handle,
            session_id=body.session_id,
            hot=(not was_cold),
            k=body.k,
        )
    metrics.wakeup_seconds.labels(layer="total", hot=hot_label).observe(
        time.monotonic() - t0
    )
    return ctx


@router.post("/{user_id}/search", response_model=list[Drawer])
async def search(
    user_id: str, body: SearchRequest, request: Request
) -> list[Drawer]:
    """Tool-call search over a user's palace (plan M0.6.1).

    Thin bridge over ``PalaceHandle.search`` — library-owned hybrid
    BM25 + vector + closet-boost retrieval with optional wing filter.
    Read path, reader_lock (NOT writer_lock). Synchronous library
    call goes through ``asyncio.to_thread`` so the event loop stays
    responsive for parallel readers under the same user and for
    every other user on the worker.

    503 on ``registry.open`` raising "shutting down" — mirrors the
    ingest/wake-up pattern so load-balancers classify correctly.
    """
    registry = _registry(request)
    settings = request.app.state.settings
    uid_hash = _hash_uid(user_id, settings.user_id_hash_salt)

    log.info(
        "search",
        extra={
            "user_id": uid_hash,
            "query_len": len(body.query),
            "wing": body.wing,
            "k": body.k,
        },
    )

    try:
        entry, was_cold = await registry.open(user_id, track_cold=True)
    except RuntimeError as exc:
        if "shutting down" in str(exc).lower():
            raise HTTPException(
                status_code=503, detail="sidecar shutting down"
            ) from exc
        raise

    hot_label = "0" if was_cold else "1"

    lock = registry.lock_for(user_id)
    t0 = time.monotonic()
    async with lock.reader_lock:
        raws = await asyncio.to_thread(
            entry.handle.search, body.query, wing=body.wing, k=body.k
        )
    metrics.search_seconds.labels(hot=hot_label).observe(
        time.monotonic() - t0
    )
    # PalaceHandle.search always returns a list (see palace_handle.py:458,
    # `result.get("results", [])`). No coalesce needed; trust the handle
    # contract. If that contract ever widens to allow None, the test
    # suite's empty-palace case will catch it first.
    return [to_drawer(r) for r in raws]


@router.get("/{user_id}/drawers", response_model=DrawersPage)
async def list_drawers(
    user_id: str,
    request: Request,
    wing: str | None = Query(None),
    room: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> DrawersPage:
    """Paginated GDPR-admin listing (plan M0.6.2).

    Thin bridge over ``PalaceHandle.list_drawers``. Offset pagination
    (ChromaDB's ``col.get`` has no cursor concept; see deltas
    "2026-04-20 M0.3 library isolation", cursor → offset bullet).
    ``total`` is ``None`` when wing/room filter applies (honest-totals
    contract). ``next_offset`` is ``None`` at end-of-pagination.

    Read path, same reader_lock / asyncio.to_thread / 503 discipline
    as /search above.

    ``limit`` default aligns to ``PalaceHandle.list_drawers``'s own
    default (20); plan sketch's 100 predates the handle. Route bounds
    ``ge=1, le=200`` prevent a million-item request.
    """
    registry = _registry(request)
    settings = request.app.state.settings
    uid_hash = _hash_uid(user_id, settings.user_id_hash_salt)

    log.info(
        "list_drawers",
        extra={
            "user_id": uid_hash,
            "wing": wing,
            "room": room,
            "limit": limit,
            "offset": offset,
        },
    )

    try:
        entry = await registry.open(user_id)
    except RuntimeError as exc:
        if "shutting down" in str(exc).lower():
            raise HTTPException(
                status_code=503, detail="sidecar shutting down"
            ) from exc
        raise

    lock = registry.lock_for(user_id)
    async with lock.reader_lock:
        result = await asyncio.to_thread(
            entry.handle.list_drawers,
            wing=wing,
            room=room,
            limit=limit,
            offset=offset,
        )
    return DrawersPage(
        drawers=[to_drawer(r) for r in result["drawers"]],
        total=result["total"],
        next_offset=result["next_offset"],
    )


__all__ = ["router"]

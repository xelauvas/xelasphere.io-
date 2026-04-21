"""Palace lifecycle routes — DELETE + rebuild (plan M0.8, spec §5.2).

Kept separate from ``routes/palace.py`` (ingest / wake-up / search /
drawers) because the concerns are genuinely distinct: lifecycle
routes mutate the palace's existence (create-from-nothing,
destroy-to-nothing), while palace routes read or write drawers
within an existing palace. Spec §5.1 prescribes the split; M0.8
lands it.

**No cross-route-file imports.** Shared helpers (``_registry``,
``_hash_uid``) live in ``routes/_common.py``; both this file and
``routes/palace.py`` import from there. A future caller proposing
"just import from palace.py into lifecycle.py for this one thing"
is rejected — that recreates the coupling the split eliminated.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from prometheus_client import Counter

from mempalace_sidecar.registry import _dir_size_bytes
from mempalace_sidecar.routes._common import _hash_uid, _registry
from mempalace_sidecar.schemas import (
    DeleteRequest,
    DeletionReceipt,
    RebuildRequest,
    RebuildResponse,
)

if TYPE_CHECKING:
    from mempalace_sidecar.audit import AuditLog


log = logging.getLogger(__name__)


# Spec §8.1 metric catalog — one counter for successful deletions.
# Module-level default-registry Counter for M0.8; M0.11b's metrics
# module will consolidate all counters under a single home with
# multiprocess-safe wiring.
_DELETE_TOTAL = Counter(
    "mempalace_delete_total",
    "Total number of successful palace deletions.",
)


router = APIRouter(prefix="/palace", tags=["palace-lifecycle"])


# Spec §4.4: DELETE is bounded at 30s wall clock. Enforced via
# asyncio.wait_for below; the nightly reaper (later milestone)
# finishes any rmtree that times out mid-flight. Rebuild gets its
# hold-time bound from the 100-conversation input cap (deviation #7).
_DELETE_TIMEOUT_SEC = 30.0
_REBUILD_MAX_CONVERSATIONS = 100


@router.delete("/{user_id}", response_model=DeletionReceipt)
async def delete_palace(
    user_id: str,
    body: DeleteRequest,
    request: Request,
) -> DeletionReceipt:
    """Two-phase destructive deletion of a user's palace.

    Flow (spec §4.4 + plan M0.8.1):

      1. Check palace dir exists; 404 if not (already-deleted case
         must not silently recreate + delete-empty, which would
         muddy the audit trail).
      2. ``registry.open(user_id)`` to get the entry.
      3. Acquire the user's ``writer_lock``.
      4. Compute ``before_bytes`` from the palace dir.
      5. ``entry.handle.close()`` — pops the library backend cache
         per spec §5.3 contract.
      6. ``registry.evict(user_id)`` — removes from registry cache.
      7. ``os.replace`` to ``<dir>.pending_delete.<ts>`` — atomic;
         the palace is gone from the caller's perspective after this.
      8. ``asyncio.shield(audit.record_delete(...))`` — audit row
         lands even if the outer timeout fires during this step.
         rmtree is NOT shielded; a timeout mid-rmtree leaves the
         .pending_delete dir for the reaper to finish (spec §4.4).
      9. ``shutil.rmtree`` via ``asyncio.to_thread`` — fast for
         typical palaces; bounded by the 30s ``wait_for``.
      10. Return ``DeletionReceipt`` and increment
          ``mempalace_delete_total``.

    Cancellation behavior: the route is wrapped in
    ``asyncio.wait_for(_, timeout=30.0)``. On timeout, CancelledError
    propagates into whichever step was running, the writer_lock
    releases, and we return HTTP 504. We do NOT catch CancelledError
    inside the inner flow — let it propagate (FastAPI's default
    handling surfaces it as 504 via wait_for's TimeoutError).
    """
    registry = _registry(request)
    settings = request.app.state.settings
    audit: "AuditLog" = request.app.state.audit
    uid_hash = _hash_uid(user_id, settings.user_id_hash_salt)

    # Existence check runs OUTSIDE the wait_for — cheap stat, and
    # the 404 error path is immediate.
    palace_path = settings.data_dir / "palaces" / user_id
    if not palace_path.exists():
        log.info(
            "delete_palace_404",
            extra={"user_id": uid_hash, "requested_by": body.requested_by},
        )
        raise HTTPException(
            status_code=404,
            detail=f"palace for user not found (may already be deleted)",
        )

    log.info(
        "delete_palace",
        extra={
            "user_id": uid_hash,
            "requested_by": body.requested_by,
            "reason_len": len(body.reason),
        },
    )

    async def _do_delete() -> DeletionReceipt:
        try:
            entry = await registry.open(user_id)
        except RuntimeError as exc:
            if "shutting down" in str(exc).lower():
                raise HTTPException(
                    status_code=503, detail="sidecar shutting down"
                ) from exc
            raise

        lock = registry.lock_for(user_id)
        async with lock.writer_lock:
            # Phase A: snapshot + close.
            before_bytes = await asyncio.to_thread(
                _dir_size_bytes, entry.path
            )
            entry.handle.close()
            await registry.evict(user_id)

            # Phase B: atomic rename + audit (shielded) + rmtree.
            ts = int(time.time())
            pending_path = entry.path.with_name(
                f"{entry.path.name}.pending_delete.{ts}"
            )
            os.replace(entry.path, pending_path)

            # Shielded — audit MUST land even if the caller cancels
            # the wait_for or the 30s timeout fires during the rmtree.
            # Without shielding, a timeout that landed inside the
            # INSERT could leave the palace renamed-but-unaudited,
            # defeating the whole point of the pre-rmtree audit row.
            await asyncio.shield(
                audit.record_delete(
                    user_id=user_id,
                    requested_by=body.requested_by,
                    reason=body.reason,
                    before_bytes=before_bytes,
                )
            )

            # NOT shielded. A timeout here leaves <dir>.pending_delete.<ts>
            # for the reaper. The caller sees 504, retries see 404
            # (palace_path no longer exists), reaper eventually rmtrees.
            await asyncio.to_thread(shutil.rmtree, pending_path)

        return DeletionReceipt(status="deleted", bytes_removed=before_bytes)

    try:
        receipt = await asyncio.wait_for(
            _do_delete(), timeout=_DELETE_TIMEOUT_SEC
        )
    except asyncio.TimeoutError as exc:
        log.warning(
            "delete_palace_timeout",
            extra={"user_id": uid_hash, "timeout_sec": _DELETE_TIMEOUT_SEC},
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"delete exceeded {_DELETE_TIMEOUT_SEC}s budget; "
                f"nightly reaper will finish cleanup"
            ),
        ) from exc

    _DELETE_TOTAL.inc()
    return receipt


@router.post("/{user_id}/rebuild", response_model=RebuildResponse)
async def rebuild_palace(
    user_id: str,
    body: RebuildRequest,
    request: Request,
) -> RebuildResponse:
    """Rebuild a user's palace from a list of conversations (plan
    M0.8.2).

    Sync-POST flow: iterate ``body.conversations``, call
    ``handle.add_drawer`` for each under writer_lock. Idempotent by
    ``turn_id`` per M0.4 contract — re-runs of the same conversation
    list report all-skipped on the second call.

    Hold-time bound: writer_lock is held for the full loop
    (~100ms/drawer at ingest rates). The 100-conversation cap
    (deviation #7) keeps worst-case hold under ~10s, below Flask's
    2s client-side read budget with margin. Larger rebuilds require
    streaming + ``from_path`` (deferred to a later milestone); spec
    §5.2 "long-running, streaming" language is what that milestone
    will satisfy. See deltas for the streaming-deferral entry.
    """
    if len(body.conversations) > _REBUILD_MAX_CONVERSATIONS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"max {_REBUILD_MAX_CONVERSATIONS} conversations per "
                f"request; got {len(body.conversations)}. Larger "
                f"rebuilds require streaming + from_path (deferred)."
            ),
        )

    registry = _registry(request)
    settings = request.app.state.settings
    uid_hash = _hash_uid(user_id, settings.user_id_hash_salt)
    job_id = str(uuid.uuid4())

    log.info(
        "rebuild_palace",
        extra={
            "user_id": uid_hash,
            "count": len(body.conversations),
            "job_id": job_id,
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
    ingested = 0
    skipped = 0
    async with lock.writer_lock:
        for conv in body.conversations:
            # handle.add_drawer is sync — wrap in to_thread to keep
            # the event loop responsive. Same pattern as the M0.4
            # ingest route.
            result = await asyncio.to_thread(
                entry.handle.add_drawer,
                turn_id=conv.turn_id,
                session_id=conv.session_id,
                pair_id=conv.pair_id,
                role=conv.role,
                text=conv.text,
                ts=conv.ts,
            )
            if result.status == "accepted":
                ingested += 1
            elif result.status == "duplicate":
                skipped += 1

    return RebuildResponse(ingested=ingested, skipped=skipped, job_id=job_id)


__all__ = ["router"]

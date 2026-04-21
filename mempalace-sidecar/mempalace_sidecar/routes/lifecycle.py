"""Palace lifecycle routes — DELETE + rebuild (plan M0.8, spec §5.2).

Kept separate from ``routes/palace.py`` (ingest / wake-up / search /
drawers) because the concerns are genuinely distinct: lifecycle
routes mutate the palace's existence (create-from-nothing,
destroy-to-nothing), while palace routes read or write drawers
within an existing palace. Spec §5.1 prescribes the split; M0.8 is
where it lands.

**No cross-route-file imports.** Shared helpers (``_registry``,
``_hash_uid``) live in ``routes/_common.py``; both this file and
``routes/palace.py`` import from there. A future caller proposing
"just import from palace.py into lifecycle.py for this one thing"
is rejected — that recreates the coupling the split eliminated.

**Stage 2 state (M0.8 RED commit):** both handlers are stubs
returning HTTP 501. The router is wired into ``main._lifespan`` so
the app starts cleanly and the tests fail with the
functionality-missing shape (stub returns 501, test expects 200)
rather than with a 404 "route not found" or an import error. Stage
3 replaces the stubs with real implementations.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request


log = logging.getLogger(__name__)


router = APIRouter(prefix="/palace", tags=["palace-lifecycle"])


# M0.8 STUB — replaced in Stage 3 with the real two-phase delete flow.
@router.delete("/{user_id}")
async def delete_palace(user_id: str, request: Request):
    raise HTTPException(
        status_code=501,
        detail="M0.8 stub — real implementation lands in Stage 3 GREEN",
    )


# M0.8 STUB — replaced in Stage 3 with the real rebuild loop.
@router.post("/{user_id}/rebuild")
async def rebuild_palace(user_id: str, request: Request):
    raise HTTPException(
        status_code=501,
        detail="M0.8 stub — real implementation lands in Stage 3 GREEN",
    )


__all__ = ["router"]

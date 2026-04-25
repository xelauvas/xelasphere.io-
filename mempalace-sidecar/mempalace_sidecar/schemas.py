"""Request/response schemas and sidecar-internal dataclasses.

This module collects:

* Pydantic models for the sidecar's HTTP surface (per plan M0.4.1) —
  ``IngestRequest``, ``IngestResponse``, ``Drawer``, ``WakeupMeta``,
  ``WakeupContext``, ``WakeupRequest``, ``SearchRequest``,
  ``DeleteRequest``, ``DeletionReceipt``.

* Sidecar-internal dataclasses returned by ``PalaceHandle`` (per plan
  M0.3.0). ``AddDrawerResult`` rides here rather than on the HTTP surface
  because it is returned from a library-boundary method to the route
  layer, which then maps it to ``IngestResponse``.

Added in the order the plan introduces them:

* M0.3.0 (this task): ``AddDrawerResult``.
* M0.4.1: the Pydantic HTTP models listed above.

M0.4.1 is seeded here alongside ``AddDrawerResult`` because the
M0.3.0 dispatch prompt explicitly requires schemas.py to exist at the
close of this milestone. M0.4.1 will consume the models without having
to recreate the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field


Role = Literal["user", "assistant"]


# -----------------------------------------------------------------------------
# Sidecar-internal dataclasses (PalaceHandle → route layer)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AddDrawerResult:
    """Return value of :meth:`PalaceHandle.add_drawer`.

    ``status`` is the resolved idempotency outcome. ``"accepted"`` means a
    new ``turns.sqlite`` row was inserted and the library was asked to file
    the drawer. ``"duplicate"`` means the ``turn_id`` was already present in
    ``turns.sqlite`` and the library was never called.

    ``drawer_id`` is the deterministic ID the library would (and did, for
    ``accepted``) compute via ``sha256(source_file + str(chunk_index))[:24]``.
    For ``duplicate`` we return the drawer_id stored in ``turns.sqlite`` at
    original ingest time — same value by construction.
    """

    status: Literal["accepted", "duplicate"]
    drawer_id: str


# -----------------------------------------------------------------------------
# HTTP request/response Pydantic models (seeded here for M0.4.1 onward)
# -----------------------------------------------------------------------------


class IngestRequest(BaseModel):
    turn_id: str
    session_id: str
    pair_id: str
    role: Role
    text: str
    ts: float
    attachments: Optional[list[dict]] = None


class IngestResponse(BaseModel):
    status: Literal["accepted", "duplicate"]


class Drawer(BaseModel):
    text: str
    wing: Optional[str] = None
    room: Optional[str] = None
    ts: float
    pair_id: Optional[str] = None
    score: Optional[float] = None


class WakeupMeta(BaseModel):
    hot: bool
    embed_ms: float
    total_ms: float
    warnings: list[str] = Field(default_factory=list)


class WakeupContext(BaseModel):
    user_id: str
    schema_version: int = 1
    l0_recent: list[Drawer] = Field(default_factory=list)
    l1_session: list[Drawer] = Field(default_factory=list)
    l2_topic: list[Drawer] = Field(default_factory=list)
    l3_semantic: list[Drawer] = Field(default_factory=list)
    meta: WakeupMeta


class WakeupRequest(BaseModel):
    query: str = ""
    k: int = 5
    layers: Optional[list[str]] = None
    session_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    wing: Optional[str] = None
    k: int = 5


class DeleteRequest(BaseModel):
    requested_by: str
    reason: str


class DeletionReceipt(BaseModel):
    status: Literal["deleted"]
    bytes_removed: int


class RebuildRequest(BaseModel):
    """Request body for ``POST /palace/{user_id}/rebuild`` (M0.8).

    ``conversations`` is a list of ingest-shaped turns. Each is the
    exact same wire shape as a normal ``/ingest`` request (reusing
    ``IngestRequest``) so the rebuild path and the normal ingest path
    share validation and the handle's ``add_drawer`` call signature.

    **Cap: 100 items per request** — spec §5.2 calls the route
    "long-running, streaming" but M0.8 ships sync-POST (see deltas
    2026-04-21 M0.8 rebuild-streaming deviation). A sync rebuild
    holds the per-user writer_lock for the full duration; at ~100ms
    per drawer, 100 conversations ~= 10s worst-case lock hold, which
    is the ceiling that keeps parallel reads from timing out at
    Flask's 2s client budget. The 501st item is rejected with HTTP
    413; larger rebuilds land when streaming + ``from_path`` arrive
    in a later milestone.
    """

    conversations: list["IngestRequest"]


class RebuildResponse(BaseModel):
    """Response for ``POST /palace/{user_id}/rebuild`` (M0.8).

    ``ingested`` counts ``AddDrawerResult.status == 'accepted'``;
    ``skipped`` counts ``'duplicate'``. Together they sum to
    ``len(request.conversations)``.

    **No ``job_id`` in M0.8.** The earlier draft had a UUID4
    ``job_id`` field "forward-compatible with streaming." M0.8
    quality review flagged it as dead weight with a silent-drift
    risk: a caller seeing a UUID in the response reasonably assumes
    polling semantics (standard job-queue idiom), and when streaming
    lands and ``job_id`` becomes load-bearing, callers that already
    started relying on it in M0.8 hit a silent contract change.
    Dropped for M0.8; reintroduced when streaming ships and its
    semantics are actually used.
    """

    ingested: int
    skipped: int


class DrawersPage(BaseModel):
    """Paginated response for GET /palace/{user_id}/drawers (M0.6.2).

    A first-class response model rather than a raw dict because M0.12's
    OpenAPI snapshot contract test captures whatever the route declares.
    A route returning a bare dict renders as an empty object in the
    generated schema, which would lock in "this endpoint has no
    documented response" forever and silently pass every future
    response-shape change.

    ``total`` is ``None`` whenever a wing/room filter is applied — the
    honest-totals contract (see deltas "2026-04-20 list_drawers honest
    totals"). Chroma's ``col.count()`` has no filter argument, so
    returning an unfiltered total under a filter would be a
    user-visible lie. ``None`` lets the Flask adapter render "showing
    N of many" or omit the count.

    ``next_offset`` is ``None`` at end-of-pagination — either because
    the current page was short (``len(drawers) < limit``) or because
    ``offset + limit >= total``. Callers iterate until they see
    ``next_offset is None``.
    """

    drawers: list[Drawer]
    total: Optional[int] = None
    next_offset: Optional[int] = None


__all__ = [
    "AddDrawerResult",
    "Role",
    "IngestRequest",
    "IngestResponse",
    "Drawer",
    "WakeupMeta",
    "WakeupContext",
    "WakeupRequest",
    "SearchRequest",
    "DeleteRequest",
    "DeletionReceipt",
    "DrawersPage",
    "RebuildRequest",
    "RebuildResponse",
]

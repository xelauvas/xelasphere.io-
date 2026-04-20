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
]

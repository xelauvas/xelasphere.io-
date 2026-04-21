"""Shared helpers for the sidecar's palace-scoped routes.

Used by every file in ``mempalace_sidecar.routes``. The split into
this module (from the original ``routes/palace.py`` in M0.8)
eliminates cross-route-file imports: ``palace.py`` does not import
from ``lifecycle.py`` and vice versa; both import these helpers
from ``_common`` instead. Keeps the separation of concerns honest —
any future helper that two route files need lives here, not in
whichever route file happened to define it first.

The module name is ``_``-prefixed to mark it as internal to the
``routes`` package. Callers outside the package should not import
from it directly; they go through the route surfaces.

``_hash_uid`` is duplicated inline in ``kg_worker.py`` (as a local
``_hash_uid`` function) pending the M0.11 consolidation into a
shared ``logging_utils`` module — see deltas 2026-04-20 M0.4
review-response + M0.11 deferral list. Keeping the two copies in
sync by contract so every sidecar log line emitting ``user_id``
(ingest, wake-up, search, drawers, DELETE, rebuild, kg_worker's
failure path) produces the same 12-char hash for the same
``(user_id, salt)`` pair. Operators correlate a retried turn across
Flask-outbox → sidecar-ingest → kg_worker-extract by matching the
hash across log streams.
"""

from __future__ import annotations

import hashlib

from fastapi import Request


def _hash_uid(user_id: str, salt: str) -> str:
    """Spec §8.4 pseudonymous-logging contract: ``sha256(user_id +
    salt)[:12]``. Never raw user_id to journald.
    """
    return hashlib.sha256(f"{user_id}{salt}".encode()).hexdigest()[:12]


def _registry(request: Request):
    """Pull the ``PalaceRegistry`` off ``app.state``.

    Wired by ``main._lifespan`` (spec §5.4). This tiny helper keeps
    the route bodies focused on their core flow and makes the
    registry dependency visible to any future middleware that wants
    to introspect it (e.g. M0.10 shutdown drain checks resident
    palaces via this attribute).
    """
    return request.app.state.registry


__all__ = ["_hash_uid", "_registry"]

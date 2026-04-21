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

``_hash_uid`` is now a re-export of ``logging_utils.hash_uid``
(M0.11 consolidation — the canonical module). Retained under the
``_hash_uid`` name so the existing route imports keep working
unchanged; M0.12+ migrates the callers to the canonical name and
deletes this alias. Because it's an alias assignment (not a
duplicate function body), the two names can't drift — any future
edit to the hash landed in ``logging_utils.hash_uid`` propagates
automatically. ``kg_worker._hash_uid`` remains a parallel body
pending the M0.12+ migration; ``test_hash_uid_consistency_across_all_callers``
keeps it aligned by value.
"""

from __future__ import annotations

from fastapi import Request

from mempalace_sidecar.logging_utils import hash_uid as _hash_uid


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

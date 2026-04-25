"""V0ToV1NoOp — test-harness-only migration (M0.9).

**This migration is a no-op by design.** It exists to exercise the
migration runner's mechanism (copytree → apply chain → rename), not
to perform any real v0 → v1 data transformation. There are no
production palaces at schema_version=0 because every palace ever
created by this sidecar landed with ``schema_version=CURRENT_SCHEMA_VERSION=1``
from ``PalaceHandle.open``'s meta.json write path
(``palace_handle.py:190-195``).

**Why keep a no-op migration?** Runner validation at
``migrations/__init__.py::_validate`` requires the registered chain
to cover every version from ``0`` up to ``CURRENT_SCHEMA_VERSION``.
Without V0ToV1NoOp, a synthetic v0 palace (constructed by tests or
an operator recovering from some external state) would fail to
open with "gap in migration chain." The no-op covers the gap.

**When this retires.** When CURRENT_SCHEMA_VERSION bumps to ≥2 and
a real v1→v2 migration exists, the v0→v1 chain entry is still
needed (so v0 palaces from any source can upgrade through to current).
V0ToV1NoOp stays. It retires only if we ever decide v0 palaces are
unreachable in practice AND we're willing to drop support for them.
That decision is post-P0, not now.

**Class name is deliberately verbose.** ``V0ToV1`` alone reads as
"real migration from v0 to v1" and invites reviewers to ask "what
does it do?" The ``NoOp`` suffix encodes intent in the class name
itself — no docstring-reading required.
"""

from __future__ import annotations

from pathlib import Path

from mempalace_sidecar.migrations._base import Migration


class V0ToV1NoOp(Migration):
    """Harness-validating no-op migration from schema_version=0 → 1.

    See module docstring for rationale. ``apply`` is intentionally
    empty — the runner's mechanism is what this migration exercises,
    not any data transformation.
    """

    from_version = 0
    to_version = 1

    def apply(self, palace_dir: Path) -> None:
        # Intentional no-op. See module docstring.
        return None


__all__ = ["V0ToV1NoOp"]

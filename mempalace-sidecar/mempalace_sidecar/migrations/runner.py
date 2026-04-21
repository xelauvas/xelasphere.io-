"""Migration runner — sibling-rename atomic pattern (M0.9, spec §5.6 dim3).

**Stage 1 RED state: stubs raise NotImplementedError.** Stage 3 GREEN
replaces the stubs with the full implementations per plan lines
2720-2827.

Only ``current_version`` is implemented at RED — it's pure logic
(read meta.json, return schema_version or 0) and harmlessly true-no-
matter-what. Everything else (``_copy_hardlink_if_possible``,
``_recover_if_interrupted``, ``apply_migrations``, ``reap_stale``)
raises ``NotImplementedError`` until Stage 3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from mempalace_sidecar.migrations._base import Migration


def current_version(palace_dir: Path) -> int:
    """Return ``palace_dir/meta.json``'s ``schema_version``, or ``0``
    if meta.json is missing. Does NOT raise on corrupt meta.json —
    that's ``PalaceHandle.open``'s job to handle (spec §5.6 step 2:
    corrupt meta raises ``RuntimeError`` from the caller).

    Stage 1 RED: implemented (pure logic, no deps).
    """
    meta = palace_dir / "meta.json"
    if not meta.exists():
        return 0
    return int(json.loads(meta.read_text()).get("schema_version", 0))


def _copy_hardlink_if_possible(src: Path, dst: Path) -> None:
    """Stage 1 stub. Stage 3 GREEN implements per plan line 2738."""
    raise NotImplementedError("M0.9 runner stub — implemented in Stage 3")


def _recover_if_interrupted(palace_dir: Path) -> bool:
    """Stage 1 stub. Stage 3 GREEN implements per plan line 2749."""
    raise NotImplementedError("M0.9 runner stub — implemented in Stage 3")


def apply_migrations(
    palace_dir: Path,
    target_version: int,
    migrations: Iterable[Migration],
) -> None:
    """Stage 1 stub. Stage 3 GREEN implements per plan line 2768."""
    raise NotImplementedError("M0.9 runner stub — implemented in Stage 3")


def reap_stale(palaces_dir: Path, max_age_sec: int = 24 * 3600) -> int:
    """Stage 1 stub. Stage 3 GREEN implements per plan line 2800."""
    raise NotImplementedError("M0.9 runner stub — implemented in Stage 3")


__all__ = [
    "current_version",
    "_copy_hardlink_if_possible",
    "_recover_if_interrupted",
    "apply_migrations",
    "reap_stale",
]

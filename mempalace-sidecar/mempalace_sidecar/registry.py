"""PalaceRegistry — per-user palace cache (stub; M0.3.1+ implementation pending).

This file is the RED-state stub; the real implementation lands in the
GREEN commit that follows. Names exist so tests import-resolve and fail
on behaviour rather than on ImportError — the latter would obscure
which contract was missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PalaceEntry:
    """Placeholder — real fields land in the GREEN commit."""


class PalaceRegistry:
    """Placeholder — real implementation lands in the GREEN commit."""

    def __init__(self, *, data_dir: Path, max_bytes: int, idle_ttl_sec: int) -> None:
        raise NotImplementedError("PalaceRegistry not yet implemented")

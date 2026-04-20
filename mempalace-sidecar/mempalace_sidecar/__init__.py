"""MemPalace sidecar.

Shadow logs are owned by the Flask app (``app/mempalace/shadow_sink.py``),
not the sidecar. See docs/superpowers/specs/2026-04-20-mempalace-integration-design.md
section 3.
"""
from mempalace_sidecar.version import __version__

__all__ = ["__version__"]

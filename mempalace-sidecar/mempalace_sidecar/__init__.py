"""MemPalace sidecar.

Shadow logs are owned by the Flask app (``app/mempalace/shadow_sink.py``),
not the sidecar. See docs/superpowers/specs/2026-04-20-mempalace-integration-design.md
section 3.
"""
# Import-order contract: the chromadb singleton patch MUST land before
# any PersistentClient / Collection is constructed in the process —
# see mempalace_sidecar/_chromadb_patch.py docstring. Keeping this as
# the first import in package __init__ is how we win that race against
# any code path that reaches chromadb via a transitive import.
import mempalace_sidecar._chromadb_patch  # noqa: F401  # side-effect import

from mempalace_sidecar.version import __version__

__all__ = ["__version__"]

"""mempalace package root.

M0.17 shipped the four public-surface files (adapter / shadow_sink /
wakeup / tool). M0.18 extends this ``__init__`` with a process-singleton
accessor (``get_adapter``) + test-isolation reset hook (``reset_adapter``),
per plan Step 7.

**Singleton rationale.** The Flask app's orchestrator touch-sites need a
stable adapter reference across requests without each touch-site
reconstructing the full dependency graph (SidecarClient, SyncSidecarClient,
Outbox, ShadowSink, redaction rules, metrics). One adapter per worker
process; gunicorn's N-worker fan-out produces N adapters naturally, each
with its own client connections and shadow-writer thread.

**Double-checked locking.** Construct under ``_lock`` on first call;
subsequent calls bypass the lock after a non-None check. Standard
threading.Lock pattern; idempotent under concurrent first-call.

**reset_adapter() rationale.** Test isolation requires the ability to
reset module-level singletons so each test can stand up a fresh adapter
with its own fake collaborators. Mirrors the memory_v3 ``reset_v3_state``
convention already established in the codebase. Resource cleanup is
load-bearing: dropping the reference without calling teardown leaks
the shadow-sink daemon thread across tests; in a large test suite that
compounds fast. reset_adapter MUST invoke adapter.teardown() before
clearing the reference.

**Test-only guard.** reset_adapter should not be callable from production
code paths. Convention: documented as test-only + guarded by
``if _adapter is not None`` so accidental production calls are inert
when no adapter exists.
"""

from __future__ import annotations

from threading import Lock


__version__ = "0.1.0"

_adapter = None
_lock = Lock()


def get_adapter():
    """Return the process-singleton MempalaceAdapter.

    Constructs on first call using env-var-driven defaults. Subsequent
    calls return the same instance. Double-checked locking pattern
    guards against concurrent first-call construction under gunicorn
    thread workers.

    Returns:
        MempalaceAdapter: the singleton instance.
    """
    raise NotImplementedError("M0.18 Stage 3 GREEN lands get_adapter")


def _build_adapter():
    """Construct a fresh adapter with env-var-driven configuration.

    Wires both SidecarClient (async) and SyncSidecarClient (sync) per
    D1: Flask sync routes call adapter.wake_up_sync/search_sync which
    route through sync_client; async routes call adapter.wake_up/search
    which route through client.
    """
    raise NotImplementedError("M0.18 Stage 3 GREEN lands _build_adapter")


def reset_adapter() -> None:
    """Tear down and clear the singleton adapter, if one exists.

    **Test-only.** This function exists for test isolation across
    adapter rebuilds; production code should not call it. Calling
    reset_adapter in production truncates the shadow-sink queue +
    closes the daemon thread + loses any pending-but-queued records.

    Resource cleanup discipline:
      1. Calls adapter.teardown() to close shadow_sink + release any
         other adapter-owned resources.
      2. Clears _adapter module-level reference so next get_adapter()
         call reconstructs.

    Idempotent when no adapter exists (no-op).
    """
    raise NotImplementedError("M0.18 Stage 3 GREEN lands reset_adapter")


__all__ = ["__version__", "get_adapter", "reset_adapter"]

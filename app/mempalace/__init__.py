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

# Process-level Metrics cache: prometheus_client's default CollectorRegistry
# is a shared-global singleton. Re-constructing Metrics against the default
# registry raises `Duplicated timeseries in CollectorRegistry` on the second
# call. Adapter rebuild (via reset_adapter → get_adapter) must NOT re-create
# Metrics; Metrics lifecycle is per-process, adapter lifecycle is separate
# (rebuilds OK for test isolation). Caught at M0.18 Stage 3 (narrow-frame
# B4-class catch; framework-semantic / prometheus-REGISTRY sub-hazard).
_metrics_cache = None


def get_adapter():
    """Return the process-singleton MempalaceAdapter.

    Constructs on first call using env-var-driven defaults. Subsequent
    calls return the same instance. Double-checked locking pattern
    guards against concurrent first-call construction under gunicorn
    thread workers.

    Returns:
        MempalaceAdapter: the singleton instance.
    """
    global _adapter
    if _adapter is None:
        with _lock:
            if _adapter is None:
                _adapter = _build_adapter()
    return _adapter


def _build_adapter():
    """Construct a fresh adapter with env-var-driven configuration.

    Wires both SidecarClient (async) and SyncSidecarClient (sync) per
    D1: Flask sync routes call adapter.wake_up_sync/search_sync which
    route through sync_client; async routes call adapter.wake_up/search
    which route through client.
    """
    import os
    from pathlib import Path

    from app.mempalace.adapter import MempalaceAdapter
    from app.mempalace.client import SidecarClient, SyncSidecarClient
    from app.mempalace.metrics import build_default_metrics
    from app.mempalace.outbox import Outbox
    from app.mempalace.redaction.rules import load_rules
    from app.mempalace.shadow_sink import ShadowSink

    socket = os.getenv("MEMPALACE_SOCKET", "/run/mempalace/sidecar.sock")
    token = os.getenv("MEMPALACE_TOKEN", "")
    outbox_path = Path(
        os.getenv("MEMPALACE_OUTBOX_PATH", "/var/lib/xelasphere/outbox.db")
    )
    shadow_dir = Path(
        os.getenv("MEMPALACE_SHADOW_DIR", "/var/log/mempalace/shadow")
    )
    rules = load_rules(
        Path(__file__).parent / "redaction" / "redaction_rules.yaml"
    )

    # Process-level Metrics cache (see module-level `_metrics_cache` comment):
    # reuse the same Metrics across adapter rebuilds so prometheus_client's
    # default CollectorRegistry doesn't get re-registered.
    global _metrics_cache
    if _metrics_cache is None:
        _metrics_cache = build_default_metrics()
    metrics = _metrics_cache

    client = SidecarClient(socket_path=socket, token=token)
    sync_client = SyncSidecarClient(socket_path=socket, token=token)
    outbox = Outbox(outbox_path)
    shadow_sink = ShadowSink(
        log_dir=shadow_dir,
        drop_metric=metrics.shadow_dropped,
        error_metric=metrics.shadow_writer_error,
    )

    return MempalaceAdapter(
        client=client,
        sync_client=sync_client,
        outbox=outbox,
        shadow_sink=shadow_sink,
        redaction_rules=rules,
        metrics=metrics,
    )


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
    global _adapter
    if _adapter is None:
        return
    try:
        _adapter.teardown()
    except Exception:
        # Teardown must never propagate — a reset that raises leaves the
        # test suite in an ambiguous state. Swallow and continue with
        # the reference clear; next get_adapter() call reconstructs.
        pass
    _adapter = None


__all__ = ["__version__", "get_adapter", "reset_adapter"]

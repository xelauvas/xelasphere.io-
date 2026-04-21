"""Prometheus metric declarations — spec §8.1 catalog (sidecar side).

Every metric the sidecar exposes at ``GET /metrics`` lives here, in a
single shared module. Route files, the registry, the KG worker, the
migration runner, and the shutdown protocol all import their counters
from this module — never declare a new ``Counter`` / ``Gauge`` /
``Histogram`` in a route file.

**Why one shared module (M0.11 multiproc fix).** ``prometheus_client``
under multi-worker uvicorn uses per-pid mmap files
(``PROMETHEUS_MULTIPROC_DIR``). Metrics declared on the default
registry inside a route module still multiproc-correctly IFF the
aggregation mode is compatible — but the declaration surface is no
longer auditable in one place, and a future counter that uses
``multiprocess_mode="max"`` when ``"livesum"`` is the semantic intent
would only surface in dashboards. Collecting every declaration here
makes the label contract, type contract, and aggregation-mode
contract inspectable from a single file. Spec §8.1 is the
authoritative catalog; this file is the code realisation.

**Registration.** Metrics register against ``prometheus_client``'s
default ``REGISTRY`` at import time. ``render_metrics()`` decides
between the default registry (single-process / tests) and a
``MultiProcessCollector``-backed one (multi-worker), keyed on the
``PROMETHEUS_MULTIPROC_DIR`` env var presence. ``multiprocess_mode``
args on the gauges are inert in single-process mode; the double-
mode compatibility is how dev tests pass without the env var while
production aggregates correctly.

**Cache-gauge livesum semantics.** ``palace_cache_size`` and
``palace_cache_bytes`` are per-worker measurements aggregated
across workers by the ``livesum`` multiprocess mode — each worker
writes its own current ``(count, bytes)`` state to its per-pid
mmap file; the scrape collector sums across files. Matches spec
§8.1 "number of hot palaces" = fleet total. The registry calls
``update_cache_gauges(count, bytes)`` at the tail of every
cache-mutation method so each worker's last-known value is current
whenever a scrape lands on it. See ``registry.py`` for the five
mutation sites.
"""

from __future__ import annotations

import logging
import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.exposition import CONTENT_TYPE_LATEST


log = logging.getLogger(__name__)


# --- Ingest / read paths ------------------------------------------------

ingest_total = Counter(
    "mempalace_ingest_total",
    "Ingest outcomes (write throughput + idempotency hit rate).",
    ["status"],  # accepted | duplicate | error
)

wakeup_seconds = Histogram(
    "mempalace_wakeup_seconds",
    "Wake-up layer latency.",
    ["layer", "hot"],  # layer={l0,l1,l2,l3,total} hot={0,1}
)

search_seconds = Histogram(
    "mempalace_search_seconds",
    "Search-route latency.",
    ["hot"],
)

chroma_query_seconds = Histogram(
    "mempalace_chroma_query_seconds",
    "ChromaDB library-internal embed+search time (L2/L3).",
    ["layer"],
)


# --- Palace cache (per-worker, livesum across workers) ------------------

palace_cache_size = Gauge(
    "mempalace_palace_cache_size",
    "Hot palace count (livesum across workers).",
    multiprocess_mode="livesum",
)

palace_cache_bytes = Gauge(
    "mempalace_palace_cache_bytes",
    "Resident bytes vs cap (livesum across workers).",
    multiprocess_mode="livesum",
)

palace_cache_hits_total = Counter(
    "mempalace_palace_cache_hits_total",
    "Hot-palace hits (ratio computed at query time, not exported as a gauge).",
)

palace_cache_misses_total = Counter(
    "mempalace_palace_cache_misses_total",
    "Cold palace opens.",
)

palace_cache_evictions_total = Counter(
    "mempalace_palace_cache_evictions_total",
    "Cache evictions.",
    ["reason"],  # size | idle
)


# --- KG worker ----------------------------------------------------------

kg_queue_depth = Gauge(
    "mempalace_kg_queue_depth",
    "KG pending-queue depth.",
    ["status"],  # pending | running | failed
    multiprocess_mode="livesum",
)

kg_daily_usd_spent = Gauge(
    "mempalace_kg_daily_usd_spent",
    "KG daily spend vs cap.",
    multiprocess_mode="livesum",
)

kg_extraction_total = Counter(
    "mempalace_kg_extraction_total",
    "KG worker tick outcomes.",
    ["status"],  # success | failure | skipped_cap
)


# --- Migrations / palace-open errors ------------------------------------

schema_migration_total = Counter(
    "mempalace_schema_migration_total",
    "Schema migrations attempted + completed.",
    ["from_version", "to_version", "status"],
)

open_palace_errors_total = Counter(
    "mempalace_open_palace_errors_total",
    "Palace-open failure modes.",
    ["reason"],  # corrupt | schema_too_new | perm
)


# --- Lifecycle ----------------------------------------------------------

delete_total = Counter(
    "mempalace_delete_total",
    "GDPR-delete completions (cross-ref audit.sqlite).",
)


# --- Readiness gauge ----------------------------------------------------

readyz_gauge = Gauge(
    "mempalace_readyz",
    "1 if all subsystems good, else 0.",
    multiprocess_mode="livesum",
)


# --- Cache-gauge update helper (called from registry mutation sites) ---


def update_cache_gauges(count: int, total_bytes: int) -> None:
    """Refresh the per-worker cache gauges after a ``PalaceRegistry``
    cache mutation.

    Called by ``registry.open``, ``registry.sweep_idle``,
    ``registry._evict_to_fit_unlocked``, ``registry.close_all``, and
    the M0.8 delete path. Each worker writes its local state; the
    scrape aggregates across workers via ``livesum``.
    """
    palace_cache_size.set(count)
    palace_cache_bytes.set(total_bytes)


# --- Rendering ---------------------------------------------------------


_MULTIPROC_WARNED = False


def render_metrics() -> tuple[bytes, str]:
    """Render current metric state for ``GET /metrics``.

    Branches on ``PROMETHEUS_MULTIPROC_DIR``:

    - **Set (production, multi-worker uvicorn).** Build a fresh
      ``CollectorRegistry`` + attach a ``MultiProcessCollector`` —
      the collector walks the per-pid mmap files under the env dir
      and aggregates across workers (livesum / max / min per the
      Gauge's declared mode). This is the load-bearing path for
      spec §8.1 aggregation semantics.
    - **Unset (dev, unit tests).** Fall back to the module-global
      default registry. Gauges with ``multiprocess_mode`` still
      work — the mode is inert in single-process context. A
      one-shot WARNING log on the first scrape names the fallback
      so an operator who accidentally deployed without the env var
      sees it surfaced.

    Returns ``(body_bytes, content_type)``; the ops route wraps
    the tuple in a ``Response``.
    """
    global _MULTIPROC_WARNED
    mp_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        if not _MULTIPROC_WARNED:
            log.warning(
                "PROMETHEUS_MULTIPROC_DIR not set — rendering from "
                "default registry. Multi-worker uvicorn deployments "
                "will produce non-monotonic scrapes in this mode."
            )
            _MULTIPROC_WARNED = True
        # prometheus_client's default REGISTRY — every metric above
        # is registered on it via the module-level declarations.
        from prometheus_client import REGISTRY as _default
        registry = _default
    return generate_latest(registry), CONTENT_TYPE_LATEST


__all__ = [
    "ingest_total",
    "wakeup_seconds",
    "search_seconds",
    "chroma_query_seconds",
    "palace_cache_size",
    "palace_cache_bytes",
    "palace_cache_hits_total",
    "palace_cache_misses_total",
    "palace_cache_evictions_total",
    "kg_queue_depth",
    "kg_daily_usd_spent",
    "kg_extraction_total",
    "schema_migration_total",
    "open_palace_errors_total",
    "delete_total",
    "readyz_gauge",
    "update_cache_gauges",
    "render_metrics",
]

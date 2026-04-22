"""M0.17 Stage 1 RED — ShadowSink scaffold.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 3986-4054 (Task M0.17.1).

Stage 1 RED shape: class exists; methods raise NotImplementedError so
tests fail predictably. Stage 3 GREEN implements the daemon-thread +
queue + day-rotated file writer.

Design deviations locked at pre-resolution (to land at Stage 3 GREEN):
  - **D1.** `drop_metric` is a callable `Callable[[str], None]` taking a
    `reason` label (not an opaque `.inc()`-having object). Aligns with
    `Metrics.shadow_dropped(reason)` in spec §8.1 amendment.
  - **D7.** `_run` loop body wrapped in try/except so FS errors
    (disk-full, permission-denied, FS-ro) don't kill the daemon thread
    silently. Error is counted via `error_metric` (Metrics.shadow_writer_error)
    and logged; loop continues.

Neither deviation changes the public API shape from the plan sketch;
both refine behavior.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable, Optional


class ShadowSink:
    """Thread-safe, non-blocking shadow-log writer.

    Called from sync gunicorn workers via `enqueue()` (`queue.put_nowait`
    under the hood); a daemon writer thread drains to disk. Overflow
    (queue full) increments the drop metric with a `queue_full` reason
    and drops the record — the request path is never blocked.

    **Concurrency model.** Multi-producer (request threads calling
    `enqueue`) / single-consumer (the daemon writer thread). `queue.Queue`
    is stdlib-thread-safe for this pattern. No additional locks required.

    **Daemon-thread resilience (D7).** The writer thread catches per-iteration
    exceptions, counts them via `error_metric`, logs, and continues. An
    FS-layer failure does not silently kill the thread.
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        max_queue: int = 10_000,
        drop_metric: Optional[Callable[[str], None]] = None,
        error_metric: Optional[Callable[[str], None]] = None,
    ) -> None:
        raise NotImplementedError("M0.17 Stage 3 GREEN lands ShadowSink.__init__")

    def enqueue(self, record: dict) -> None:
        raise NotImplementedError("M0.17 Stage 3 GREEN lands ShadowSink.enqueue")

    def stop(self, timeout: float = 2.0) -> None:
        raise NotImplementedError("M0.17 Stage 3 GREEN lands ShadowSink.stop")


__all__ = ["ShadowSink"]

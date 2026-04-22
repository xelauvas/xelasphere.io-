"""ShadowSink — thread-safe, non-blocking shadow-log writer (M0.17).

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 3986-4054 (Task M0.17.1). Spec reference: §3 line 138 (shadow
mode enqueues non-blocking); §8.1 amendment (shadow_dropped_total,
shadow_writer_error_total).

**Concurrency model.** Multi-producer (request threads calling
`enqueue`) / single-consumer (the daemon writer thread). `queue.Queue`
is stdlib-thread-safe for this pattern; no additional locks required.

**Design deviations (pinned at M0.17 pre-resolution):**
  - **D1.** `drop_metric` is a `Callable[[str], None]` taking a reason
    label, not an opaque `.inc()`-having object. Aligns with the
    `Metrics.shadow_dropped(reason)` method shape in spec §8.1.
  - **D7.** `_run` loop body wraps FS I/O in try/except so disk-full /
    permission-denied / FS-readonly errors don't silently kill the
    daemon thread. The error is counted via `error_metric(reason)`
    and logged; loop continues. Prevents silent shadow-data-loss.
"""

from __future__ import annotations

import errno
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional


log = logging.getLogger(__name__)


# Errnos mapped to the `fs_full` reason label. EDQUOT (quota exceeded)
# is semantically equivalent to ENOSPC for operator purposes (same R11
# DiskLow runbook path per spec §8.3). Linux/macOS have EDQUOT; Windows
# does not — getattr guard preserves cross-platform loadability.
_FS_FULL_ERRNOS = {errno.ENOSPC}
if hasattr(errno, "EDQUOT"):
    _FS_FULL_ERRNOS.add(errno.EDQUOT)


def _classify_os_error(exc: BaseException) -> str:
    """Map writer-thread exceptions to the reason label taxonomy
    declared in spec §8.1 shadow_writer_error_total:
        fs_full / permission_denied / fs_readonly / other
    """
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, OSError):
        if exc.errno in _FS_FULL_ERRNOS:
            return "fs_full"
        if exc.errno == errno.EROFS:
            return "fs_readonly"
    return "other"


class ShadowSink:
    """Non-blocking shadow-log writer.

    Called from sync gunicorn workers via `enqueue()` (wraps
    `queue.put_nowait`); a daemon writer thread drains to disk. Queue
    overflow fires `drop_metric("queue_full")` and discards the record —
    the request path is never blocked.

    Daily-rotated file: `shadow-YYYY-MM-DD.jsonl` under `log_dir`,
    append mode so a restart within the same day continues to the same
    file (logrotate is expected to handle compression per spec §7).
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        max_queue: int = 10_000,
        drop_metric: Optional[Callable[[str], None]] = None,
        error_metric: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._drop_metric = drop_metric
        self._error_metric = error_metric
        self._fh = None
        self._cur_day: Optional[str] = None
        self._stopped = False
        self._t = threading.Thread(
            target=self._run, name="mempalace-shadow-writer", daemon=True
        )
        self._t.start()

    def enqueue(self, record: dict) -> None:
        """Non-blocking enqueue. Never raises.

        Drop-metric labeling (Spec M1 fold):
          - ``queue_full``: the daemon-writer queue is saturated; put_nowait
            raised queue.Full. Operator signal for "back-pressure; scale
            up or investigate writer-thread health."
          - ``sink_stopped``: enqueue called after stop(). Operator signal
            for "record lost during graceful shutdown or post-shutdown
            call-site bug." Distinct from queue_full so dashboards
            querying "back-pressure rate" aren't noisy on graceful shutdown.
        """
        if self._stopped:
            if self._drop_metric is not None:
                self._drop_metric("sink_stopped")
            return
        try:
            self._q.put_nowait(record)
        except queue.Full:
            if self._drop_metric is not None:
                self._drop_metric("queue_full")

    def stop(self, timeout: float = 2.0) -> None:
        """Drain remaining queue, join writer thread, close fh.

        Idempotent per M0.3 convention — second call is a no-op.
        """
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        self._t.join(timeout=timeout)
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def _run(self) -> None:
        """Daemon writer loop. Pulls records off the queue and writes
        them to the current day's file. Wrap FS I/O in try/except per
        D7 so a transient FS error doesn't silently kill the thread.
        """
        while not self._stop.is_set() or not self._q.empty():
            try:
                rec = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._rotate_if_needed()
                self._fh.write(json.dumps(rec).encode() + b"\n")
                self._fh.flush()
            except Exception as exc:
                # D7: error_metric fires with reason label; loop continues.
                # If _fh was opened but write failed, leave it open for the
                # next iteration — it may recover on a transient condition.
                # Rotation-level errors also land here (mkdir fail, open fail).
                if self._error_metric is not None:
                    self._error_metric(_classify_os_error(exc))
                log.exception("mempalace-shadow-writer: error during write")
                # If rotation itself failed, clear _fh so next iteration
                # attempts a fresh open rather than reusing a bad handle.
                if self._fh is not None:
                    try:
                        if self._fh.closed:
                            self._fh = None
                    except Exception:
                        self._fh = None

    def _rotate_if_needed(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if self._cur_day != today:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            path = self._log_dir / f"shadow-{today}.jsonl"
            self._fh = open(path, "ab")
            self._cur_day = today


__all__ = ["ShadowSink"]

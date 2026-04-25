"""Flusher entrypoint for mempalace-flusher.service (M0.19.4).

Spec §3: "single flusher thread in v1." Running the flusher as its own
systemd unit enforces single-instance trivially (one PID per unit),
keeps its lifecycle independent of the web tier, and avoids the gunicorn
N-workers-per-outbox-SQLite-DB race that running inside a gunicorn
post_worker_init hook would cause.

Deployment: systemd ExecStart points at
``/opt/xelasphere/venv/bin/python -m app.mempalace.flusher_main``.

Lifecycle:
  1. Construct SyncSidecarClient + Outbox + default Metrics.
  2. Start Flusher thread.
  3. Install SIGTERM + SIGINT handlers that call Flusher.stop().
  4. Flusher.join() blocks until stop() + loop exit.
  5. Close client + exit 0.

systemd sends SIGTERM on shutdown; KillSignal=SIGTERM is set in the
unit file. TimeoutStopSec=15s gives the flusher 10s drain budget (spec
§4) plus 5s grace.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

from app.mempalace.client import SyncSidecarClient
from app.mempalace.flusher import Flusher
from app.mempalace.metrics import build_default_metrics
from app.mempalace.outbox import Outbox


def run() -> None:
    logging.basicConfig(
        level=os.getenv("MEMPALACE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("mempalace-flusher")

    socket_path = os.getenv("MEMPALACE_SOCKET_PATH", "/run/mempalace/sidecar.sock")
    token = os.environ["MEMPALACE_TOKEN"]
    outbox_path = Path(os.environ["MEMPALACE_OUTBOX_PATH"])

    client = SyncSidecarClient(socket_path=socket_path, token=token)
    outbox = Outbox(outbox_path)
    metrics = build_default_metrics()

    flusher = Flusher(outbox, client, metrics)
    flusher.start()
    log.info("mempalace-flusher started")

    def _stop(signum, frame):  # noqa: ARG001
        log.info("signal %s received, draining flusher ...", signum)
        flusher.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    flusher.join()  # blocks until stop() set + loop exits
    client.close()
    log.info("mempalace-flusher exited cleanly")
    sys.exit(0)


if __name__ == "__main__":
    run()

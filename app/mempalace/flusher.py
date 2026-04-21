"""Flask-side flusher thread: drains the outbox → sidecar ingest.

Implements plan sketch at `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 3743-3815 (Task M0.15.7) with D4/D7/D8 deviations per the M0.15
Stage 4a deltas entry.

**D8 — Cross-side Idempotency-Key invariant.** `_send_one` sets
`headers = {"Idempotency-Key": row["turn_id"]}`. The sidecar validates at
`mempalace-sidecar/mempalace_sidecar/routes/palace.py:89` and returns 400
if the header disagrees with `body.turn_id`. Failure mode if this
invariant drifts (e.g., a future refactor changes the header to a
client-side UUID for tracing): every request 400s, the circuit breaker
trips uniformly across the fleet, and debugging surfaces as "breaker
perma-tripped" with no local error message. Silent cascade, not local
error. Do NOT change this header value without updating the sidecar
validator.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

import httpx

logger = logging.getLogger(__name__)


class Flusher(threading.Thread):
    daemon = True

    def __init__(
        self,
        outbox,
        client,
        metrics,
        *,
        poll_interval_s: float = 1.0,
        batch_size: int = 20,
        purge_interval_loops: int = 600,
    ) -> None:
        super().__init__(name="mempalace-flusher")
        self._outbox = outbox
        self._client = client
        self._metrics = metrics
        self._poll = poll_interval_s
        self._batch = batch_size
        self._purge_every = purge_interval_loops
        self._stop_event = threading.Event()
        self._loops = 0

    def stop(self) -> None:
        """Signal the flusher to exit its run loop on the next iteration."""
        self._stop_event.set()

    def run(self) -> None:
        """Main flusher loop: claim → send → mark_sent/mark_failed, with
        periodic purge every N loops.

        **D4 exception-handling contract.** Transient errors
        (`sqlite3.OperationalError`, `httpx.HTTPError`) are logged at
        WARNING and the loop continues. Unexpected exceptions PROPAGATE
        and kill the flusher thread — real bugs (programmer errors,
        schema mismatches) must not be silently eaten (doctrine hit #4
        class: bare `except Exception: pass` swallowing real bugs).

        Plan fix #9: `flusher_tick` is a real Prometheus gauge, updated
        EVERY iteration including idle (no-rows) iterations, so ops can
        distinguish "flusher alive but idle" from "flusher dead."
        """
        while not self._stop_event.is_set():
            self._metrics.flusher_tick()
            try:
                batch_t0 = time.monotonic()
                rows = self._outbox.claim_batch(self._batch)
                for row in rows:
                    try:
                        self._send_one(row)
                        self._outbox.mark_sent([row["id"]])
                    except Exception as e:
                        # Per-row failure: record and move on to next row.
                        # Row-level failures should never kill the flusher;
                        # the outer-try D4 narrowing applies to row-loop
                        # setup errors (claim_batch, mark_*), not per-row
                        # sidecar failures.
                        self._outbox.mark_failed(row["id"], str(e)[:500])
                if rows:
                    self._metrics.flush_batch(time.monotonic() - batch_t0)
                self._loops += 1
                if self._loops % self._purge_every == 0:
                    try:
                        self._outbox.purge_old_sent(days=7)
                    except Exception as e:
                        # Purge is best-effort; failure during purge should
                        # not kill the flusher. Log and continue.
                        logger.warning(
                            "flusher purge failed",
                            extra={"error": str(e)[:500]},
                        )
            except (sqlite3.OperationalError, httpx.HTTPError) as e:
                # D4: transient — log and continue.
                logger.warning(
                    "flusher transient error",
                    extra={"error": str(e)[:500]},
                )
            # Unexpected exceptions are NOT caught here; they propagate
            # out of run(), which kills the thread. That's intentional.
            if self._stop_event.wait(self._poll):
                break

    def _send_one(self, row: dict) -> None:
        """Send a single outbox row to the sidecar.

        D8 cross-side invariant: Idempotency-Key MUST equal body.turn_id.
        """
        body = {
            "turn_id": row["turn_id"],
            "session_id": row["session_id"],
            "pair_id": row["pair_id"],
            "role": row["role"],
            "text": row["text"],
            "ts": row["ts"],
        }
        headers = {"Idempotency-Key": row["turn_id"]}
        if row.get("request_id"):
            headers["X-Request-ID"] = row["request_id"]
        self._client.post_json(
            f"/palace/{row['user_id']}/ingest", body, headers=headers
        )

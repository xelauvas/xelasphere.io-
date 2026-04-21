"""Flask-side flusher thread: drains the outbox → sidecar ingest.

M0.15 Stage 1 RED scaffolding: `Flusher.__init__` stores config + invokes
`threading.Thread.__init__`. `run()` is NOT overridden at Stage 1 — the
inherited Thread.run() default (no-op) applies; tests that start the
flusher and assert on side effects (gauge ticks, row state transitions)
fail with AssertionError. `stop()` is undefined; tests calling it get
AttributeError. Mixed RED shape per template Part 2 #5 discipline
(functionality-missing is the target; AssertionError on "behaves as empty
stub" is in the same class).

Stage 3 GREEN implements the claim/flush/mark loop per plan sketch at
`docs/superpowers/plans/2026-04-20-mempalace-integration.md` lines 3743-3815,
with D4 exception-handling narrowing applied.

**D8 — Cross-side Idempotency-Key invariant** (documented here because the
invariant lives in `_send_one` at Stage 3 GREEN, not yet present). The
flusher's `_send_one` will set `headers = {"Idempotency-Key": row["turn_id"]}`.
Sidecar validates at `mempalace-sidecar/mempalace_sidecar/routes/palace.py:89`
and returns 400 if the header disagrees with `body.turn_id`. Failure mode if
this invariant drifts (e.g., a future refactor changes the header to a
client-side UUID for tracing): every request 400s, the circuit breaker trips
uniformly across the fleet, and debugging surfaces as "breaker perma-tripped"
with no local error message. Silent cascade, not local error. Do NOT change
this header value without updating the sidecar validator — the invariant is
enforced by the sidecar but documented nowhere in a single place.
"""

from __future__ import annotations

import threading


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

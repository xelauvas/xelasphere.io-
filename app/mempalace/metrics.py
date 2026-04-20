import time

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, REGISTRY


class Metrics:
    """Flask-side metrics for the mempalace adapter.

    Plan review fix #8 / #9: all metrics referenced elsewhere in the codebase
    are declared here, and `flusher_last_tick_seconds` is a real Prometheus
    gauge (not a private Python attr), so MempalaceFlusherStalled can fire.
    """

    def __init__(self, registry: CollectorRegistry | None = REGISTRY):
        self._r = registry
        self.outbox_depth = Gauge(
            "xelasphere_mempalace_outbox_depth", "Outbox rows by status",
            ["status"], registry=registry,
        )
        self.outbox_age_seconds = Histogram(
            "xelasphere_mempalace_outbox_age_seconds", "Pending-row age at flush",
            registry=registry,
        )
        self.outbox_rejected = Counter(
            "xelasphere_mempalace_outbox_rejected_total", "Outbox at cap", registry=registry,
        )
        self._inserts_dropped = Counter(
            "xelasphere_mempalace_inserts_dropped_total", "Silent drops",
            ["reason"], registry=registry,
        )
        self._flush_batch_seconds = Histogram(
            "xelasphere_mempalace_flush_batch_seconds", "Flusher batch time",
            registry=registry,
        )
        self._flusher_last_tick = Gauge(
            "xelasphere_mempalace_flusher_last_tick_seconds", "Monotonic ts of last flusher loop",
            registry=registry,
        )
        self._client_request_seconds = Histogram(
            "xelasphere_mempalace_client_request_seconds", "Sidecar round-trip",
            ["route", "status_code"], registry=registry,
        )
        self._circuit_state = Gauge(
            "xelasphere_mempalace_circuit_state", "0=closed 1=half 2=open",
            ["worker_pid"], registry=registry,
        )
        self._circuit_transitions = Counter(
            "xelasphere_mempalace_circuit_transitions_total", "State changes",
            ["from_state", "to_state", "worker_pid"], registry=registry,
        )
        self._redaction_hits = Counter(
            "xelasphere_mempalace_redaction_hits_total", "Scrub counts",
            ["rule"], registry=registry,
        )
        self._wakeup_skipped = Counter(
            "xelasphere_mempalace_wakeup_skipped_total", "Why injection was skipped",
            ["reason"], registry=registry,
        )
        self._mode = Gauge(
            "xelasphere_mempalace_mode", "0=off 1=shadow 2=active",
            registry=registry,
        )

    # --- call-site-friendly methods (stable adapter API) ---

    def inserts_dropped(self, reason: str) -> None:
        self._inserts_dropped.labels(reason=reason).inc()

    def flush_batch(self, seconds: float) -> None:
        self._flush_batch_seconds.observe(seconds)

    def flusher_tick(self) -> None:
        # Plan review fix #9: ticked every loop iteration, empty-outbox included.
        self._flusher_last_tick.set(time.monotonic())

    def client_request(self, route: str, status_code: int, seconds: float) -> None:
        self._client_request_seconds.labels(route=route, status_code=str(status_code)).observe(seconds)

    def circuit_state(self, state: str, *, worker_pid: int) -> None:
        mapping = {"closed": 0, "half_open": 1, "open": 2}
        self._circuit_state.labels(worker_pid=str(worker_pid)).set(mapping[state])

    def circuit_transition(self, from_state: str, to_state: str, *, worker_pid: int) -> None:
        self._circuit_transitions.labels(
            from_state=from_state, to_state=to_state, worker_pid=str(worker_pid),
        ).inc()

    def redaction_hit(self, rule: str) -> None:
        self._redaction_hits.labels(rule=rule).inc()

    def wakeup_skipped(self, reason: str) -> None:
        self._wakeup_skipped.labels(reason=reason).inc()

    def mode(self, value: str) -> None:
        mapping = {"off": 0, "shadow": 1, "active": 2}
        self._mode.set(mapping[value])


def build_default_metrics() -> Metrics:
    return Metrics()

def test_metrics_expose_expected_names():
    from app.mempalace.metrics import Metrics
    m = Metrics()
    assert m.outbox_depth is not None
    assert m.inserts_dropped("mode_off") is None  # method style
    m.circuit_state("closed", worker_pid=1)
    m.flusher_tick()
    m.redaction_hit("openai_key")


def test_inserts_dropped_increments(capsys):
    from app.mempalace.metrics import Metrics
    from prometheus_client import CollectorRegistry
    registry = CollectorRegistry()
    m = Metrics(registry=registry)
    m.inserts_dropped("mode_off")
    m.inserts_dropped("mode_off")
    from prometheus_client import generate_latest
    text = generate_latest(registry).decode()
    assert 'xelasphere_mempalace_inserts_dropped_total{reason="mode_off"} 2.0' in text

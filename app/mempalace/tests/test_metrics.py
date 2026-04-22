def test_metrics_expose_expected_names():
    from app.mempalace.metrics import Metrics
    from prometheus_client import CollectorRegistry

    # M0.18 Stage 3 fix: use a fresh registry rather than the default
    # CollectorRegistry. At M0.17 this test was run in a world where the
    # default REGISTRY was pristine when Metrics() was first constructed.
    # M0.18 adds `_build_adapter()` which constructs Metrics against the
    # default registry via `build_default_metrics()`; test-ordering can
    # therefore populate the default REGISTRY before this test runs,
    # causing a duplicate-registration ValueError. Fresh registry
    # matches the sibling test_inserts_dropped_increments pattern below
    # and removes the ordering dependency.
    m = Metrics(registry=CollectorRegistry())
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

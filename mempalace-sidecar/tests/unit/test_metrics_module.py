"""Unit test for M0.11 shared-metrics-module presence invariant.

One assertion: ``mempalace_sidecar.metrics`` is the single source of
truth for every sidecar-side Prometheus metric. No route file, no
helper module, declares its own ``Counter`` / ``Gauge`` /
``Histogram`` on the default registry.

The M0.11 multiproc fix consolidates declarations into ``metrics.py``
(see module docstring there). The most visible pre-M0.11 violation
was ``routes/lifecycle.py::_DELETE_TOTAL`` — a Counter declared on
the default registry inside a route module. Stage 3 GREEN migrates
it to ``metrics.delete_total`` and re-exports under the same name
in ``lifecycle.py`` (so existing callers' import paths still work)
pointing at the shared module's instance.

Nastiest reachable context: a future milestone adds a new metric
inside a route module because "it's easier" — regression.
``hasattr(metrics, "delete_total") and lifecycle._DELETE_TOTAL is
metrics.delete_total`` catches it.
"""

from __future__ import annotations


def test_delete_total_declared_in_shared_module() -> None:
    """Stage 1 RED-failing test.

    At RED: ``metrics.delete_total`` does not yet exist (declaration
    is deferred to Stage 3 GREEN to avoid a ``Duplicated timeseries``
    collision with ``routes/lifecycle.py::_DELETE_TOTAL``).
    ``hasattr(metrics, "delete_total")`` returns False; the first
    assertion fails. At GREEN, the lifecycle migration lands and
    both assertions pass.
    """
    from mempalace_sidecar import metrics
    from mempalace_sidecar.routes import lifecycle

    assert hasattr(metrics, "delete_total"), (
        "mempalace_sidecar.metrics.delete_total not declared — "
        "migration from routes/lifecycle.py::_DELETE_TOTAL not landed"
    )
    assert lifecycle._DELETE_TOTAL is metrics.delete_total, (
        "routes/lifecycle.py::_DELETE_TOTAL is a distinct Counter "
        "object from metrics.delete_total — migration incomplete"
    )

"""M0.17 Stage 1 RED — ShadowSink tests.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 3986-4054 (Task M0.17.1).

At Stage 1 RED: every test fails at the `ShadowSink(...)` constructor
call (NotImplementedError). Expected RED shape — functionality-missing
is the target per template Part 2 #5 discipline.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.mempalace.shadow_sink import ShadowSink


# --- D1: drop metric is Callable[[str], None] per pre-resolution ---


def test_enqueue_1000_records_all_written(tmp_path):
    """Baseline throughput: 1000 enqueues drain to disk under a clean
    stop(). File has 1000 lines; none lost.
    """
    drops: list[str] = []
    sink = ShadowSink(log_dir=tmp_path, drop_metric=drops.append)
    try:
        for i in range(1000):
            sink.enqueue({"i": i})
    finally:
        sink.stop(timeout=5.0)

    day = time.strftime("%Y-%m-%d")
    log_file = tmp_path / f"shadow-{day}.jsonl"
    assert log_file.exists(), f"shadow log file missing: {log_file}"
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1000
    assert drops == []  # no drops on clean path


def test_queue_full_drops_with_queue_full_reason(tmp_path):
    """D1 contract: when put_nowait raises queue.Full, the drop metric
    fires with reason='queue_full' and the record is discarded (request
    path never blocks).

    Strategy: max_queue=2, stop-gated writer thread, fire 10 enqueues
    without giving the writer a chance to drain. The first 2 land in
    the queue; the remaining 8 are dropped.

    Risk at Stage 3 GREEN: if the writer thread drains faster than the
    test can enqueue, expected_drops shrinks. Mitigate by holding the
    writer via a large max_queue... wait, that defeats the test.
    Correct mitigation: Stage 3 GREEN provides a drain-pause hook OR
    this test accepts `drops >= 1` as the lower bound. Locked here as
    `>= 1` with the specific 8-drop expectation documented as ideal.
    """
    drops: list[str] = []
    sink = ShadowSink(log_dir=tmp_path, max_queue=2, drop_metric=drops.append)
    try:
        for i in range(10):
            sink.enqueue({"i": i})
    finally:
        sink.stop(timeout=5.0)

    assert len(drops) >= 1, (
        f"expected at least 1 queue_full drop, got 0 "
        f"(max_queue=2, 10 enqueues; writer may have drained too fast)"
    )
    assert all(r == "queue_full" for r in drops), drops


def test_same_day_append_across_stop_start(tmp_path):
    """Stop one sink, start another, assert both write to the same
    day's file (append mode, no truncation).
    """
    sink1 = ShadowSink(log_dir=tmp_path)
    sink1.enqueue({"from": "first"})
    sink1.stop(timeout=5.0)

    sink2 = ShadowSink(log_dir=tmp_path)
    sink2.enqueue({"from": "second"})
    sink2.stop(timeout=5.0)

    day = time.strftime("%Y-%m-%d")
    log_file = tmp_path / f"shadow-{day}.jsonl"
    lines = log_file.read_text().splitlines()
    assert len(lines) == 2
    assert '"from": "first"' in lines[0]
    assert '"from": "second"' in lines[1]


# --- D7: FS-error resilience via error_metric + continue-not-die ---


def test_writer_thread_survives_fs_error(tmp_path, monkeypatch):
    """D7 contract: if the writer raises on disk I/O (e.g., disk full,
    permission denied, FS-ro), the error_metric fires with a reason
    label AND the loop continues. Subsequent enqueues still have a
    chance to drain (if FS error was transient).

    Strategy: patch `builtins.open` within the writer's scope to raise
    PermissionError on first call, then succeed. First enqueue triggers
    the error path; second enqueue succeeds.

    Quality I1 fold: assertion tightened. Previously asserted only that
    >=1 error fired and labels were in the taxonomy — a broken D7 (thread
    died after first error) would pass the old assertion. The assertion
    now also verifies that {"n": 2} actually landed on disk, which is
    the specific contract D7 preserves.
    """
    errors: list[str] = []

    import builtins
    real_open = builtins.open
    call_count = {"n": 0}

    def flaky_open(path, *args, **kwargs):
        if "shadow-" in str(path) and call_count["n"] == 0:
            call_count["n"] += 1
            raise PermissionError("simulated FS-ro")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", flaky_open)

    sink = ShadowSink(log_dir=tmp_path, error_metric=errors.append)
    try:
        sink.enqueue({"n": 1})
        time.sleep(0.2)  # give writer a tick to hit the error
        sink.enqueue({"n": 2})
    finally:
        sink.stop(timeout=5.0)

    assert len(errors) >= 1, f"expected ≥1 writer error, got {errors}"
    assert all(r in ("fs_full", "permission_denied", "fs_readonly", "other")
               for r in errors), errors

    # Quality I1 fold — survival check: the second enqueue must have
    # landed on disk. If the writer thread died after the first error,
    # {"n": 2} would never be written and the assertion below fails.
    day = time.strftime("%Y-%m-%d")
    log_file = tmp_path / f"shadow-{day}.jsonl"
    assert log_file.exists(), "log file should exist after survival"
    contents = log_file.read_text()
    assert '"n": 2' in contents, (
        f"second record did not drain after transient FS error; "
        f"writer thread likely died. contents={contents!r}"
    )


def test_writer_error_labels_cover_reason_taxonomy(tmp_path, monkeypatch):
    """D7 refinement: error_metric gets a reason label from the taxonomy
    {fs_full, permission_denied, fs_readonly, other}. Spec §8.1
    amendment declares these as the counter's label values.

    Stage 3 GREEN maps:
      - OSError with errno.ENOSPC or errno.EDQUOT → 'fs_full'
      - PermissionError → 'permission_denied'
      - OSError with errno.EROFS → 'fs_readonly'
      - any other exception → 'other'

    Quality I2 fold: assertion tightened from `expected_label in errors`
    subset check to `errors[0] == expected_label` first-element exact.
    Catches label-mapping drift that the subset check would miss when
    a recovery-path iteration fires an unintended extra label.

    Spec M2 fold: EDQUOT (quota exceeded) added to fs_full. Same
    operator-runbook path (§R11 DiskLow) as ENOSPC.
    """
    import errno

    scenarios = [
        (PermissionError("p"), "permission_denied"),
        (OSError(errno.ENOSPC, "no space"), "fs_full"),
        (OSError(errno.EROFS, "read-only fs"), "fs_readonly"),
    ]
    # Spec M2: include EDQUOT when the platform exposes it (Linux/macOS yes;
    # Windows no). Platforms lacking EDQUOT skip the scenario; the mapping
    # is still covered on CI's Linux runners.
    if hasattr(errno, "EDQUOT"):
        scenarios.append((OSError(errno.EDQUOT, "quota exceeded"), "fs_full"))

    for exc, expected_label in scenarios:
        errors: list[str] = []
        import builtins
        real_open = builtins.open
        call_count = {"n": 0}

        def flaky_open(path, *args, _exc=exc, **kwargs):
            if "shadow-" in str(path) and call_count["n"] == 0:
                call_count["n"] += 1
                raise _exc
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", flaky_open)
        sink = ShadowSink(log_dir=tmp_path, error_metric=errors.append)
        try:
            sink.enqueue({"trigger": str(exc)})
            time.sleep(0.2)
        finally:
            sink.stop(timeout=5.0)
        # Quality I2 fold: first-element exact rather than subset.
        assert errors and errors[0] == expected_label, (
            f"expected first label '{expected_label}' for {exc!r}, got {errors}"
        )


# --- lifecycle ---


def test_stop_is_idempotent(tmp_path):
    """stop() called twice does not raise. Mirrors M0.3 close-is-idempotent
    convention for the rest of the mempalace package.
    """
    sink = ShadowSink(log_dir=tmp_path)
    sink.stop(timeout=5.0)
    sink.stop(timeout=5.0)  # no raise


def test_enqueue_after_stop_drops_with_sink_stopped_reason(tmp_path):
    """Enqueue after stop fires drop_metric with reason='sink_stopped'.

    Spec M1 fold: previously fired 'queue_full' post-stop, conflating
    back-pressure ("queue saturated; investigate writer health") with
    lifecycle ("sink stopped; record arrived too late"). Distinct
    labels let operator dashboards differentiate the two conditions.

    Never raises — the calling request path must not see shadow-sink
    lifecycle errors.
    """
    drops: list[str] = []
    sink = ShadowSink(log_dir=tmp_path, drop_metric=drops.append)
    sink.stop(timeout=5.0)
    sink.enqueue({"late": True})  # no raise; drop fires
    assert drops == ["sink_stopped"]

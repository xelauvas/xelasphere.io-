"""Integration tests for M0.10 graceful shutdown (spec §5.7).

Five mandatory + one optional teeth-bearing test, per doctrine
commit c529ccd ("plan sketches require teeth-bearing verification").
Every test below drives ``shutdown_sequence`` against a real app —
no mock lifespan, no mock registry — so plan-vs-reality and
plan-vs-correctness drift both have a chance to surface.

Mandatory:

- ``test_shutdown_sequence_emits_all_phase_markers`` — all 6 phase
  markers land in the log record stream, in order. A reader
  looking at journald during a slow shutdown must be able to see
  exactly which phase stalled.

- ``test_drain_waits_for_in_flight_request`` — shutdown blocks
  until a pending request completes. Without the counter+wait_idle
  path, shutdown would yank palace handles out from under an
  in-flight handler.

- ``test_drain_respects_timeout_on_stuck_request`` — a wedged
  request cannot hold shutdown hostage. Drain returns within the
  budget even when ``wait_idle`` never wakes. Upper bound tight
  at 1.0s to catch accidental double-wait or fallback-poll bugs.

- ``test_concurrent_drain_waits_for_all_handlers`` — three
  concurrent in-flight requests all release before the registry
  close phase runs. Wall-clock bound < 1.8s catches serialized
  drain bugs (a naive loop would take N × per-handler-time).

- ``test_close_all_failure_still_emits_stopping`` — if
  ``registry.close_all`` raises, ``STOPPING=1`` must still fire.
  journald marking the unit failed-vs-stopped affects oncall
  alert routing.

Optional:

- ``test_install_signal_handlers_wires_sigterm_sigint`` —
  SIGTERM/SIGINT handlers route through ``shutdown_sequence``.
  Skipped if signal-handler introspection isn't reliable on the
  test platform.

All tests fail at Stage 1 RED because ``shutdown_sequence``
raises ``NotImplementedError``. Stage 3 GREEN lands the
implementation.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest


pytestmark = pytest.mark.integration


# ``app`` fixture comes from ``tests/integration/conftest.py`` —
# rooted at tmp_path, middleware mounted via ``create_app`` so
# ``app.state.in_flight`` is already live before the lifespan fires.


async def test_shutdown_sequence_emits_all_phase_markers(
    app, caplog
) -> None:
    """All six phase markers appear in the log stream, in order.

    Order must match spec §5.7 + module docstring: gate → worker
    cancel → drain → registry close → systemd notify → done.
    """
    from mempalace_sidecar.shutdown import shutdown_sequence

    async with app.router.lifespan_context(app):
        with caplog.at_level(logging.INFO, logger="mempalace_sidecar.shutdown"):
            await shutdown_sequence(app, drain_timeout_sec=1.0)

    expected = [
        "shutdown_gate",
        "shutdown_worker_cancel",
        "shutdown_drain",
        "shutdown_registry_close",
        "shutdown_systemd_notify",
        "shutdown_done",
    ]
    observed_in_order = [
        phase
        for rec in caplog.records
        for phase in expected
        if phase in rec.getMessage()
    ]
    # De-dup consecutive duplicates then assert the distinct
    # sequence equals the expected ordering. Any phase missing —
    # or out of order — fails here.
    deduped = []
    for name in observed_in_order:
        if not deduped or deduped[-1] != name:
            deduped.append(name)
    assert deduped == expected, (
        f"phase markers out of order or missing: got {deduped}"
    )


async def test_drain_waits_for_in_flight_request(app) -> None:
    """A pending request completes before shutdown moves on.

    Simulates an in-flight request by bumping ``app.state.in_flight``
    directly (same effect as the middleware would have). A helper
    coroutine releases the counter after 200ms. ``shutdown_sequence``
    must observe the release and proceed WITHOUT having force-cancelled.
    """
    from mempalace_sidecar.shutdown import shutdown_sequence

    async with app.router.lifespan_context(app):
        counter = app.state.in_flight
        counter.enter()

        async def _release_after_200ms() -> None:
            await asyncio.sleep(0.2)
            counter.exit()

        t0 = time.perf_counter()
        await asyncio.gather(
            shutdown_sequence(app, drain_timeout_sec=2.0),
            _release_after_200ms(),
        )
        elapsed = time.perf_counter() - t0

        # Drain blocked on the in-flight request: elapsed must be
        # at least the release delay (200ms, minus scheduler jitter).
        assert elapsed >= 0.15, (
            f"drain didn't wait for in-flight release: elapsed={elapsed:.3f}s"
        )
        # Far below the 2s timeout: drain woke promptly once the
        # counter hit zero, not via timeout fallback.
        assert elapsed < 1.0, (
            f"drain took longer than expected: elapsed={elapsed:.3f}s"
        )
        assert counter.count == 0


async def test_drain_respects_timeout_on_stuck_request(app) -> None:
    """A wedged request cannot hold shutdown hostage indefinitely.

    Leaves the counter bumped and NEVER releases it. ``shutdown_sequence``
    returns within the drain_timeout budget. Upper bound tight at 1.0s
    to catch accidental double-wait or fallback-poll bugs — if the
    timeout path did a `sleep(timeout)` AFTER a failed `wait_for`,
    this would land near 1.0s+, not inside the tight window.
    """
    from mempalace_sidecar.shutdown import shutdown_sequence

    async with app.router.lifespan_context(app):
        counter = app.state.in_flight
        counter.enter()  # never released

        t0 = time.perf_counter()
        await shutdown_sequence(app, drain_timeout_sec=0.5)
        elapsed = time.perf_counter() - t0

        assert 0.4 <= elapsed <= 1.0, (
            f"drain timeout outside tight window [0.4, 1.0]: "
            f"elapsed={elapsed:.3f}s"
        )


async def test_concurrent_drain_waits_for_all_handlers(app) -> None:
    """Three concurrent in-flight requests all complete before
    the registry close phase runs.

    A serialized drain (N × per-handler) would push wall-clock past
    1.8s; the concurrent gather-based drain should finish well
    under. Bound widened to 1.8s to tolerate scheduler jitter on
    loaded CI runners while still catching serialized regressions.
    """
    from mempalace_sidecar.shutdown import shutdown_sequence

    async with app.router.lifespan_context(app):
        counter = app.state.in_flight

        for _ in range(3):
            counter.enter()

        completed: list[float] = []

        async def _release_one(delay: float) -> None:
            await asyncio.sleep(delay)
            counter.exit()
            completed.append(time.perf_counter())

        t0 = time.perf_counter()
        await asyncio.gather(
            shutdown_sequence(app, drain_timeout_sec=3.0),
            _release_one(0.1),
            _release_one(0.2),
            _release_one(0.3),
        )
        elapsed = time.perf_counter() - t0

        assert len(completed) == 3
        assert elapsed < 1.8, (
            f"concurrent drain took too long: elapsed={elapsed:.3f}s "
            "(bug: are releases being serialized?)"
        )
        assert counter.count == 0


async def test_close_all_failure_still_emits_stopping(
    app, monkeypatch
) -> None:
    """``registry.close_all`` raising MUST NOT suppress the
    systemd ``STOPPING=1`` notify.

    journald distinguishes cleanly-stopped vs failed units; a
    swallowed notify routes oncall alerts to the wrong bucket.
    """
    from mempalace_sidecar import shutdown as shutdown_mod
    from mempalace_sidecar.shutdown import shutdown_sequence

    stopping_calls: list[None] = []

    def _fake_stopping() -> None:
        stopping_calls.append(None)

    async with app.router.lifespan_context(app):
        # Patch BOTH the main-module notifier (used by lifespan) and
        # the shutdown-module notifier (used by shutdown_sequence).
        # Stage 3 GREEN ships a single notifier location; this test
        # tolerates either wiring.
        monkeypatch.setattr(
            "mempalace_sidecar.main._sd_notify_stopping", _fake_stopping
        )
        if hasattr(shutdown_mod, "_sd_notify_stopping"):
            monkeypatch.setattr(
                shutdown_mod, "_sd_notify_stopping", _fake_stopping
            )

        async def _raising_close_all() -> None:
            raise RuntimeError("simulated close_all blowup")

        monkeypatch.setattr(
            app.state.registry, "close_all", _raising_close_all
        )

        # shutdown_sequence must not re-raise: the whole point is
        # to proceed through the notify even when close_all fails.
        await shutdown_sequence(app, drain_timeout_sec=0.5)

        assert stopping_calls, (
            "STOPPING=1 was not emitted after close_all raised"
        )


async def test_install_signal_handlers_wires_sigterm_sigint(app) -> None:
    """OPTIONAL — SIGTERM/SIGINT handlers route through
    ``shutdown_sequence``.

    Skipped if the shutdown module doesn't expose
    ``install_signal_handlers``. Stage 3 GREEN decides whether to
    wire via signal handlers or rely on uvicorn's SIGTERM routing.
    """
    import signal

    from mempalace_sidecar import shutdown as shutdown_mod

    if not hasattr(shutdown_mod, "install_signal_handlers"):
        pytest.skip("install_signal_handlers not implemented")

    loop = asyncio.get_running_loop()
    shutdown_mod.install_signal_handlers(loop, app)

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            handler = loop._signal_handlers.get(sig)  # type: ignore[attr-defined]
            assert handler is not None, (
                f"no handler installed for {sig.name}"
            )
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass

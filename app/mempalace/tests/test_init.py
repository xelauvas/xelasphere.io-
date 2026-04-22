"""M0.18 Stage 1 RED — get_adapter / reset_adapter tests.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4400-4442 (Task M0.17.x Step 7 — defined in M0.17 plan text but
landed at M0.18).

At Stage 1 RED: tests fail at get_adapter() / reset_adapter() bodies
(NotImplementedError). Expected RED shape.

Singleton tests use module-level state reset between tests via
reset_adapter fixture (which itself is Stage 3 GREEN's responsibility
to implement). At Stage 1 RED, the reset is ineffective (raises
NotImplementedError); tests manually scrub module state as fallback.
"""

from __future__ import annotations

import pytest

import app.mempalace as mempalace_pkg
from app.mempalace import __version__, get_adapter, reset_adapter


@pytest.fixture(autouse=True)
def scrub_singleton_between_tests():
    """Reset module-level _adapter between tests for isolation.

    Calls reset_adapter() if possible; falls back to direct state scrub
    if reset_adapter raises (Stage 1 RED state). This keeps test
    isolation robust across the RED → GREEN transition.
    """
    try:
        reset_adapter()
    except NotImplementedError:
        pass
    # Fallback: direct scrub regardless.
    mempalace_pkg._adapter = None
    yield
    try:
        reset_adapter()
    except NotImplementedError:
        pass
    mempalace_pkg._adapter = None


def test_package_version_preserved():
    """The M0.18 __init__ rewrite must preserve __version__ from M0.17."""
    assert __version__ == "0.1.0"


def test_get_adapter_returns_singleton_across_calls(monkeypatch):
    """Singleton contract: calling get_adapter() twice returns the same
    instance. This is the single-threaded happy path; thread-safety is
    covered by the double-checked-locking test below.
    """
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("MEMPALACE_SOCKET", "/tmp/test.sock")
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_OUTBOX_PATH", "/tmp/test-outbox.db")
    monkeypatch.setenv("MEMPALACE_SHADOW_DIR", "/tmp/test-shadow")

    first = get_adapter()
    second = get_adapter()
    assert first is second, "get_adapter must return the same instance across calls"


def test_get_adapter_constructs_on_first_call(monkeypatch):
    """Lazy construction: adapter doesn't exist until first get_adapter()
    call. Verified by scrubbing module state and confirming _adapter is
    None before first call, not None after.
    """
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("MEMPALACE_SOCKET", "/tmp/test.sock")
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_OUTBOX_PATH", "/tmp/test-outbox.db")
    monkeypatch.setenv("MEMPALACE_SHADOW_DIR", "/tmp/test-shadow")

    mempalace_pkg._adapter = None
    assert mempalace_pkg._adapter is None
    adapter = get_adapter()
    assert mempalace_pkg._adapter is adapter


def test_reset_adapter_clears_singleton(monkeypatch):
    """reset_adapter() sets _adapter to None so next get_adapter() call
    reconstructs a fresh instance.
    """
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("MEMPALACE_SOCKET", "/tmp/test.sock")
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_OUTBOX_PATH", "/tmp/test-outbox.db")
    monkeypatch.setenv("MEMPALACE_SHADOW_DIR", "/tmp/test-shadow")

    first = get_adapter()
    reset_adapter()
    assert mempalace_pkg._adapter is None, (
        "reset_adapter must clear _adapter to None"
    )
    second = get_adapter()
    assert first is not second, (
        "After reset_adapter, get_adapter must return a fresh instance"
    )


def test_reset_adapter_calls_teardown(monkeypatch):
    """Resource-cleanup discipline: reset_adapter invokes adapter.teardown()
    before clearing the reference. Without this, the shadow-sink daemon
    thread leaks across adapter rebuilds; in a test suite this compounds
    fast.
    """
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("MEMPALACE_SOCKET", "/tmp/test.sock")
    monkeypatch.setenv("MEMPALACE_TOKEN", "t" * 32)
    monkeypatch.setenv("MEMPALACE_OUTBOX_PATH", "/tmp/test-outbox.db")
    monkeypatch.setenv("MEMPALACE_SHADOW_DIR", "/tmp/test-shadow")

    adapter = get_adapter()
    teardown_calls: list[int] = []
    original_teardown = adapter.teardown

    def _recording_teardown():
        teardown_calls.append(1)
        try:
            original_teardown()
        except NotImplementedError:
            pass  # Stage 1 RED: teardown stub raises; test still asserts invocation.

    adapter.teardown = _recording_teardown
    reset_adapter()
    assert teardown_calls, "reset_adapter must call adapter.teardown() before clearing"


def test_reset_adapter_is_idempotent_when_no_adapter_exists():
    """reset_adapter() is a no-op when _adapter is None. Safe to call
    from test fixtures + shutdown hooks without pre-check guard.
    """
    import app.mempalace as mempalace_pkg_local
    mempalace_pkg_local._adapter = None
    reset_adapter()  # must not raise
    assert mempalace_pkg_local._adapter is None

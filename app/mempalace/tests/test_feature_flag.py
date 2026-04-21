"""Tests for M0.13 feature_flag + startup-sanity check.

Seven tests at rung 3 of the M0.13 test-count ladder:

Base (per plan):
  1. test_mode_defaults_to_off
  2. test_legacy_memory_defaults_to_on
  3. test_startup_rejects_both_off
  4. test_post_p4_allows_off_with_warn

Rung 2 (post-M0.12 reflection — load-bearing invariant):
  5. test_feature_flag_defaults_compose_to_sane_startup

Rung 3 (post-M0.12 reflection — production-silent deployment-paste
bugs with high diagnosis cost):
  6. test_legacy_memory_whitespace_and_empty_string_handling
  7. test_xelasphere_post_p4_strict_comparison

All fail at Stage 1 RED via NotImplementedError from the stub
module. Stage 3 GREEN implements the three functions + wires
``assert_startup_sane`` into ``app/__init__.py::create_app``.
"""

from __future__ import annotations

import pytest


# --- Base tests (from plan) ---------------------------------------


def test_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("MEMPALACE_MODE", raising=False)
    from app.mempalace.feature_flag import MempalaceMode, mode
    assert mode() == MempalaceMode.OFF


def test_legacy_memory_defaults_to_on(monkeypatch):
    monkeypatch.delenv("LEGACY_MEMORY", raising=False)
    from app.mempalace.feature_flag import legacy_memory_active
    assert legacy_memory_active() is True


def test_startup_rejects_both_off(monkeypatch):
    """mode=OFF + legacy=off + NOT post-P4 → SystemExit(2).

    Nastiest reachable context: a deployment where an operator
    flipped both flags off by mistake (or in a rushed rollout
    that didn't coordinate with MEMPALACE_MODE=shadow|active).
    Refusing to boot is the safety contract.
    """
    from app.mempalace.feature_flag import assert_startup_sane
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("LEGACY_MEMORY", "off")
    monkeypatch.setenv("XELASPHERE_POST_P4", "0")
    with pytest.raises(SystemExit):
        assert_startup_sane()


def test_post_p4_allows_off_with_warn(monkeypatch, caplog):
    """Post-P4: mode=OFF with LEGACY_MEMORY unset (defaults on in
    pre-P4 semantics but intentionally off here via the
    post-P4=1 escape) passes with a WARNING log.
    """
    from app.mempalace.feature_flag import assert_startup_sane
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.delenv("LEGACY_MEMORY", raising=False)
    monkeypatch.setenv("XELASPHERE_POST_P4", "1")
    with caplog.at_level("WARNING"):
        assert_startup_sane()
    # At least one WARNING record names the MEMPALACE_MODE=off
    # state so an operator tailing journald sees what they
    # booted into.
    assert any(
        "MEMPALACE_MODE=off" in r.message for r in caplog.records
    )


# --- Rung 2: defaults-compose invariant --------------------------


def test_feature_flag_defaults_compose_to_sane_startup(monkeypatch):
    """The unset-env default state MUST pass assert_startup_sane
    without raising SystemExit.

    This encodes the load-bearing invariant behind M0.13's
    "existing Flask tests won't regress" mitigation: ``mode()``
    defaults to OFF and ``legacy_memory_active()`` defaults to
    True, which composes to the SAFE branch of the sane-check.
    If a future default change flips either predicate, EVERY
    existing Flask test that instantiates ``create_app()``
    silently starts calling ``SystemExit(2)`` at test collection —
    not a unit-test failure, a pytest session abort. This test
    goes red at the moment of the default change, not at the
    moment some downstream test happens to notice.

    Nastiest reachable context: a future rollout phase flips
    ``LEGACY_MEMORY`` default to "off" in anticipation of P4
    without reviewing ``assert_startup_sane`` callers. Silent
    breakage of the entire Flask test suite.
    """
    from app.mempalace.feature_flag import assert_startup_sane
    monkeypatch.delenv("MEMPALACE_MODE", raising=False)
    monkeypatch.delenv("LEGACY_MEMORY", raising=False)
    monkeypatch.delenv("XELASPHERE_POST_P4", raising=False)
    # Does not raise.
    assert_startup_sane()


# --- Rung 3: production-silent deployment-paste bugs -------------


@pytest.mark.parametrize(
    "value, expected_active",
    [
        ("on", True),
        ("off", False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        # Case insensitivity — already in plan via .lower().
        ("ON", True),
        ("TRUE", True),
        # Whitespace — post-M0.12 reflection catch (f). Permissive
        # semantic chosen: operator-paste with stray leading or
        # trailing whitespace should not silently disable legacy
        # memory.
        ("on ", True),
        (" on", True),
        ("  on  ", True),
        ("off ", False),
        (" off", False),
        # Empty string — treat as "unset" fallback to on? Or "off"?
        # Chosen semantic: empty-string means "the operator set
        # the env var but the value is blank" which is more likely
        # a misconfiguration than intent-to-be-on. Return False
        # (memory off). Caught by assert_startup_sane if mode is
        # also OFF, so misconfigured deploys don't silently lose
        # memory — they refuse to boot.
        ("", False),
        # Garbage → false. Conservative default for unknown values.
        ("maybe", False),
        ("truish", False),
    ],
)
def test_legacy_memory_whitespace_and_empty_string_handling(
    monkeypatch, value: str, expected_active: bool
):
    """LEGACY_MEMORY parsing handles operator-paste whitespace
    without silently flipping memory off.

    Nastiest reachable context: config-file paste with a trailing
    newline or a leading indent. `LEGACY_MEMORY=" on"` is a
    realistic deployment mistake (operator copies from a docs page
    with a bullet-point indent). Plan-sketch used
    ``.lower() in (...)`` without ``.strip()``; test asserts the
    implementation's chosen permissive-on-whitespace semantic so
    a future .strip() removal reds here.
    """
    from app.mempalace.feature_flag import legacy_memory_active
    monkeypatch.setenv("LEGACY_MEMORY", value)
    assert legacy_memory_active() is expected_active, (
        f"LEGACY_MEMORY={value!r} → expected active={expected_active}"
    )


def test_xelasphere_post_p4_strict_comparison(monkeypatch):
    """XELASPHERE_POST_P4 is a strict ``== "1"`` comparison.

    Dangerous-flag-by-design: enabling post-P4 mode allows BOTH
    memory systems to be off at once, which in pre-P4 would be
    a user-visible data-loss. The strict comparison refuses
    near-miss values (``true``, ``yes``, ``True``) so an operator
    typo doesn't accidentally enable the escape hatch.

    Nastiest reachable context: operator types
    ``XELASPHERE_POST_P4=true`` expecting it to work (typical
    truthy-env convention). Strict comparison treats this as
    NOT-post-P4, so the pre-P4 SystemExit fires and refuses to
    boot. That's the desired behavior — fail loud on typos
    rather than silently enabling unsafe state.
    """
    from app.mempalace.feature_flag import assert_startup_sane
    monkeypatch.setenv("MEMPALACE_MODE", "off")
    monkeypatch.setenv("LEGACY_MEMORY", "off")

    # Strict: only "1" lets post-P4 mode through.
    for accept_value in ("1",):
        monkeypatch.setenv("XELASPHERE_POST_P4", accept_value)
        # Does not raise.
        assert_startup_sane()

    # Everything else (near-misses + unset) refuses to boot.
    for reject_value in ("true", "yes", "True", "TRUE", "on", "0"):
        monkeypatch.setenv("XELASPHERE_POST_P4", reject_value)
        with pytest.raises(SystemExit):
            assert_startup_sane()

    # Unset also refuses to boot (pre-P4 default).
    monkeypatch.delenv("XELASPHERE_POST_P4", raising=False)
    with pytest.raises(SystemExit):
        assert_startup_sane()

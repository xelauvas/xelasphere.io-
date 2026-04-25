"""Feature-flag + startup-sanity check for the MemPalace adapter (M0.13).

Flask-side counterpart to the sidecar's readiness protocol. Two
env-var-driven predicates + a boot-time sanity check that refuses
to start the app if the combined flag state would leave users
with no memory subsystem at all.

**Defaults-composition invariant (load-bearing).**
  ``mode()`` defaults to OFF.
  ``legacy_memory_active()`` defaults to True.
  ``assert_startup_sane()`` passes iff NOT (mode==OFF AND legacy=False).

Composition: unset-env is OFF + True = SAFE. Existing Flask tests
that don't set either env var don't trip SystemExit(2). **Changing
either default requires reviewing assert_startup_sane() callers**,
or the unset-env case flips to SystemExit(2) and every existing
test's ``create_app()`` fixture aborts at collection time.
``test_feature_flag_defaults_compose_to_sane_startup`` encodes
this invariant directly so a future default change reds the test
at the moment of the change.

**Asymmetric parsing** (intentional — written down because it
looks inconsistent at first glance):
  - ``MEMPALACE_MODE`` is permissive-on-whitespace
    (``.strip().lower()``) and case-insensitive. Operator-rotated
    rollout knob; paste-from-docs patterns should not flip
    ``MempalaceMode(" off".lower())`` into an uncaught
    ``ValueError``. Landed post-M0.13 review catch: original
    plan-verbatim version used only ``.lower()`` without
    ``.strip()``, inconsistent with LEGACY_MEMORY's parser.
  - ``LEGACY_MEMORY`` is permissive (``.strip().lower()``). Operators
    rotate this knob; realistic deployment patterns include
    config-doc paste with stray whitespace or shell-history recall
    with mixed case. Permissive parsing prevents silent memory-off
    from a misconfigured-but-intended-on value.
  - ``XELASPHERE_POST_P4`` is strict (``== "1"``). This is a one-way
    gate enabling DANGEROUS behavior (both memory systems off
    simultaneously). Strict comparison refuses near-miss values
    (``true``, ``yes``, ``True``) so an operator typo doesn't
    accidentally enable the escape hatch. Fail-loud on typos >
    fail-silent on operator convenience.

**SystemExit(2) vs custom-exception (deviation-consideration).**
``assert_startup_sane`` raises ``SystemExit(2)`` directly rather
than a domain exception the caller catches. Plan-verbatim;
existing tests verified safe against regression (no test sets
``LEGACY_MEMORY=off`` without also setting ``MEMPALACE_MODE``).
Alternative design (``raise MemPalaceStartupError`` + caller-
does-exit) kept in reserve if a future test harness needs the
function to not kill the process directly.
"""

from __future__ import annotations

import enum
import logging
import os


log = logging.getLogger(__name__)


class MempalaceMode(str, enum.Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


def mode() -> MempalaceMode:
    """Current MemPalace rollout mode (off / shadow / active).

    Read from env EVERY CALL (not cached at module-import). This
    is load-bearing for the test harness — ``monkeypatch.setenv``
    between calls must be observed. A module-level constant
    (``MODE = MempalaceMode(os.getenv(...))``) would capture env
    once at import time and the tests could not override it.

    Permissive-on-whitespace + case-insensitive parsing — see the
    asymmetric-parsing block in the module docstring.
    ``MEMPALACE_MODE=" off"`` (leading space from operator paste)
    normalizes to ``"off"`` and returns ``MempalaceMode.OFF``
    rather than raising an uncaught ValueError from the enum
    constructor.
    """
    return MempalaceMode(os.getenv("MEMPALACE_MODE", "off").strip().lower())


def legacy_memory_active() -> bool:
    """True iff the legacy memory_v3 subsystem is active.

    Defaults to True when ``LEGACY_MEMORY`` is unset — safe
    composition with the default ``mode() == OFF`` per the
    invariant named in the module docstring.

    Permissive parsing: ``.strip()`` removes operator-paste
    whitespace; ``.lower()`` handles mixed case. Recognized truthy
    values: ``on``, ``true``, ``1``, ``yes`` (case-insensitive,
    whitespace-trimmed). Everything else — including empty string
    — returns False. Empty-string semantics are "operator set the
    var but the value is blank," which is more likely
    misconfiguration than intent-to-be-on; coupled with
    assert_startup_sane, misconfigured deploys refuse to boot
    rather than silently losing memory.
    """
    value = os.getenv("LEGACY_MEMORY", "on").strip().lower()
    return value in ("on", "true", "1", "yes")


def _is_post_p4() -> bool:
    """Strict ``== "1"`` comparison — see module docstring on
    asymmetric parsing. Any other value (unset, ``true``, ``0``)
    treats as pre-P4.
    """
    return os.getenv("XELASPHERE_POST_P4") == "1"


def assert_startup_sane() -> None:
    """Refuse to boot if the flag combination would leave users
    with no memory subsystem at all.

    Decision tree:
      - post-P4 AND mode == OFF → SAFE with WARNING log. The
        warning fires whenever mempalace is explicitly off in
        post-P4 environments, regardless of legacy state —
        operator-notification that mempalace is currently
        disabled. Operator-visible in journald so a debugger
        investigating "why no mempalace" sees the state at app
        startup.
      - mode != OFF OR legacy_memory_active() == True → SAFE;
        return silently. At least one memory subsystem is active;
        no notification needed.
      - mode == OFF AND legacy == off AND NOT post-P4 → CRITICAL
        log + SystemExit(2). Pre-P4 data-loss scenario the sanity
        check exists for.

    **Post-P4 warning semantic (locked at M0.13 Stage 3 doctrine
    hit #6).** Plan-verbatim implementation initially only warned
    when BOTH mode=OFF AND legacy=off; the plan-verbatim test
    expected the warning whenever post-P4 + mode=OFF. Test's
    broader semantic adopted here because operator-visibility of
    "mempalace is off" matters more than narrowness of the
    "globally off" category. See deltas entry for M0.13 hit #6
    — plan-sketch internal inconsistency between test and impl
    halves, caught at Stage 3 by the test suite running against
    the implementation.
    """
    m = mode()
    legacy_on = legacy_memory_active()

    # Post-P4 escape hatch: any mode=OFF produces an operator-
    # notification warning and proceeds. Legacy-on-or-off doesn't
    # affect the warning — post-P4 means the operator accepted
    # the flag combination; the warning is just audit trail.
    if _is_post_p4() and m == MempalaceMode.OFF:
        legacy_note = (
            "LEGACY_MEMORY=on (legacy memory still active)"
            if legacy_on
            else "LEGACY_MEMORY=off (memory globally disabled)"
        )
        log.warning(
            "MEMPALACE_MODE=off (post-P4 environment). MemPalace "
            "adapter is disabled; %s.",
            legacy_note,
        )
        return

    # Non-post-P4 path: at least one of mode / legacy must be active.
    if not (m == MempalaceMode.OFF and not legacy_on):
        return

    log.critical(
        "Refusing to boot: MEMPALACE_MODE=off AND LEGACY_MEMORY=off "
        "(pre-P4). This would leave users with no memory at all. "
        "Set MEMPALACE_MODE=shadow|active or LEGACY_MEMORY=on. "
        "Post-P4: set XELASPHERE_POST_P4=1 to allow this state."
    )
    raise SystemExit(2)


__all__ = [
    "MempalaceMode",
    "mode",
    "legacy_memory_active",
    "assert_startup_sane",
]

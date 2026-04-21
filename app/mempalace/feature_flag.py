"""Feature-flag + startup-sanity check for the MemPalace adapter (M0.13).

Flask-side counterpart to the sidecar's readiness protocol. Two
env-var-driven predicates + a boot-time sanity check that refuses
to start the app if the combined flag state would leave users
with no memory subsystem at all.

**Stage 1 RED stub.** Each function raises NotImplementedError.
Stage 3 GREEN lands the real bodies + wires assert_startup_sane
into app/__init__.py's create_app() after config load.
"""

from __future__ import annotations

import enum


class MempalaceMode(str, enum.Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


# Defaults note — load-bearing invariant:
#   mode() defaults to OFF.
#   legacy_memory_active() defaults to True.
#   assert_startup_sane() passes iff NOT (mode==OFF AND legacy=False).
# Composition: unset-env is OFF + True = SAFE (existing Flask tests
# that don't set either env var don't trip SystemExit).
# **Changing either default requires reviewing assert_startup_sane()
# callers**, or the unset-env case flips to SystemExit(2) and every
# existing test collection aborts at import.
# ``test_feature_flag_defaults_compose_to_sane_startup`` encodes this
# invariant directly so a future default change reds the test at
# the moment of the change, not at the moment some downstream test
# happens to notice.


def mode() -> MempalaceMode:
    """Current MemPalace rollout mode (off / shadow / active)."""
    raise NotImplementedError(
        "mode() lands in M0.13 Stage 3 GREEN"
    )


def legacy_memory_active() -> bool:
    """True iff the legacy memory_v3 subsystem is active (default on)."""
    raise NotImplementedError(
        "legacy_memory_active() lands in M0.13 Stage 3 GREEN"
    )


def assert_startup_sane() -> None:
    """Refuse to boot if the flag combination would leave users with
    no memory subsystem at all (mode=OFF AND legacy=off AND not
    post-P4).
    """
    raise NotImplementedError(
        "assert_startup_sane() lands in M0.13 Stage 3 GREEN"
    )


__all__ = [
    "MempalaceMode",
    "mode",
    "legacy_memory_active",
    "assert_startup_sane",
]

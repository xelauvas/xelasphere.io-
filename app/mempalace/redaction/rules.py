"""Redaction rules module — Stage 1 RED scaffold.

Implements plan sketch at `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 3861-3980 (Task M0.16) — Flask-side redaction pipeline for API keys /
credit cards / JWT / SSN.

**Stage 1 RED shape.** `load_rules` has real implementation (needed for
tests to collect against CORPUS + RULES module-level globals). `scrub` is
stubbed to return the empty string — both positive tests (assert marker in
scrub-output) and negative tests (assert scrub-output == input) fail cleanly
under the stub. `_luhn` raises NotImplementedError — Luhn tests error out.
Mixed RED shape per template Part 2 #5 discipline (functionality-missing is
the target; AssertionError on empty-stub + NotImplementedError on raise are
both in the class).

Stage 3 GREEN lands real scrub + real Luhn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml


def _luhn(s: str) -> bool:
    """Luhn checksum validator for credit card number candidates.

    Stage 1 RED: raises NotImplementedError. Stage 3 GREEN implements
    the standard Luhn algorithm (double every second digit from right,
    subtract 9 if > 9, sum all, mod 10 must equal 0).
    """
    raise NotImplementedError("_luhn: Stage 3 GREEN lands behavior")


VALIDATORS: dict[str, Callable[[str], bool]] = {"luhn": _luhn}


def load_rules(path: Path) -> list[dict]:
    """Load rules from the YAML file at `path`.

    Real implementation at Stage 1 — tests need this to collect against
    the RULES module-level global. Raises yaml.YAMLError on malformed
    input (unchanged from PyYAML default).
    """
    return yaml.safe_load(Path(path).read_text())["rules"]


def scrub(text: str, rules: list[dict], metrics=None) -> str:
    """Scrub `text` by applying each rule in order.

    Stage 1 RED stub: returns empty string. Causes positive tests
    (assert marker in out) and negative tests (assert out == text) to
    fail cleanly under stub.

    Stage 3 GREEN implements the real scrub loop per plan lines
    3929-3941: for each rule, re.compile(pattern).sub with metric-hook +
    validator check if configured.
    """
    return ""

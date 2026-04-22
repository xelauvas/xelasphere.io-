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

import re
from pathlib import Path
from typing import Callable

import yaml


def _luhn(s: str) -> bool:
    """Luhn checksum validator for credit card number candidates.

    Standard algorithm: double every second digit from the right (i.e.,
    positions 1, 3, 5, ... under zero-indexed reversed iteration); subtract
    9 if the doubled value exceeds 9; sum all digits; pass if sum % 10 == 0.

    Minimum length is 13 digits (Visa 13/16, MasterCard 16, Amex 15, Discover
    16, etc.); shorter digit sequences are rejected without attempting the
    checksum. Non-digit characters are filtered via str.isdigit() before the
    length check.

    Verified against 4532015112830366 (Luhn-valid): sum 50, 50 % 10 == 0.
    """
    digits = [int(d) for d in s if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


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

    For each rule, compile the regex pattern and substitute matches with
    the rule's `replace` marker. If the rule declares a validator (e.g.,
    `luhn` for credit_card_luhn), run the validator on the match first;
    return the match unchanged if the validator rejects it (Luhn-invalid
    digit sequences pass through untouched).

    When `metrics` is provided, call `metrics.redaction_hit(rule["name"])`
    on every rule-match that passes validation — one increment per match,
    not per rule, not per scrub call.

    Rule order is significant: each rule processes the output of prior
    rules. Per plan-sketch intent, rules with more-specific prefixes
    (anthropic_key, aws_access_key_id) should precede more-general ones
    (openai_key) in rules.yaml so specific markers land first.
    """
    out = text
    for rule in rules:
        pat = re.compile(rule["pattern"])
        validator = VALIDATORS.get(rule.get("validator"))

        def _replace(m, _rule=rule, _validator=validator):
            if _validator is not None and not _validator(m.group(0)):
                return m.group(0)
            if metrics is not None:
                metrics.redaction_hit(_rule["name"])
            return _rule["replace"]

        out = pat.sub(_replace, out)
    return out

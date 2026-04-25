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
    # M4 fix: narrow to ASCII-only. `str.isdigit()` accepts Unicode digits
    # (Devanagari, Arabic-Indic, fullwidth, etc.) which the Luhn algorithm
    # is not defined over; narrow to the specific decimal-digit char set.
    digits = [int(d) for d in s if d in "0123456789"]
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

    Validates schema: each rule dict must have `name`, `pattern`, and
    `replace` keys; `validator` is optional. Raises ValueError at load
    time with a clear message if the schema is violated — catches
    malformed rule edits at load rather than at first-use deep inside
    scrub() (M0.16 Stage 4c fold for M5).

    Raises yaml.YAMLError on malformed YAML input (unchanged from
    PyYAML default).
    """
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict) or "rules" not in data or not isinstance(data["rules"], list):
        raise ValueError(
            f"Malformed rules YAML at {path}: expected top-level 'rules' key "
            f"mapping to a list; got {type(data).__name__}"
        )
    for i, rule in enumerate(data["rules"]):
        if not isinstance(rule, dict):
            raise ValueError(
                f"Malformed rule at index {i} in {path}: expected dict, got {type(rule).__name__}"
            )
        for key in ("name", "pattern", "replace"):
            if key not in rule:
                raise ValueError(
                    f"Malformed rule at index {i} in {path}: missing required key '{key}'. "
                    f"Got keys: {sorted(rule.keys())}"
                )
    return data["rules"]


def scrub(text: str, rules: list[dict], metrics=None) -> str:
    """Scrub `text` by applying each rule in order.

    For each rule, compile the regex pattern and substitute matches with
    the rule's `replace` marker. If the rule declares a validator (e.g.,
    `luhn` for credit_card_luhn), run the validator on the match first.

    **Validator-reject retry-shorter semantics.** When a rule has a
    validator and the validator rejects the full greedy match, scrub
    retries successively shorter prefixes of the match. If any prefix
    passes the validator, that prefix is redacted and any trailing chars
    of the original match are preserved (they may still be matched by
    subsequent rules). This handles the greedy-quantifier escape path
    where a valid-by-validator prefix is contained within an invalid-by-
    validator superstring (I1 from M0.16 Stage 4c review). Without
    retry, `re.sub` advances past the rejected match and the valid
    prefix leaks.

    **Known limitation.** Retry scans prefixes only, not all substrings.
    A valid-by-validator substring in the middle of a longer invalid
    match (not at the match prefix) still leaks. This is accepted for
    M0.16; if production traffic surfaces middle-substring adversarial
    inputs, a substring-scan version can replace prefix-retry.

    When `metrics` is provided, call `metrics.redaction_hit(rule["name"])`
    on every rule-match that passes validation — one increment per
    successful redaction, not per rule, not per scrub call.

    Rule order is significant: each rule processes the output of prior
    rules.
    """
    out = text
    for rule in rules:
        pat = re.compile(rule["pattern"])
        validator = VALIDATORS.get(rule.get("validator"))

        if validator is None:
            # No validator: straightforward substitution.
            def _replace(m, _rule=rule):
                if metrics is not None:
                    metrics.redaction_hit(_rule["name"])
                return _rule["replace"]
            out = pat.sub(_replace, out)
        else:
            # Validator-gated: retry shorter prefixes on validator reject.
            result: list[str] = []
            last_end = 0
            for m in pat.finditer(out):
                result.append(out[last_end:m.start()])
                match_text = m.group(0)
                redacted = False
                for length in range(len(match_text), 0, -1):
                    if validator(match_text[:length]):
                        if metrics is not None:
                            metrics.redaction_hit(rule["name"])
                        result.append(rule["replace"])
                        result.append(match_text[length:])
                        redacted = True
                        break
                if not redacted:
                    result.append(match_text)
                last_end = m.end()
            result.append(out[last_end:])
            out = "".join(result)
    return out

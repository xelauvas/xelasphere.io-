"""M0.16 Stage 1 RED: tests for redaction rules (Task M0.16).

Stage 1 RED state:
  - `load_rules` is real (needed for module-level RULES global).
  - `scrub` is stubbed — returns empty string. Positive tests (assert
    marker in out) and negative tests (assert out == input) both fail
    cleanly under stub.
  - `_luhn` raises NotImplementedError. Luhn tests error out.

Expected RED shape (mixed per Part 2 #5 discipline):
  - AssertionError on corpus tests (stub returns "").
  - NotImplementedError on luhn tests (stub raises).

Both shapes are functionality-missing RED.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.mempalace.redaction.rules import _luhn, load_rules, scrub

RULES = load_rules(Path(__file__).parents[1] / "redaction_rules.yaml")
CORPUS = yaml.safe_load(
    (Path(__file__).parent / "redaction_corpus.yaml").read_text()
)


@pytest.mark.parametrize("case", CORPUS["positives"])
def test_positives_redacted(case):
    """Every positive case should contain its declared redaction_marker
    after scrub applies the matching rule."""
    out = scrub(case["text"], RULES)
    assert case["redaction_marker"] in out, (
        f"positive case text {case['text']!r} should contain marker "
        f"{case['redaction_marker']!r} after scrub; got {out!r}"
    )


@pytest.mark.parametrize("case", CORPUS["negatives"])
def test_negatives_preserved(case):
    """Negative cases should pass through scrub unchanged — no rule matches."""
    out = scrub(case["text"], RULES)
    assert out == case["text"], (
        f"negative case text {case['text']!r} should be preserved by scrub; "
        f"got {out!r}"
    )


# Luhn validator unit tests.

@pytest.mark.parametrize("number", [
    "4111111111111111",   # Visa test
    "5555555555554444",   # MasterCard test
    "378282246310005",    # Amex test (15 digits)
])
def test_luhn_valid_credit_cards(number):
    """Known-valid test-card numbers from the PCI test-card reference set
    should pass Luhn validation."""
    assert _luhn(number) is True


@pytest.mark.parametrize("number", [
    "1111111111111111",   # all-ones, Luhn-invalid
    "4111111111111112",   # Visa test with wrong check digit
    "12345678901234567",  # 17-digit Luhn-invalid
])
def test_luhn_invalid_credit_cards(number):
    """Numbers that look like card numbers but fail the Luhn checksum."""
    assert _luhn(number) is False


@pytest.mark.parametrize("value", [
    "",
    "abc",
    "123",  # under the 13-digit minimum
])
def test_luhn_non_digit_or_too_short(value):
    """Empty strings, non-digit inputs, and too-short digit sequences
    should fail Luhn validation (return False, not raise)."""
    assert _luhn(value) is False


def test_load_rules_returns_list_of_dicts():
    """load_rules returns a list of rule dicts matching the rules.yaml
    structure — each rule has name, pattern, replace keys at minimum."""
    rules = load_rules(Path(__file__).parents[1] / "redaction_rules.yaml")
    assert isinstance(rules, list)
    assert len(rules) >= 9  # expected 9 rule families at M0.16
    for rule in rules:
        assert "name" in rule
        assert "pattern" in rule
        assert "replace" in rule

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
    "4222222222222",       # 13-digit Visa short-format (M3 fold: range endpoint)
    "30569309025904",      # 14-digit Diners Club test (M3 fold: range coverage)
    "378282246310005",     # 15-digit Amex test
    "4111111111111111",    # 16-digit Visa test
    "5555555555554444",    # 16-digit MasterCard test
    "123456789012345671",  # 18-digit Luhn-valid constructed (M3 fold)
    "1234567890123456710", # 19-digit Luhn-valid constructed (M3 fold: range endpoint)
])
def test_luhn_valid_credit_cards(number):
    """Known-valid test-card numbers from the PCI test-card reference set
    PLUS constructed Luhn-valid cases covering the full {13,19} digit
    range per M0.16 Stage 4c review finding M3 (range endpoints untested).
    """
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


# Adversarial tests for I1 (M0.16 Stage 4c review finding): greedy
# quantifier {13,19} + validator-reject-without-retry escape path.
# If scrub's pattern matches a Luhn-invalid superstring that contains a
# Luhn-valid prefix, the valid prefix must be redacted. Current behavior
# leaks the valid prefix because `re.sub` doesn't retry shorter lengths
# when the validator rejects the full match.

@pytest.mark.parametrize("text, valid_prefix_len", [
    # 14-digit Luhn-invalid with 13-digit Luhn-valid prefix "4222222222222".
    ("42222222222229", 13),
    # 17-digit Luhn-invalid with 16-digit Luhn-valid prefix "4532015112830366".
    ("45320151128303669", 16),
])
def test_cc_adversarial_greedy_rejects_but_prefix_is_valid(text, valid_prefix_len):
    """I1 adversarial: greedy {13,19} matches full digit run; if validator
    rejects, scrub must retry shorter prefixes. The Luhn-valid prefix
    within the Luhn-invalid superstring should be redacted.
    """
    out = scrub(text, RULES)
    assert "[REDACTED:cc]" in out, (
        f"adversarial text {text!r} (length {len(text)}) contains a "
        f"Luhn-valid {valid_prefix_len}-digit prefix; scrub should retry "
        f"shorter on validator reject and redact the prefix. Got: {out!r}"
    )


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


def test_load_rules_raises_on_missing_required_key(tmp_path):
    """M0.16 Stage 4c fold (M5): load_rules must raise ValueError at load
    time when a rule is missing one of the required keys (name / pattern /
    replace), not silently pass through to KeyError at first use inside
    scrub."""
    malformed_yaml = tmp_path / "malformed.yaml"
    malformed_yaml.write_text(
        "rules:\n"
        "  - name: ok_rule\n"
        "    pattern: \"abc\"\n"
        "    replace: \"[REDACTED:ok]\"\n"
        "  - name: broken_rule\n"
        # missing 'pattern' key
        "    replace: \"[REDACTED:broken]\"\n"
    )
    with pytest.raises(ValueError, match="missing required key 'pattern'"):
        load_rules(malformed_yaml)


def test_load_rules_raises_on_missing_rules_key(tmp_path):
    """M0.16 Stage 4c fold (M5): load_rules must raise ValueError when
    the top-level 'rules' key is absent or maps to a non-list."""
    malformed_yaml = tmp_path / "no_rules_key.yaml"
    malformed_yaml.write_text("not_rules: []\n")
    with pytest.raises(ValueError, match="expected top-level 'rules' key"):
        load_rules(malformed_yaml)

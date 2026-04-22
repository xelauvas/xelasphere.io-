# Redaction feedback loop

The `redaction/` module scrubs palace turn text for secrets and PII before ingest. Rules live in `redaction_rules.yaml`; positive + negative test strings live in `tests/redaction_corpus.yaml`. The module grows via a four-step feedback loop as new leak shapes surface in production.

## The four-step loop

### 1. Collect

When a leaked secret is found (in a palace, in logs, in debugging, in a security report), **anonymize** the leaked value before recording it. Replace the sensitive characters with clearly-non-real alternatives that preserve the structure the regex cares about (prefix, character class, length). For example, a leaked OpenAI key like `sk-realkey12345...` becomes `sk-fake0000000000000000000` — same prefix, same character class, same length, zero real-secret content.

Add the anonymized example to `tests/redaction_corpus.yaml` under `positives`:

```yaml
- text: "found in a log: sk-fake0000000000000000000 end"
  redaction_marker: "[REDACTED:openai_key]"
```

If the leaked shape is a false-positive candidate (looks like a secret but isn't), add to `negatives`:

```yaml
- text: "the variable openai_key holds the API key"
```

### 2. Rule

Add or update the corresponding pattern in `redaction_rules.yaml`:

```yaml
- name: openai_key
  pattern: "sk-[A-Za-z0-9]{20,}"
  replace: "[REDACTED:openai_key]"
```

Each rule has `name`, `pattern`, `replace`, and optionally `validator` (for checksum-gated rules like `credit_card_luhn`). Load-time schema validation is enforced in `rules.py::load_rules`.

**Rule-name stability.** Once a rule name ships, it becomes a stable identifier in three places:
- Metric label value: `xelasphere_mempalace_redaction_hits_total{rule="<name>"}` (spec §8.1).
- Redaction marker in text: `[REDACTED:<name>]`.
- Operator dashboards and alerts that reference the above (spec §8.2 `MempalaceRedactionBroken`).

Renaming a rule requires coordinated changes to any downstream consumer that relies on the label. Prefer adding a new rule with a new name (and deprecating the old one if the shape changes) over renames.

### 3. Test

```
pytest app/mempalace/redaction/tests/
```

Verify the positive case redacts AND that every existing negative remains intact. Every positive must contain its declared marker after `scrub()`; every negative must be returned unchanged.

Corpus entries are parametrized across `test_positives_redacted` and `test_negatives_preserved`, so each entry becomes its own test case; a corpus gap shows up as a failing case, and a corpus mistake (positive that doesn't match its rule, negative that incorrectly matches) shows up as a failing case with the specific input visible in the assertion message.

### 4. Ship

Green CI → merge. The metric `xelasphere_mempalace_redaction_hits_total{rule="<new>"}` should begin incrementing on real production traffic within hours of merge. Prolonged zero-rate across all rules is detected by the `MempalaceRedactionBroken` alert (spec §8.2) — `rate(redaction_hits_total[24h]) == 0 for 24h AND rate(ingest_total[24h]) > 0`.

## Corpus growth target

Spec §6 Testing strategy (line 602) names `redaction corpus (500 strings, 0 leaks)` as a security invariant. M0.16 ships ~50 strings as the starting point; the corpus grows via this feedback loop plus periodic adversarial review as production surfaces new leak shapes. The gap between starting corpus and invariant target is expected and explicit — corpus growth is a continuous process, not a one-time deliverable.

## Validator semantics

Rules that declare a `validator` (currently only `credit_card_luhn`) gate substitution behind the validator function. If the validator rejects the full greedy match, `scrub()` retries successively shorter prefixes until one passes or no length validates. This handles the greedy-quantifier escape path where a valid-by-validator prefix is contained within an invalid-by-validator superstring.

**Known limitation.** Retry scans prefixes only, not all substrings. A valid-by-validator substring in the middle of a longer invalid match (not at the match prefix) still leaks. If production traffic surfaces middle-substring adversarial inputs, a substring-scan version can replace prefix-retry.

## Adding a new validator

To add a new validator (e.g., checksum for a different identifier type):

1. Implement as a function `def _my_validator(s: str) -> bool` in `rules.py`.
2. Register in the `VALIDATORS` module-level dict: `VALIDATORS["my_name"] = _my_validator`.
3. Reference in `redaction_rules.yaml` via `validator: my_name`.

Validators must return `True` for "redact this match" and `False` for "leave this match unchanged."

## Who reviews changes

All changes to `redaction_rules.yaml` and `redaction_corpus.yaml` go through standard code review. Changes to regex patterns require adversarial-test cases in the corpus that exercise the edge of the pattern (truncated forms, case variants, boundary inputs). Changes that introduce new rule names should include a note in the PR description about metric-label cardinality (each new rule name adds a label value to `redaction_hits_total`; cardinality stays bounded by total rule count, which is low).

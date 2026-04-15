"""W-10 — shape guards for the eval fixture file.

These run on every `pytest` invocation (not marked `eval`) so broken
fixture files fail CI immediately, before the slow LLM-backed runner
even attempts them.
"""
import json
from pathlib import Path

import pytest

from app.services.chat_engine import ALLOWED_LAYOUTS


FIXTURE_PATH = (
    Path(__file__).resolve().parents[0] / "scaffold_v2_fixtures.jsonl"
)


def _load_fixtures():
    if not FIXTURE_PATH.exists():
        pytest.fail(f"Fixture file missing: {FIXTURE_PATH}")
    entries = []
    for line_no, line in enumerate(FIXTURE_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entries.append((line_no, json.loads(line)))
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON at line {line_no}: {e}")
    return entries


def test_fixture_file_has_at_least_54_entries():
    entries = _load_fixtures()
    assert len(entries) >= 54, (
        f"Expected ≥54 entries (14 v1 + 40 hand-curated), got {len(entries)}."
    )


def test_every_entry_has_required_keys():
    required = {"id", "prompt", "expected_layout", "tags"}
    for line_no, entry in _load_fixtures():
        missing = required - set(entry.keys())
        assert not missing, (
            f"Line {line_no} id={entry.get('id')!r}: missing keys {missing}"
        )


def test_expected_layouts_are_valid():
    allowed = set(ALLOWED_LAYOUTS)
    for line_no, entry in _load_fixtures():
        expected = entry["expected_layout"]
        candidates = [expected] if isinstance(expected, str) else expected
        bad = set(candidates) - allowed
        assert not bad, (
            f"Line {line_no} id={entry['id']!r}: invalid layout(s) {bad}. "
            f"Allowed: {allowed}"
        )


def test_no_duplicate_ids():
    ids = [e["id"] for _, e in _load_fixtures()]
    seen, dups = set(), set()
    for i in ids:
        (dups if i in seen else seen).add(i)
    assert not dups, f"Duplicate fixture ids: {dups}"


def test_no_duplicate_prompts():
    prompts = [e["prompt"].strip().lower() for _, e in _load_fixtures()]
    seen, dups = set(), set()
    for p in prompts:
        (dups if p in seen else seen).add(p)
    assert not dups, f"Duplicate prompts (case-insensitive): {list(dups)[:3]}..."


def test_section_count_bounds_coherent():
    """If min/max section counts are set, min ≤ max and both positive."""
    for line_no, entry in _load_fixtures():
        lo = entry.get("expected_section_count_min")
        hi = entry.get("expected_section_count_max")
        if lo is None and hi is None:
            continue
        assert lo is not None and hi is not None, (
            f"Line {line_no} id={entry['id']!r}: set both section count bounds or neither."
        )
        assert 1 <= lo <= hi <= 10, (
            f"Line {line_no} id={entry['id']!r}: bounds {lo}-{hi} out of range."
        )


def test_structured_entries_have_section_bounds():
    """Layouts that imply sections must declare expected section counts so
    the runner can catch 'structured with 0 sections' regressions."""
    for line_no, entry in _load_fixtures():
        expected = entry["expected_layout"]
        candidates = [expected] if isinstance(expected, str) else expected
        if "structured" in candidates:
            assert entry.get("expected_section_count_min") is not None, (
                f"Line {line_no} id={entry['id']!r}: structured layout must "
                f"declare expected_section_count_min."
            )


def test_every_layout_has_at_least_one_fixture():
    """Coverage: each allowed layout must appear at least once in the seed."""
    from collections import Counter
    layout_counts: Counter = Counter()
    for _, entry in _load_fixtures():
        expected = entry["expected_layout"]
        for c in ([expected] if isinstance(expected, str) else expected):
            layout_counts[c] += 1
    missing = set(ALLOWED_LAYOUTS) - set(layout_counts)
    assert not missing, (
        f"Layouts with zero fixture coverage: {missing}. "
        f"Counts: {dict(layout_counts)}"
    )

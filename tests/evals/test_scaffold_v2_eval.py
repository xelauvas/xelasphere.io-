"""W-11 — Scaffold v2 layout-accuracy eval runner.

Gated: runs only under `pytest -m eval`. Requires OPENAI_API_KEY or GROQ_API_KEY.

Loads tests/evals/scaffold_v2_fixtures.jsonl, calls classify_user_intent()
on each prompt, scores layout accuracy. Writes a per-run report to
docs/superpowers/evals/{YYYY-MM-DD}-{sha}.json and appends a single
summary row to history.jsonl. Fails when accuracy drops below
EVAL_ACCURACY_FLOOR (set in W-12).

Spec: §6.5
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest


FIXTURE_PATH = Path(__file__).resolve().parent / "scaffold_v2_fixtures.jsonl"
RESULTS_DIR = (
    Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "evals"
)
HISTORY_PATH = RESULTS_DIR / "history.jsonl"
LATEST_PATH = RESULTS_DIR / "latest.json"


# Baseline measured on 122-prompt fixture (W-10 seed + 66 hand-curated
# weak-pattern cases from W-13-alt path). Three clean runs: 56.6%, 58.2%,
# 59.0%. Worst-case baseline 56.6%. Floor = 56.6% - 3 pp noise tolerance
# = 53.6%.
#
# Note on the drop from the 54-prompt baseline (67.9%): the added 66
# prompts deliberately target the three weakest v2.0 miss patterns —
# prose/conversational boundary, multi-angle explainers that should hit
# structured, technical/code-heavy boundary. The eval is now more
# discriminating, not the classifier worse. Classifier prompt tuning
# against these patterns is a v2.1 item, gated on compliance-audit data.
#
# PRs that drop below this floor must either (a) fix the regression
# before merge or (b) justify a new baseline in the PR description.
EVAL_ACCURACY_FLOOR: float = 0.536


def _load_fixtures() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
        ).decode().strip()
    except Exception:
        return "nogit"


def _layout_ok(got: str, expected) -> bool:
    candidates = [expected] if isinstance(expected, str) else list(expected)
    return got in candidates


def _section_count_ok(n: int, entry: Dict[str, Any]) -> bool:
    lo = entry.get("expected_section_count_min")
    hi = entry.get("expected_section_count_max")
    if lo is None or hi is None:
        return True
    return lo <= n <= hi


def _run_eval() -> Dict[str, Any]:
    from app.services import chat_engine
    chat_engine._intent_cache_v2.clear()

    fixtures = _load_fixtures()
    per_prompt: List[Dict[str, Any]] = []
    per_layout_hits: Dict[str, int] = {}
    per_layout_total: Dict[str, int] = {}

    for fx in fixtures:
        try:
            env = chat_engine.classify_user_intent(fx["prompt"], use_cache=False)
        except Exception as e:
            per_prompt.append({
                "id": fx["id"], "ok": False, "error": str(e),
                "got": None, "expected": fx["expected_layout"],
            })
            continue
        scaffold = (env or {}).get("scaffold") or {}
        layout = scaffold.get("layout", "unknown")
        layout_match = _layout_ok(layout, fx["expected_layout"])
        sections_match = True
        if layout_match and "expected_section_count_min" in fx:
            sections_match = _section_count_ok(
                len(scaffold.get("sections") or []), fx,
            )
        ok = layout_match and sections_match

        expected_str = (
            fx["expected_layout"]
            if isinstance(fx["expected_layout"], str)
            else "|".join(fx["expected_layout"])
        )
        per_layout_total[expected_str] = per_layout_total.get(expected_str, 0) + 1
        if ok:
            per_layout_hits[expected_str] = per_layout_hits.get(expected_str, 0) + 1

        per_prompt.append({
            "id": fx["id"],
            "ok": ok,
            "got": layout,
            "expected": fx["expected_layout"],
            "sections_got": len(scaffold.get("sections") or []),
            "layout_match": layout_match,
            "sections_match": sections_match,
            "intent": env.get("intent_type") if env else None,
        })

    hits = sum(1 for p in per_prompt if p["ok"])
    accuracy = hits / len(per_prompt) if per_prompt else 0.0

    summary = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sha": _git_sha(),
        "total": len(per_prompt),
        "hits": hits,
        "accuracy": accuracy,
        "per_layout_accuracy": {
            k: per_layout_hits.get(k, 0) / per_layout_total[k]
            for k in per_layout_total
        },
        "per_layout_counts": per_layout_total,
    }
    return {"summary": summary, "per_prompt": per_prompt}


def _write_results(report: Dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    sha = report["summary"]["sha"]
    fname = RESULTS_DIR / f"{date}-{sha}.json"
    fname.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    LATEST_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report["summary"]) + "\n")


# ============================================================
# Public tests
# ============================================================


@pytest.mark.eval
@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="Eval requires at least one LLM key (OPENAI_API_KEY or GROQ_API_KEY).",
)
def test_scaffold_layout_accuracy():
    report = _run_eval()
    _write_results(report)
    acc = report["summary"]["accuracy"]
    misses = [p for p in report["per_prompt"] if not p["ok"]]
    print(f"\nEVAL ACCURACY: {acc:.1%} ({report['summary']['hits']}/{report['summary']['total']})")
    print(f"Floor: {EVAL_ACCURACY_FLOOR:.1%}")
    if misses:
        print(f"Misses ({len(misses)}):")
        for m in misses[:20]:
            print(f"  {m['id']}: expected={m['expected']} got={m.get('got')}  sections={m.get('sections_got')}")
    assert acc >= EVAL_ACCURACY_FLOOR, (
        f"Eval accuracy {acc:.1%} below floor {EVAL_ACCURACY_FLOOR:.1%}. "
        f"Results written to {LATEST_PATH}"
    )


# ============================================================
# Harness self-tests (run on default pytest, not marked eval)
# ============================================================


def test_runner_skips_when_no_api_keys(monkeypatch):
    """With no keys set, the eval test is skipped — never errors."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # Harness-level skipif is metadata — verify we can still load fixtures without keys:
    fixtures = _load_fixtures()
    assert len(fixtures) >= 54


def test_runner_writes_results_file(monkeypatch, tmp_path):
    """Mocked _run_eval → _write_results produces both dated file + history line."""
    fake_report = {
        "summary": {
            "ts": "2026-04-15T00:00:00+00:00",
            "sha": "abc123",
            "total": 10, "hits": 9, "accuracy": 0.9,
            "per_layout_accuracy": {"prose": 1.0},
            "per_layout_counts": {"prose": 10},
        },
        "per_prompt": [{"id": "x", "ok": True}],
    }
    monkeypatch.setattr(
        "tests.evals.test_scaffold_v2_eval.RESULTS_DIR", tmp_path,
    )
    monkeypatch.setattr(
        "tests.evals.test_scaffold_v2_eval.HISTORY_PATH", tmp_path / "history.jsonl",
    )
    monkeypatch.setattr(
        "tests.evals.test_scaffold_v2_eval.LATEST_PATH", tmp_path / "latest.json",
    )
    _write_results(fake_report)
    dated_files = list(tmp_path.glob("2026-04-15-abc123.json"))
    assert len(dated_files) == 1
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "history.jsonl").exists()
    history_line = (tmp_path / "history.jsonl").read_text().strip()
    assert json.loads(history_line)["sha"] == "abc123"


def test_layout_ok_accepts_list_of_acceptable_layouts():
    assert _layout_ok("prose", "prose") is True
    assert _layout_ok("prose", ["prose", "long-prose"]) is True
    assert _layout_ok("structured", ["prose", "long-prose"]) is False


def test_section_count_ok_boundary():
    entry = {"expected_section_count_min": 2, "expected_section_count_max": 5}
    assert _section_count_ok(2, entry) is True
    assert _section_count_ok(5, entry) is True
    assert _section_count_ok(1, entry) is False
    assert _section_count_ok(6, entry) is False


def test_section_count_ok_no_bounds_is_true():
    assert _section_count_ok(42, {}) is True

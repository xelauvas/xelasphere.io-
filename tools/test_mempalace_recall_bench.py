"""Tests for tools/mempalace-recall-bench scoring logic."""
from __future__ import annotations

import importlib.util
import importlib.machinery
from pathlib import Path

import pytest

# Load the script as a module — the script has no .py extension by
# convention (matches `tools/mempalace-rubric` style). Use an explicit
# SourceFileLoader so importlib doesn't refuse on the missing suffix.
_BENCH_PATH = Path(__file__).resolve().parent / "mempalace-recall-bench"
_loader = importlib.machinery.SourceFileLoader(
    "mempalace_recall_bench", str(_BENCH_PATH)
)
_spec = importlib.util.spec_from_loader("mempalace_recall_bench", _loader)
bench = importlib.util.module_from_spec(_spec)
_loader.exec_module(bench)


def _record(query: str, *, user_id: str = "u1", l2: list[dict] | None = None,
            l3: list[dict] | None = None, l0: list[dict] | None = None,
            l1: list[dict] | None = None, legacy: str = "") -> dict:
    return {
        "ts": 1.0,
        "user_id": user_id,
        "query": query,
        "legacy": legacy,
        "mempalace": {
            "l0_recent": l0 or [],
            "l1_session": l1 or [],
            "l2_topic": l2 or [],
            "l3_semantic": l3 or [],
        },
    }


def test_score_recall_query_truth_in_l2():
    rec = _record("when's my pitch", l2=[{"text": "pitch on May 22"}])
    scores = bench.score_recall_query(rec, "May")
    assert scores == {
        "legacy": False, "l0_recent": False, "l1_session": False,
        "l2_topic": True, "l3_semantic": False,
    }


def test_score_recall_query_truth_in_legacy_only():
    rec = _record("who's beta", legacy="beta user: Sarah")
    scores = bench.score_recall_query(rec, "Sarah")
    assert scores["legacy"] is True
    assert all(scores[t] is False for t in ["l0_recent", "l1_session", "l2_topic", "l3_semantic"])


def test_score_recall_query_case_insensitive():
    rec = _record("foot issue", l3=[{"text": "right-foot PLANTAR fasciitis"}])
    scores = bench.score_recall_query(rec, "plantar")
    assert scores["l3_semantic"] is True


def test_score_crosstopic_query_counts_project_keywords():
    rec = _record(
        "best NYC coffee shop",
        l2=[
            {"text": "Marathoners with ER physician partners"},  # 2 project keywords
            {"text": "Brooklyn has good coffee"},                # 0
            {"text": "Sarah said the lattes are great"},         # 1 (Sarah)
        ],
    )
    counts = bench.score_crosstopic_query(rec, project_keywords=["marathoner", "er physician", "sarah"])
    assert counts["l2_topic"] == (2, 3)


def test_find_query_match_returns_most_recent():
    records = [
        {"query": "What's my deadline first time", "ts": 100},
        {"query": "Other thing", "ts": 200},
        {"query": "What's my deadline second time", "ts": 300},
    ]
    match = bench._find_query_match(records, "deadline")
    assert match["ts"] == 300


def test_find_query_match_returns_none_when_no_match():
    records = [{"query": "something else", "ts": 100}]
    assert bench._find_query_match(records, "deadline") is None


def test_evaluate_persona_recall_pass_threshold():
    records = [
        _record("when's my deadline", l2=[{"text": "June 8 (birthday)"}]),
        _record("who's beta user", l2=[{"text": "Sarah is the beta user"}]),
        _record("what box am I deploying", l2=[{"text": "Hetzner box"}]),
        _record("what date launching", l2=[{"text": "June 8"}]),
    ]
    persona = {
        "user_id": "u1",
        "description": "test",
        "recall_queries": [
            {"query": "when's my deadline", "truth": "June 8", "label": "deadline"},
            {"query": "who's beta user", "truth": "Sarah", "label": "beta user"},
            {"query": "what box am I deploying", "truth": "Hetzner", "label": "box"},
            {"query": "what date launching", "truth": "June 8", "label": "launch"},
        ],
        "crosstopic_queries": [],
        "project_keywords": [],
    }
    result = bench.evaluate_persona(persona, records)
    assert result["n_recall_matched"] == 4
    assert result["recall_l2_or_l3"] == 1.0
    assert result["pass_recall"] is True


def test_evaluate_persona_recall_below_threshold():
    records = [
        _record("question 1", l2=[]),  # miss
        _record("question 2", l2=[{"text": "right answer"}]),  # hit
    ]
    persona = {
        "user_id": "u1",
        "description": "test",
        "recall_queries": [
            {"query": "question 1", "truth": "answer", "label": "q1"},
            {"query": "question 2", "truth": "answer", "label": "q2"},
        ],
        "crosstopic_queries": [],
        "project_keywords": [],
    }
    result = bench.evaluate_persona(persona, records)
    assert result["recall_l2_or_l3"] == 0.5
    assert result["pass_recall"] is False  # below 0.80


def test_evaluate_persona_crosstopic_pass_threshold():
    records = [
        _record(
            "book recommendation",
            l2=[{"text": "no project context"}, {"text": "another off-topic item"}],
            l3=[{"text": "still off-topic"}],
        ),
    ]
    persona = {
        "user_id": "u1",
        "description": "test",
        "recall_queries": [],
        "crosstopic_queries": [{"query": "book recommendation", "label": "book"}],
        "project_keywords": ["project_specific_word"],
    }
    result = bench.evaluate_persona(persona, records)
    assert result["crosstopic_l2_l3_avg"] == 0.0
    assert result["pass_crosstopic"] is True


def test_evaluate_persona_crosstopic_fail_threshold():
    records = [
        _record(
            "book recommendation",
            l2=[
                {"text": "Marathon training plan"},
                {"text": "Sarah says..."},
                {"text": "innocent topic"},
            ],
            l3=[
                {"text": "Boston Marathon prep"},
                {"text": "Asheville running scene"},
                {"text": "innocent topic"},
            ],
        ),
    ]
    persona = {
        "user_id": "u1",
        "description": "test",
        "recall_queries": [],
        "crosstopic_queries": [{"query": "book recommendation", "label": "book"}],
        "project_keywords": ["marathon", "sarah", "boston", "asheville"],
    }
    result = bench.evaluate_persona(persona, records)
    # l2: 2/3 = 0.667, l3: 2/3 = 0.667, avg = 0.667
    assert result["crosstopic_l2_l3_avg"] == pytest.approx(2/3)
    assert result["pass_crosstopic"] is False  # above 0.40


def test_evaluate_persona_no_matched_records_returns_none_pass():
    persona = {
        "user_id": "no-such-user",
        "description": "missing",
        "recall_queries": [{"query": "anything", "truth": "x", "label": "q"}],
        "crosstopic_queries": [],
        "project_keywords": [],
    }
    result = bench.evaluate_persona(persona, [])
    assert result["n_recall_matched"] == 0
    assert result["pass_recall"] is None  # no data → no judgment
    assert result["pass_crosstopic"] is None


def test_overall_pass_requires_at_least_one_persona_with_data():
    """If every persona is no-data, overall_pass is False (nothing to verify)."""
    evaluations = [
        {"pass_recall": None, "pass_crosstopic": None},
        {"pass_recall": None, "pass_crosstopic": None},
    ]
    assert bench.overall_pass(evaluations) is False


def test_overall_pass_succeeds_when_data_present_and_all_pass():
    evaluations = [
        {"pass_recall": True, "pass_crosstopic": None},
        {"pass_recall": True, "pass_crosstopic": True},
    ]
    assert bench.overall_pass(evaluations) is True


def test_overall_pass_fails_when_any_fail():
    evaluations = [
        {"pass_recall": True, "pass_crosstopic": True},
        {"pass_recall": False, "pass_crosstopic": True},
    ]
    assert bench.overall_pass(evaluations) is False

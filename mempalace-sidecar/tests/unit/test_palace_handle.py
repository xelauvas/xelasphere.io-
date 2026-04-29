"""Unit tests for PalaceHandle (spec §5.3, plan M0.3.0).

These tests exercise the library-isolation wrapper against real mempalace
3.3.1 machinery on a tmp_path. "Unit" here means "per-handle, no sidecar
HTTP layer"; they still hit ChromaDB on disk because mocking Chroma at
the depth required to validate the idempotency contract would test the
mock, not the contract.

The integration test file (``tests/integration/test_palace_handle_real.py``)
covers only the embedding-model reuse assertion which requires an
explicit warmup-style opener.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import mempalace.palace as mp_palace
from mempalace_sidecar.palace_handle import PalaceHandle
from mempalace_sidecar.schemas import AddDrawerResult


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def palace_path(tmp_path: Path) -> Path:
    return tmp_path / "palace"


@pytest.fixture
def handle(palace_path: Path):
    h = PalaceHandle.open(palace_path)
    try:
        yield h
    finally:
        # Best-effort close — tests that exercise close explicitly will have
        # already closed, and close() is idempotent by contract.
        h.close()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _expected_drawer_id(turn_id: str, wing: str = "conversations", room: str = "session") -> str:
    source_file = f"turn:{turn_id}"
    return (
        f"drawer_{wing}_{room}_"
        + hashlib.sha256((source_file + str(0)).encode()).hexdigest()[:24]
    )


def _add(handle, *, turn_id: str, session_id: str = "sess", pair_id: str = "p", role: str = "user",
         text: str = "hello world", ts: float = 1_700_000_000.0,
         wing: str | None = None, room: str | None = None) -> AddDrawerResult:
    kwargs = {
        "turn_id": turn_id,
        "session_id": session_id,
        "pair_id": pair_id,
        "role": role,
        "text": text,
        "ts": ts,
    }
    if wing is not None:
        kwargs["wing"] = wing
    if room is not None:
        kwargs["room"] = room
    return handle.add_drawer(**kwargs)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_open_creates_palace_dir_and_child_state(palace_path: Path):
    assert not palace_path.exists()
    h = PalaceHandle.open(palace_path)
    try:
        # Directory + child state materialize.
        assert palace_path.is_dir()
        assert (palace_path / "turns.sqlite").is_file()
        assert (palace_path / "kg.sqlite").is_file()
        assert (palace_path / "meta.json").is_file()
        assert (palace_path / "config").is_dir()

        meta = json.loads((palace_path / "meta.json").read_text())
        assert meta["schema_version"] == 1
        assert "created_at" in meta

        # turns table exists with the literal schema from spec §5.3.
        conn = sqlite3.connect(str(palace_path / "turns.sqlite"))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
            assert cols == {
                "turn_id", "drawer_id", "session_id", "pair_id",
                "role", "ts", "filed_at",
            }
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='turns'"
            )}
            # The two spec-mandated indexes (+ sqlite's implicit PK autoindex).
            assert "turns_session_ts" in indexes
            assert "turns_pair" in indexes
        finally:
            conn.close()
    finally:
        h.close()


def test_add_drawer_idempotent_by_turn_id(handle):
    r1 = _add(handle, turn_id="T1", text="first call body")
    assert r1.status == "accepted"
    assert handle.col.count() == 1

    r2 = _add(handle, turn_id="T1", text="second call body (must not be filed)")
    assert r2.status == "duplicate"
    # Same drawer_id returned (the one stored in turns.sqlite on the first call).
    assert r2.drawer_id == r1.drawer_id
    # Critically: library was not called a second time.
    assert handle.col.count() == 1


def test_add_drawer_deterministic_drawer_id(tmp_path: Path):
    """Two fresh handles on the same palace compute the same drawer_id for
    the same turn_id — confirming the ``source_file=f"turn:{turn_id}"``
    encoding trick (spec §5.3) holds across process state.
    """
    p = tmp_path / "palace-det"
    h1 = PalaceHandle.open(p)
    try:
        r1 = _add(h1, turn_id="T-det")
    finally:
        h1.close()

    h2 = PalaceHandle.open(p)
    try:
        # The turn_id is already in turns.sqlite from h1, so this returns
        # "duplicate" — but it returns the SAME drawer_id, because both
        # handles derive it from the same ``source_file``.
        r2 = _add(h2, turn_id="T-det")
    finally:
        h2.close()

    assert r1.drawer_id == r2.drawer_id == _expected_drawer_id("T-det")


def test_session_drawers_returns_by_session(handle):
    # 3 turns in session A, 2 in session B, inserted interleaved.
    _add(handle, turn_id="A1", session_id="A", ts=1.0)
    _add(handle, turn_id="B1", session_id="B", ts=2.0)
    _add(handle, turn_id="A2", session_id="A", ts=3.0)
    _add(handle, turn_id="B2", session_id="B", ts=4.0)
    _add(handle, turn_id="A3", session_id="A", ts=5.0)

    drawers_a = handle.session_drawers("A")
    assert len(drawers_a) == 3

    # Reverse-time order: A3 (ts=5) → A2 (ts=3) → A1 (ts=1).
    # Each drawer dict carries the library's own metadata; cross-reference
    # via the source_file = f"turn:{turn_id}" encoding.
    got_turn_ids = [d["metadata"]["source_file"] for d in drawers_a]
    assert got_turn_ids == ["turn:A3", "turn:A2", "turn:A1"]

    drawers_b = handle.session_drawers("B")
    assert len(drawers_b) == 2
    got_turn_ids_b = [d["metadata"]["source_file"] for d in drawers_b]
    assert got_turn_ids_b == ["turn:B2", "turn:B1"]


def test_semantic_search_returns_search_memories_shape(handle, monkeypatch):
    """semantic_search must unwrap the library's dict envelope and return
    the inner ``results`` list. Mocked so we don't exercise ChromaDB's
    embedding path — we're testing the unwrap contract.
    """
    fixed = {
        "query": "anything",
        "filters": {"wing": None, "room": None},
        "total_before_filter": 2,
        "results": [
            {"text": "doc a", "wing": "conversations", "room": "session",
             "similarity": 0.9},
            {"text": "doc b", "wing": "conversations", "room": "session",
             "similarity": 0.7},
        ],
    }

    calls: list = []

    def fake_search_memories(query, palace_path, wing=None, room=None, n_results=5,
                             max_distance=0.0):
        calls.append({"query": query, "palace_path": palace_path,
                      "wing": wing, "room": room, "n_results": n_results})
        return fixed

    # Patch at the place palace_handle imported from.
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        fake_search_memories,
    )

    out = handle.semantic_search("anything", k=5)
    assert out == fixed["results"]
    assert len(calls) == 1
    assert calls[0]["query"] == "anything"
    # Buffer-fetch contract: ask the library for k * multiplier candidates
    # so post-filter trim back to k doesn't truncate. Default multiplier
    # is 3 (see DEFAULT_RETRIEVAL_BUFFER_MULTIPLIER).
    assert calls[0]["n_results"] == 15
    # palace_path passed as a string, per library signature.
    assert isinstance(calls[0]["palace_path"], str)


def test_semantic_search_filters_below_relevance_threshold(handle, monkeypatch):
    """Items with similarity below the threshold (default 0.25) are
    filtered out. Surfaced by Marcus persona dogfood (2026-04-29) where
    cross-topic queries (book recommendations, NYC coffee shops) returned
    project-context items at similarities 0.17-0.21 that polluted the
    LLM's context. Real recall hits scored 0.33-0.46 — the 0.25 floor
    sits between noise and signal.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                {"text": "high-relevance hit", "wing": "conversations",
                 "room": "session", "similarity": 0.45},
                {"text": "borderline hit", "wing": "conversations",
                 "room": "session", "similarity": 0.30},
                {"text": "low-similarity noise", "wing": "conversations",
                 "room": "session", "similarity": 0.18},
                {"text": "very low noise", "wing": "conversations",
                 "room": "session", "similarity": 0.05},
            ],
        },
    )
    out = handle.semantic_search("query", k=5)
    assert [r["text"] for r in out] == ["high-relevance hit", "borderline hit"]


def test_semantic_search_threshold_configurable_via_env(handle, monkeypatch):
    """``MEMPALACE_SEMANTIC_SCORE_THRESHOLD`` env var overrides the default
    so prod ops can re-tune the floor without a code deploy.
    """
    monkeypatch.setenv("MEMPALACE_SEMANTIC_SCORE_THRESHOLD", "0.5")
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                {"text": "above 0.5", "wing": "conversations",
                 "room": "session", "similarity": 0.6},
                {"text": "below 0.5 but above default 0.25",
                 "wing": "conversations", "room": "session",
                 "similarity": 0.4},
            ],
        },
    )
    out = handle.semantic_search("query", k=5)
    assert [r["text"] for r in out] == ["above 0.5"]


def test_semantic_search_treats_missing_similarity_as_zero(handle, monkeypatch):
    """Items without a ``similarity`` field are treated as similarity=0
    and filtered out — defensive against library schema drift, and
    consistent with the floor's intent (no signal, no value).
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                {"text": "with similarity", "wing": "conversations",
                 "room": "session", "similarity": 0.4},
                {"text": "no similarity field", "wing": "conversations",
                 "room": "session"},
            ],
        },
    )
    out = handle.semantic_search("query", k=5)
    assert [r["text"] for r in out] == ["with similarity"]


def test_semantic_search_returns_empty_when_all_below_threshold(handle, monkeypatch):
    """If every result is below threshold, return an empty list rather
    than pad with noise. Cross-topic queries should fall through cleanly
    to whatever the LLM does without retrieved memory.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                {"text": "all noise 1", "similarity": 0.10},
                {"text": "all noise 2", "similarity": 0.08},
            ],
        },
    )
    out = handle.semantic_search("query", k=5)
    assert out == []


def test_semantic_search_drops_assistant_hedge_at_read(handle, monkeypatch):
    """Read-time hedge filter complements PR #103's write-time filter:
    pre-deploy hedges already in the palace would otherwise outrank real
    establishment turns at retrieval time. Surfaced by Marcus persona
    bench (2026-04-29): "What's my A-race target time again?" returned
    two hedge responses at similarities 0.705 and 0.509 ahead of the
    real "sub-3:00" establishment turn at 0.436.

    Filter mirrors PR #103: same patterns, same length threshold,
    applied only to assistant turns (text starts with "ACTION: chat").
    The two hedge texts below are the literal strings observed in the
    bench-2026-04-29 probe.
    """
    hedge_a = (
        "ACTION: chat\nYour A-race target time isn't specified in our "
        "current conversation, and I don't have any prior information about "
        "your target times. Could you please provide more context or specify "
        "which race you're referring to?"
    )
    hedge_b = (
        "ACTION: chat\nI don't have any information about your A-race, "
        "including the date. If you could provide more context or details "
        "about the race, I'd be happy to help you find the information "
        "you're looking for."
    )
    real_answer = (
        "ACTION: chat\nYour A-race goal is sub-3:00 in Boston 2027 "
        "according to our earlier conversation. Coach Jenny Hadfield "
        "has you doing 90-mile peak weeks with hill repeats."
    )
    # Both hedges must be under the length threshold or the filter is moot.
    assert len(hedge_a) < 250
    assert len(hedge_b) < 250

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                {"text": hedge_a, "similarity": 0.705},
                {"text": real_answer, "similarity": 0.436},
                {"text": hedge_b, "similarity": 0.509},
            ],
        },
    )
    out = handle.semantic_search("a-race target", k=5)
    # Both hedges dropped, real establishment turn kept.
    assert len(out) == 1
    assert "sub-3:00" in out[0]["text"]


def test_semantic_search_keeps_user_text_with_hedge_phrasing(handle, monkeypatch):
    """Read-time hedge filter applies ONLY to assistant turns (text
    prefixed "ACTION: chat"). User turns that happen to use hedge
    phrasing must stay — they're real user statements that may carry
    establishment-grade signal ("I don't know what to do about my
    plantar fasciitis" is the user telling us about an injury).
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                # User turn — no ACTION: chat prefix. Kept regardless of phrasing.
                {"text": "I don't know what to do about my plantar fasciitis.",
                 "similarity": 0.55},
                # Assistant hedge — dropped.
                {"text": "ACTION: chat\nI don't know what you mean.",
                 "similarity": 0.55},
            ],
        },
    )
    out = handle.semantic_search("plantar", k=5)
    assert len(out) == 1
    assert out[0]["text"].startswith("I don't know what to do")


def test_semantic_search_keeps_long_assistant_response_with_hedge_phrase(
    handle, monkeypatch,
):
    """Long substantive assistant responses must stay even when they
    open with hedge phrasing. Threshold mirrors PR #103: items < 250
    chars + matching pattern are filtered; longer responses carry real
    semantic content. Without the length gate, every "I don't know
    exactly, but here's what I can tell you..." answer gets dropped.
    """
    long_assistant = (
        "ACTION: chat\nI don't know exactly what your peak mileage tolerance is, "
        "but here's what I can tell you: at 90 mile weeks with chronic plantar "
        "fasciitis, the typical marker for trouble is morning stiffness lasting "
        "more than 10 minutes after waking. If you're crossing that threshold "
        "consistently, drop volume by 15-20% for two weeks and add a daily 5-min "
        "calf and arch mobility routine before reassessing."
    )
    assert len(long_assistant) >= 350  # invariant for the test premise (v2 threshold)
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                {"text": long_assistant, "similarity": 0.55},
            ],
        },
    )
    out = handle.semantic_search("mileage", k=5)
    assert len(out) == 1
    assert out[0]["text"] == long_assistant


def test_semantic_search_buffer_fetch_trims_to_k_after_filter(handle, monkeypatch):
    """Buffer-fetch contract: ask the library for k * multiplier
    candidates so post-filter trim back to k doesn't truncate. Surfaced
    2026-04-29 — at fetch=k=5, two slots were pre-deploy hedges; PR #107
    dropped them but the result truncated to 3 with no backfill, burying
    the real ``sub-3:00`` answer at chroma rank 4.

    This test simulates: caller asks k=3, library returns 9 candidates,
    6 are filtered (4 hedges + 2 below floor), the 3 best survivors fill
    the result. Without buffer-fetch the result would be 1 item.
    """
    captured: dict = {}

    def capture_n_results(query, palace_path, wing=None, room=None,
                         n_results=5, max_distance=0.0):
        captured["n_results"] = n_results
        return {
            "results": [
                # 4 short hedges (filtered):
                {"text": "ACTION: chat\nI don't know.", "similarity": 0.95},
                {"text": "ACTION: chat\nCould you clarify?", "similarity": 0.92},
                {"text": "ACTION: chat\nI don't have any info.", "similarity": 0.90},
                {"text": "ACTION: chat\nI'm not sure.", "similarity": 0.85},
                # 3 substantive items (kept):
                {"text": "real answer one", "similarity": 0.60},
                {"text": "real answer two", "similarity": 0.55},
                {"text": "real answer three", "similarity": 0.50},
                # 2 below-floor (filtered):
                {"text": "noise one", "similarity": 0.10},
                {"text": "noise two", "similarity": 0.05},
            ],
        }

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        capture_n_results,
    )
    out = handle.semantic_search("query", k=3)
    # Library must have been asked for k * multiplier = 9 candidates.
    assert captured["n_results"] == 9
    # Caller gets up to k=3 substantive items.
    assert len(out) == 3
    assert [r["text"] for r in out] == [
        "real answer one", "real answer two", "real answer three",
    ]


def test_semantic_search_buffer_fetch_caps_at_absolute_ceiling(handle, monkeypatch):
    """The buffer multiplier is bounded by ``DEFAULT_RETRIEVAL_BUFFER_CAP``
    (50) so a caller passing absurdly large k doesn't blow up the chroma
    fetch. With multiplier=3, k=20 would be 60; cap clamps to 50.
    """
    captured: dict = {}

    def capture(query, palace_path, wing=None, room=None,
                n_results=5, max_distance=0.0):
        captured["n_results"] = n_results
        return {"results": []}

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories", capture,
    )
    handle.semantic_search("query", k=20)
    assert captured["n_results"] == 50  # capped at DEFAULT_RETRIEVAL_BUFFER_CAP


def test_semantic_search_buffer_multiplier_configurable_via_env(handle, monkeypatch):
    """``MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER`` env var overrides the
    default so prod ops can tune the buffer width without a code deploy.
    """
    monkeypatch.setenv("MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER", "5")
    captured: dict = {}

    def capture(query, palace_path, wing=None, room=None,
                n_results=5, max_distance=0.0):
        captured["n_results"] = n_results
        return {"results": []}

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories", capture,
    )
    handle.semantic_search("query", k=4)
    assert captured["n_results"] == 20  # k=4 * multiplier=5


def test_semantic_search_invalid_buffer_multiplier_falls_back(handle, monkeypatch):
    """Bad env var falls back to default — defensive against ops typos.
    """
    monkeypatch.setenv("MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER", "not-a-number")
    captured: dict = {}

    def capture(query, palace_path, wing=None, room=None,
                n_results=5, max_distance=0.0):
        captured["n_results"] = n_results
        return {"results": []}

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories", capture,
    )
    handle.semantic_search("query", k=5)
    assert captured["n_results"] == 15  # default multiplier=3


def test_semantic_search_zero_or_negative_buffer_multiplier_falls_back(
    handle, monkeypatch,
):
    """Multiplier < 1 doesn't make sense (would shrink the fetch); fall
    back to default rather than break retrieval.
    """
    monkeypatch.setenv("MEMPALACE_RETRIEVAL_BUFFER_MULTIPLIER", "0")
    captured: dict = {}

    def capture(query, palace_path, wing=None, room=None,
                n_results=5, max_distance=0.0):
        captured["n_results"] = n_results
        return {"results": []}

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories", capture,
    )
    handle.semantic_search("query", k=5)
    assert captured["n_results"] == 15  # default multiplier=3


def test_semantic_search_drops_isnt_specified_hedge(handle, monkeypatch):
    """v2 hedge pattern: 'X isn't specified' / 'X is not specified'.
    Surfaced by Marcus bench (2026-04-29) — the live sidecar returned
    'Your A-race target time isn't specified...' (212 chars) at sim 0.688
    on the recall probe, ranking ahead of the real answer. v1 patterns
    didn't match the phrasing.
    """
    text = (
        "ACTION: chat\nYour A-race target time isn't specified in our "
        "current conversation, but I can help you determine a realistic "
        "goal based on your previous performances or current training "
        "if you provide more details."
    )
    assert len(text) < 350  # in length-gate range
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [{"text": text, "similarity": 0.688}]},
    )
    out = handle.semantic_search("query", k=5)
    assert out == []


def test_semantic_search_drops_didnt_decide_hedge(handle, monkeypatch):
    """v2 hedge pattern: 'didn't decide'. Surfaced by Marcus bench —
    'You didn't decide anything... beginning of our conversation'
    (257 chars) was just over the v1 length cap of 250 and survived;
    the v2 cap of 350 + new pattern catches it cleanly.
    """
    text = (
        "ACTION: chat\nYou didn't decide anything about the Saturday "
        "long-run time change in this conversation, as this is the "
        "beginning of our conversation and no such discussion has taken "
        "place. If you'd like to discuss or decide on a time change, "
        "I'm here to help."
    )
    assert 250 < len(text) < 350  # would have survived v1 length=250
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [{"text": text, "similarity": 0.356}]},
    )
    out = handle.semantic_search("query", k=5)
    assert out == []


def test_semantic_search_drops_beginning_of_conversation_hedge(handle, monkeypatch):
    """v2 hedge pattern: 'beginning of our/this conversation' — common
    opener for assistant turns that have no recall to offer.
    """
    text = (
        "ACTION: chat\nThis is the beginning of our conversation, so I "
        "don't have prior context. Let me know what you're working on."
    )
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [{"text": text, "similarity": 0.5}]},
    )
    out = handle.semantic_search("query", k=5)
    assert out == []


def test_semantic_search_drops_if_you_provide_more_details_hedge(handle, monkeypatch):
    """v2 hedge pattern: 'if you provide more details' / variants — the
    offer-to-help-if-more-info pattern that masks a non-answer. Patterns
    cover 'if you (provide|share|give|can provide|can share)' + (more|
    additional) + (details|context|information). Other syntactic shapes
    ('Once you give me X', 'When you tell me Y') aren't covered — they
    weren't observed in the bench data and would risk false positives
    in substantive answers.
    """
    cases = [
        "ACTION: chat\nI can answer if you provide more details.",
        "ACTION: chat\nLet me know if you can share more context.",
        "ACTION: chat\nIf you give me additional information I can help.",
    ]
    for text in cases:
        monkeypatch.setattr(
            "mempalace_sidecar.palace_handle._library_search_memories",
            lambda *a, _t=text, **kw: {"results": [{"text": _t, "similarity": 0.5}]},
        )
        out = handle.semantic_search("query", k=5)
        assert out == [], f"hedge survived: {text!r}"


def test_semantic_search_keeps_long_substantive_hedge_phrased_answer(
    handle, monkeypatch,
):
    """The bumped 350-char length threshold preserves the original v1
    intent: substantive answers that happen to use hedge phrasing carry
    real signal and stay indexed. This 360-char answer mentions
    'isn't specified' but goes on to provide concrete guidance.
    """
    text = (
        "ACTION: chat\nYour exact mileage cap isn't specified in your "
        "training plan, but here's the general rule for chronic plantar "
        "fasciitis at sub-3:00 marathon training: peak weeks should not "
        "exceed 90 miles, and any week where morning stiffness lasts "
        "more than 10 minutes signals you should drop volume by 15-20% "
        "for 2 weeks. Add daily 5-min calf and arch mobility before "
        "reassessing."
    )
    assert len(text) >= 350  # invariant for the test premise
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [{"text": text, "similarity": 0.5}]},
    )
    out = handle.semantic_search("mileage", k=5)
    assert len(out) == 1
    assert out[0]["text"] == text


def test_semantic_search_drops_near_duplicate_user_question(handle, monkeypatch):
    """Surfaced by Marcus bench (2026-04-29): on the recall probe
    'Hey, what's my A-race target time again?' the top two retrieved
    items were past phrasings of the same question at similarities
    0.972 and 0.958, displacing the real ``sub-3:00`` establishment
    turn from top-K. Drop them — a past identical question doesn't
    answer the present one.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [
            # Past phrasings of the same question (drop):
            {"text": "What's my A-race target time again?", "similarity": 0.972},
            {"text": "Hey, picking up. What's my A-race target time again?",
             "similarity": 0.958},
            # Real establishment turn — user statement, no question mark:
            # Anchors the relative floor (0.32 * 0.7 = 0.224); near-dup Q's
            # at 0.97/0.96 are well above and drop.
            {"text": '"I\'m Marcus, marathoner in Asheville. Training for '
                     'Boston 2027 — my first Boston, sub-3:00 goal. "',
             "similarity": 0.32},
        ]},
    )
    out = handle.semantic_search("any-query", k=5)
    assert len(out) == 1
    assert "sub-3:00" in out[0]["text"]


def test_semantic_search_keeps_user_question_below_relative_floor(
    handle, monkeypatch,
):
    """A user question that scored well below the relative floor (top
    non-question sim × 0.7) is kept — it's tangentially related, not a
    near-duplicate. With anchor at 0.65 (a high-sim non-question item),
    floor is 0.455; a 0.42 question stays.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [
            # High-sim non-question anchor (sets relative floor at 0.455):
            {"text": "Marcus's intro statement about marathon goals.",
             "similarity": 0.65},
            # Tangentially related question well below 0.455:
            {"text": "What did I decide about the Saturday long-run time change?",
             "similarity": 0.42},
        ]},
    )
    out = handle.semantic_search("a-race", k=5)
    assert len(out) == 2  # both kept; question is tangential, not near-dup


def test_semantic_search_keeps_long_user_message_ending_in_question(
    handle, monkeypatch,
):
    """A long user message that ends with a clarifying question still
    carries establishment-grade signal in the body. The 200-char length
    cap protects this case — a 280-char message with a question at the
    end is not a 'near-duplicate question.'
    """
    long_user_msg = (
        "I've been dealing with right-foot plantar fasciitis for about "
        "six months now. It's worse in the morning, gets better as I run, "
        "but flares up after long runs. Coach Jenny has me doing daily "
        "calf stretches and a heel-up sleep brace. Should I cut peak "
        "mileage from 90 to 80?"
    )
    assert len(long_user_msg) >= 200  # invariant for the test premise
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [
            {"text": long_user_msg, "similarity": 0.92},
        ]},
    )
    out = handle.semantic_search("plantar", k=5)
    assert len(out) == 1


def test_semantic_search_keeps_assistant_question_at_high_similarity(
    handle, monkeypatch,
):
    """Assistant turns ('ACTION: chat\\n...?') even at high similarity
    are not near-dup user questions — different concern. They're caught
    by the hedge filter if hedge-shaped, otherwise retained.
    """
    text = (
        "ACTION: chat\nWould you like me to build the training plan "
        "around the sub-3:00 target, or shift to a more conservative "
        "sub-3:10 with the plantar fasciitis recovery in mind?"
    )
    # Substantive assistant question — not a hedge.
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [{"text": text, "similarity": 0.91}]},
    )
    out = handle.semantic_search("any", k=5)
    assert len(out) == 1


def test_semantic_search_keeps_user_statement_at_high_similarity(
    handle, monkeypatch,
):
    """A user statement (no '?') at high similarity is a real
    establishment turn — keep it. Marcus's intro turn at sim 0.93 to
    'tell me about myself' should not be dropped.
    """
    statement = '"I\'m Marcus, marathoner in Asheville, sub-3:00 Boston 2027 goal."'
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [{"text": statement, "similarity": 0.93}]},
    )
    out = handle.semantic_search("about-me", k=5)
    assert len(out) == 1


def test_semantic_search_relative_near_dup_threshold_adapts_to_corpus(
    handle, monkeypatch,
):
    """Cross-persona generalization: Marcus's near-dup questions scored
    0.97 (dense corpus); Tom's scored 0.58 (sparser corpus). v1 absolute
    threshold of 0.85 caught Marcus but not Tom. v2 uses a relative
    threshold (top non-question sim × 0.7) so the same logic catches
    near-dups in either density.

    Simulates Tom's deploy-box probe: top non-question item at 0.394
    (a 'Full recap' user statement); near-dup questions at 0.581 and
    0.477. Floor = 0.394 * 0.7 = 0.276. Both questions exceed 0.276,
    so both drop. Real Hetzner item at 0.288 (a question with
    establishment-grade content but length > question-filter cap)
    survives via the length gate.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [
            # Near-dup user questions (drop):
            {"text": "What box am I deploying to?", "similarity": 0.581},
            {"text": "What's my deploy box again?", "similarity": 0.477},
            # User statement anchor for relative floor:
            {"text": "Full recap — features, stack, deadline, beta user, "
                     "deploy target. Just want to see I've got it all straight.",
             "similarity": 0.394},
            # The answer-bearing question (length > 200 chars: keeps via length gate)
            {"text": "Deploy target is a Hetzner box, ~4GB RAM. Does that "
                     "change the DB recommendation? I want to make sure I'm "
                     "not over-provisioning given the workload pattern, so "
                     "let me know what specs justify the choice and what "
                     "I should keep an eye on as users come online.",
             "similarity": 0.288},
        ]},
    )
    out = handle.semantic_search("deploy-box", k=5)
    texts = [r["text"] for r in out]
    # Both near-dup questions dropped; statement and Hetzner kept.
    assert len(out) == 2
    assert any("Full recap" in t for t in texts)
    assert any("Hetzner" in t for t in texts)


def test_semantic_search_no_near_dup_filter_when_only_questions_returned(
    handle, monkeypatch,
):
    """Edge case: retrieval returns only user-turn questions with no
    non-question anchor. Under v2 relative threshold, the floor would
    be undefined; we fall back to no near-dup filtering rather than
    drop everything and leave the caller empty-handed.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [
            {"text": "What did I decide about the long-run change?",
             "similarity": 0.42},
            {"text": "Who was that runner I mentioned?", "similarity": 0.38},
        ]},
    )
    out = handle.semantic_search("recap", k=5)
    # Both questions kept (no anchor → no near-dup filter applied).
    assert len(out) == 2


def test_semantic_search_relative_floor_anchored_on_non_questions_only(
    handle, monkeypatch,
):
    """The relative floor anchor is the highest sim among NON-USER-
    QUESTION items. If a question scores at 0.95 (highest of all) but
    a statement is at 0.60, the floor is 0.60 * 0.7 = 0.42, not
    0.95 * 0.7 = 0.665. Otherwise the floor would pivot on the very
    items we're trying to drop, anchoring itself out.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {"results": [
            # Highest sim is a question (would be near-dup if we anchored on it).
            {"text": "What's my favorite color?", "similarity": 0.95},
            # Non-question anchor (correct anchor):
            {"text": "I told you blue is my favorite color.", "similarity": 0.60},
            # Lower-sim question (would be kept under correct anchor: 0.45 < 0.42).
            {"text": "Wait, what color did I say?", "similarity": 0.45},
        ]},
    )
    out = handle.semantic_search("color", k=5)
    # 0.95 question dropped (above 0.60 * 0.7 = 0.42). 0.45 question
    # also dropped (still above 0.42). 0.60 statement kept.
    assert len(out) == 1
    assert "blue is my favorite" in out[0]["text"]


def test_semantic_search_hedge_filter_complements_score_floor(handle, monkeypatch):
    """Both filters apply — a high-similarity hedge that passes the
    score floor still gets dropped by the hedge filter, and a low-
    similarity non-hedge still gets dropped by the score floor. Order
    is irrelevant because the predicates compose by AND.
    """
    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_search_memories",
        lambda *a, **kw: {
            "results": [
                # High-similarity assistant hedge — score floor passes,
                # hedge filter drops.
                {"text": "ACTION: chat\nI don't have any info about that.",
                 "similarity": 0.80},
                # Low-similarity non-hedge — score floor drops.
                {"text": "ACTION: chat\nA substantive answer that scores low.",
                 "similarity": 0.10},
                # High-similarity non-hedge — both pass.
                {"text": "ACTION: chat\nA substantive answer that scores high.",
                 "similarity": 0.60},
            ],
        },
    )
    out = handle.semantic_search("anything", k=5)
    assert len(out) == 1
    assert "scores high" in out[0]["text"]


def test_list_drawers_offset_pagination(handle):
    # Insert 25 drawers.
    for i in range(25):
        _add(
            handle,
            turn_id=f"T{i:02d}",
            session_id="S",
            pair_id=f"p{i}",
            role="user" if i % 2 == 0 else "assistant",
            text=f"drawer body {i}",
            ts=float(i),
        )
    assert handle.col.count() == 25

    page1 = handle.list_drawers(limit=10, offset=0)
    page2 = handle.list_drawers(limit=10, offset=10)
    page3 = handle.list_drawers(limit=10, offset=20)

    assert len(page1["drawers"]) == 10
    assert len(page2["drawers"]) == 10
    assert len(page3["drawers"]) == 5

    assert page1["total"] == 25
    assert page2["total"] == 25
    assert page3["total"] == 25

    assert page1["next_offset"] == 10
    assert page2["next_offset"] == 20
    assert page3["next_offset"] is None


def test_list_drawers_total_is_none_when_filtered(handle):
    """Honesty contract: total is filtered-inaccurate, so for any filtered
    query we return total=None rather than an unfiltered wrong count. The
    Flask adapter renders "showing N of many" rather than a lie.

    next_offset still works: when the returned page is full, more may
    exist; when short, we're at the end.
    """
    # Insert 10 drawers in wing A and 5 in wing B.
    for i in range(10):
        _add(handle, turn_id=f"A{i:02d}", session_id="S", pair_id=f"a{i}",
              role="user", text=f"A-body {i}", ts=float(i), wing="wingA", room="r")
    for i in range(5):
        _add(handle, turn_id=f"B{i:02d}", session_id="S", pair_id=f"b{i}",
              role="user", text=f"B-body {i}", ts=float(100 + i), wing="wingB", room="r")
    assert handle.col.count() == 15

    # Filtered: total must be None (honest).
    filtered = handle.list_drawers(wing="wingA", limit=10, offset=0)
    assert filtered["total"] is None
    assert len(filtered["drawers"]) == 10
    # Full page → more may exist → next_offset is offset+limit.
    assert filtered["next_offset"] == 10

    # Short page → end of pagination → next_offset is None.
    short_page = handle.list_drawers(wing="wingB", limit=10, offset=0)
    assert short_page["total"] is None
    assert len(short_page["drawers"]) == 5
    assert short_page["next_offset"] is None

    # Unfiltered path still returns the real total.
    unfiltered = handle.list_drawers(limit=10, offset=0)
    assert unfiltered["total"] == 15
    assert unfiltered["next_offset"] == 10


def test_close_pops_backend_cache(palace_path: Path):
    """Teeth-bearing spec §5.3 cache-eviction contract: close() MUST remove
    the library's backend client entry. The key the library uses is the
    resolved absolute path string.
    """
    h = PalaceHandle.open(palace_path)

    # After open, the library's backend cache holds our palace.
    key = str(h.palace_path)
    # Library stores the resolved/absolute path; verify our key is the one
    # that ended up in the cache.
    assert any(Path(k).resolve() == Path(key).resolve()
               for k in mp_palace._DEFAULT_BACKEND._clients), \
        f"expected a client keyed by {key!r} in _clients: " \
        f"{list(mp_palace._DEFAULT_BACKEND._clients.keys())}"

    h.close()

    # After close, no entry in the backend cache references this palace path.
    assert not any(Path(k).resolve() == Path(key).resolve()
                   for k in mp_palace._DEFAULT_BACKEND._clients), \
        "_DEFAULT_BACKEND._clients still holds an entry for the closed palace"


def test_close_is_idempotent(handle):
    """Spec §5.3 lifecycle: second and subsequent close() calls are no-ops.
    Must NOT raise (KnowledgeGraph.close on an already-closed sqlite
    connection would raise without a guard).
    """
    handle.close()
    # Second call — should be a no-op, not raise.
    handle.close()
    # Third call for good measure.
    handle.close()


def test_no_mcp_server_import():
    """Spec §5.3 "Why not bridge to mempalace.mcp_server": the mcp_server
    module uses module-level global palace state and would cause cross-user
    data leaks in a multi-tenant sidecar. Grep the source; fail on any
    reference.
    """
    pkg_root = Path(__file__).resolve().parents[2] / "mempalace_sidecar"
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        text = py.read_text()
        # Catch both ``import mempalace.mcp_server`` and
        # ``from mempalace.mcp_server import ...`` and ``mempalace.mcp_server.foo``.
        if "mempalace.mcp_server" in text or "from mempalace import mcp_server" in text:
            offenders.append(str(py))
    assert offenders == [], \
        f"mempalace.mcp_server references forbidden per spec §5.3; found in: {offenders}"


def test_turns_authoritative_when_library_id_derivation_changes(handle, monkeypatch):
    """Empirical proof that ``turns.sqlite`` — not the library — is the
    idempotency authority.

    Simulate a future ``mempalace`` release that changes how drawer_ids are
    derived from ``source_file``: we monkey-patch ``miner.add_drawer`` to
    salt the ID so the library would file a DIFFERENT drawer on the second
    call. If turns.sqlite is authoritative, the second call is blocked
    before the library is reached, and the collection count stays at 1.
    """
    # First call: normal path, accepted.
    r1 = _add(handle, turn_id="T-auth", text="original")
    assert r1.status == "accepted"
    assert handle.col.count() == 1

    # Second call: monkey-patch the library's add_drawer via the bound
    # reference palace_handle uses. If handle.add_drawer short-circuits on
    # turns.sqlite, this patched function never runs.
    patched_calls: list = []

    def salted_add_drawer(col, wing, room, content, source_file, chunk_index, agent):
        patched_calls.append(source_file)
        # Produce a DIFFERENT ID than the deterministic one.
        salted_id = "drawer_SALTED_DIFFERENT_ID_" + source_file[-8:]
        col.upsert(documents=[content], ids=[salted_id],
                   metadatas=[{"wing": wing, "room": room,
                               "source_file": source_file,
                               "salted": True}])
        return True

    monkeypatch.setattr(
        "mempalace_sidecar.palace_handle._library_add_drawer",
        salted_add_drawer,
    )

    r2 = _add(handle, turn_id="T-auth", text="second (must not file)")

    # Two assertions, both load-bearing:
    # 1. status == "duplicate" — blocked by turns.sqlite PK gate, not by
    #    library-level dedup.
    assert r2.status == "duplicate"
    # 2. collection.count() == 1 — library was never called (patched_calls
    #    would have recorded a call if it had been).
    assert handle.col.count() == 1
    assert patched_calls == [], \
        "handle.add_drawer called the library on a duplicate turn_id; " \
        "turns.sqlite is NOT the authority"

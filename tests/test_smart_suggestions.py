"""
Tests for app/services/smart_suggestions.py

Covers:
- Suggestion dataclass creation and defaults
- to_dict() serialization (reasoning excluded)
- to_task_option() conversion
- from_dict() deserialization round-trip
- save_pool() Firestore interaction
- load_pool() — empty case and data case
- get_pool_metadata() — missing and present cases
- Constants
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

from app.services.smart_suggestions import (
    Suggestion,
    save_pool,
    load_pool,
    get_pool_metadata,
    select_display_slots,
    _score_today_slot,
    build_generation_prompt,
    parse_llm_suggestions,
    POOL_SIZE,
    DISPLAY_SIZE,
    SLOT_TYPES,
)
from app.grog_api import TaskOption


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_suggestion(**kwargs) -> Suggestion:
    defaults = dict(
        title="Finish the report",
        subtitle="You were working on it earlier",
        prompt="Let's continue the report",
        slot_type="continue",
        category="work",
        icon="pencil",
        reasoning="User has an unfinished report task",
    )
    defaults.update(kwargs)
    return Suggestion(**defaults)


def make_mock_db(snapshot_exists=False, snapshot_data=None):
    """Build a minimal Firestore mock that chains collection/document/get/set."""
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_subcollection = MagicMock()
    mock_document = MagicMock()
    mock_pool_doc = MagicMock()
    mock_snapshot = MagicMock()

    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_document
    mock_document.collection.return_value = mock_subcollection
    mock_subcollection.document.return_value = mock_pool_doc
    mock_pool_doc.get.return_value = mock_snapshot
    mock_pool_doc.set.return_value = None
    mock_snapshot.exists = snapshot_exists
    mock_snapshot.to_dict.return_value = snapshot_data or {}

    return mock_db, mock_pool_doc, mock_snapshot


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_pool_size(self):
        assert POOL_SIZE == 10

    def test_display_size(self):
        assert DISPLAY_SIZE == 3

    def test_slot_types(self):
        assert SLOT_TYPES == ("continue", "today", "discover")


# ---------------------------------------------------------------------------
# Suggestion creation & defaults
# ---------------------------------------------------------------------------

class TestSuggestionCreation:
    def test_basic_creation(self):
        s = make_suggestion()
        assert s.title == "Finish the report"
        assert s.subtitle == "You were working on it earlier"
        assert s.prompt == "Let's continue the report"
        assert s.slot_type == "continue"
        assert s.category == "work"
        assert s.icon == "pencil"
        assert s.reasoning == "User has an unfinished report task"

    def test_shown_count_defaults_to_zero(self):
        s = make_suggestion()
        assert s.shown_count == 0

    def test_created_at_auto_set(self):
        before = datetime.now(timezone.utc)
        s = make_suggestion()
        after = datetime.now(timezone.utc)
        assert s.created_at is not None
        assert isinstance(s.created_at, datetime)
        assert before <= s.created_at <= after

    def test_created_at_can_be_supplied(self):
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        s = make_suggestion(created_at=dt)
        assert s.created_at == dt

    def test_shown_count_can_be_supplied(self):
        s = make_suggestion(shown_count=5)
        assert s.shown_count == 5


# ---------------------------------------------------------------------------
# to_dict()
# ---------------------------------------------------------------------------

class TestSuggestionToDict:
    def test_reasoning_excluded(self):
        s = make_suggestion()
        d = s.to_dict()
        assert "reasoning" not in d

    def test_required_fields_present(self):
        s = make_suggestion()
        d = s.to_dict()
        for key in ("title", "subtitle", "prompt", "slot_type", "category", "icon", "shown_count", "created_at"):
            assert key in d, f"Missing key: {key}"

    def test_field_values_correct(self):
        s = make_suggestion()
        d = s.to_dict()
        assert d["title"] == "Finish the report"
        assert d["subtitle"] == "You were working on it earlier"
        assert d["prompt"] == "Let's continue the report"
        assert d["slot_type"] == "continue"
        assert d["category"] == "work"
        assert d["icon"] == "pencil"
        assert d["shown_count"] == 0

    def test_created_at_serialized_as_string(self):
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        s = make_suggestion(created_at=dt)
        d = s.to_dict()
        assert isinstance(d["created_at"], str)
        assert "2026-01-15" in d["created_at"]


# ---------------------------------------------------------------------------
# to_task_option()
# ---------------------------------------------------------------------------

class TestSuggestionToTaskOption:
    def test_returns_task_option_instance(self):
        s = make_suggestion()
        task_option = s.to_task_option()
        assert isinstance(task_option, TaskOption)

    def test_title_and_subtitle_preserved(self):
        s = make_suggestion()
        task_option = s.to_task_option()
        assert task_option.title == s.title
        assert task_option.subtitle == s.subtitle

    def test_action_wraps_prompt(self):
        s = make_suggestion(prompt="Tell me about Python")
        task_option = s.to_task_option()
        assert task_option.action == "sendMessage('Tell me about Python')"

    def test_icon_preserved(self):
        s = make_suggestion(icon="rocket")
        task_option = s.to_task_option()
        assert task_option.icon == "rocket"

    def test_task_option_to_dict_works(self):
        """Ensure the TaskOption produced is valid by calling its own to_dict()."""
        s = make_suggestion()
        d = s.to_task_option().to_dict()
        assert d["title"] == s.title
        escaped = s.prompt.replace("'", "\\'")
        assert d["action"] == f"sendMessage('{escaped}')"


# ---------------------------------------------------------------------------
# from_dict()
# ---------------------------------------------------------------------------

class TestSuggestionFromDict:
    def test_round_trip(self):
        original = make_suggestion()
        d = original.to_dict()
        # reasoning is excluded from to_dict; add it back to test deserialization
        d["reasoning"] = original.reasoning
        restored = Suggestion.from_dict(d)
        assert restored.title == original.title
        assert restored.subtitle == original.subtitle
        assert restored.prompt == original.prompt
        assert restored.slot_type == original.slot_type
        assert restored.category == original.category
        assert restored.icon == original.icon
        assert restored.reasoning == original.reasoning
        assert restored.shown_count == original.shown_count

    def test_missing_reasoning_defaults_to_empty_string(self):
        d = make_suggestion().to_dict()
        s = Suggestion.from_dict(d)
        assert s.reasoning == ""

    def test_created_at_parsed_from_iso_string(self):
        dt = datetime(2026, 3, 10, 8, 30, 0, tzinfo=timezone.utc)
        d = make_suggestion(created_at=dt).to_dict()
        s = Suggestion.from_dict(d)
        assert s.created_at.year == 2026
        assert s.created_at.month == 3
        assert s.created_at.day == 10

    def test_created_at_from_datetime_object(self):
        dt = datetime(2026, 5, 20, tzinfo=timezone.utc)
        d = {
            "title": "Test", "subtitle": "Sub", "prompt": "p",
            "slot_type": "today", "category": "cat", "icon": "star",
            "shown_count": 0, "created_at": dt,
        }
        s = Suggestion.from_dict(d)
        assert s.created_at == dt

    def test_defaults_for_missing_fields(self):
        s = Suggestion.from_dict({})
        assert s.title == ""
        assert s.subtitle == ""
        assert s.prompt == ""
        assert s.icon == "star"
        assert s.shown_count == 0
        assert s.reasoning == ""

    def test_invalid_created_at_string_falls_back(self):
        d = make_suggestion().to_dict()
        d["created_at"] = "not-a-date"
        s = Suggestion.from_dict(d)
        assert isinstance(s.created_at, datetime)


# ---------------------------------------------------------------------------
# save_pool()
# ---------------------------------------------------------------------------

class TestSavePool:
    def test_calls_firestore_set(self):
        mock_db, mock_pool_doc, _ = make_mock_db()
        suggestions = [make_suggestion(title=f"S{i}") for i in range(3)]
        save_pool(mock_db, "user123", suggestions, reason="initial_load")
        mock_pool_doc.set.assert_called_once()

    def test_firestore_path_correct(self):
        mock_db, _, _ = make_mock_db()
        save_pool(mock_db, "user_abc", [], reason="test")
        mock_db.collection.assert_called_with("users")
        mock_db.collection.return_value.document.assert_called_with("user_abc")
        mock_db.collection.return_value.document.return_value.collection.assert_called_with("smart_suggestions")
        (mock_db.collection.return_value.document.return_value
         .collection.return_value.document.assert_called_with("pool"))

    def test_payload_contains_suggestions(self):
        mock_db, mock_pool_doc, _ = make_mock_db()
        suggestions = [make_suggestion(title="My suggestion")]
        save_pool(mock_db, "u1", suggestions, reason="refresh")
        call_args = mock_pool_doc.set.call_args[0][0]
        assert "suggestions" in call_args
        assert len(call_args["suggestions"]) == 1
        assert call_args["suggestions"][0]["title"] == "My suggestion"

    def test_payload_contains_metadata(self):
        mock_db, mock_pool_doc, _ = make_mock_db()
        save_pool(mock_db, "u1", [], reason="manual_refresh")
        call_args = mock_pool_doc.set.call_args[0][0]
        assert "last_refresh" in call_args
        assert call_args["refresh_reason"] == "manual_refresh"
        assert call_args["pool_size"] == 0

    def test_reasoning_not_in_persisted_suggestions(self):
        mock_db, mock_pool_doc, _ = make_mock_db()
        s = make_suggestion(reasoning="secret internal reasoning")
        save_pool(mock_db, "u1", [s], reason="test")
        call_args = mock_pool_doc.set.call_args[0][0]
        persisted_suggestion = call_args["suggestions"][0]
        assert "reasoning" not in persisted_suggestion


# ---------------------------------------------------------------------------
# load_pool()
# ---------------------------------------------------------------------------

class TestLoadPool:
    def test_returns_empty_list_when_no_pool(self):
        mock_db, _, _ = make_mock_db(snapshot_exists=False)
        result = load_pool(mock_db, "user_no_pool")
        assert result == []

    def test_returns_suggestions_when_pool_exists(self):
        s = make_suggestion(title="Loaded suggestion")
        raw = s.to_dict()
        snapshot_data = {"suggestions": [raw], "pool_size": 1}
        mock_db, _, _ = make_mock_db(snapshot_exists=True, snapshot_data=snapshot_data)
        result = load_pool(mock_db, "user123")
        assert len(result) == 1
        assert isinstance(result[0], Suggestion)
        assert result[0].title == "Loaded suggestion"

    def test_returns_multiple_suggestions(self):
        suggestions = [make_suggestion(title=f"S{i}") for i in range(5)]
        raw = [s.to_dict() for s in suggestions]
        snapshot_data = {"suggestions": raw, "pool_size": 5}
        mock_db, _, _ = make_mock_db(snapshot_exists=True, snapshot_data=snapshot_data)
        result = load_pool(mock_db, "user123")
        assert len(result) == 5

    def test_empty_suggestions_list_in_doc(self):
        snapshot_data = {"suggestions": [], "pool_size": 0}
        mock_db, _, _ = make_mock_db(snapshot_exists=True, snapshot_data=snapshot_data)
        result = load_pool(mock_db, "user123")
        assert result == []

    def test_firestore_path_correct(self):
        mock_db, _, _ = make_mock_db(snapshot_exists=False)
        load_pool(mock_db, "user_xyz")
        mock_db.collection.assert_called_with("users")
        mock_db.collection.return_value.document.assert_called_with("user_xyz")


# ---------------------------------------------------------------------------
# get_pool_metadata()
# ---------------------------------------------------------------------------

class TestGetPoolMetadata:
    def test_returns_defaults_when_no_pool(self):
        mock_db, _, _ = make_mock_db(snapshot_exists=False)
        meta = get_pool_metadata(mock_db, "user_new")
        assert meta["last_refresh"] is None
        assert meta["refresh_reason"] is None
        assert meta["pool_size"] == 0

    def test_returns_metadata_when_pool_exists(self):
        snapshot_data = {
            "last_refresh": "2026-04-01T10:00:00+00:00",
            "refresh_reason": "scheduled",
            "pool_size": 7,
        }
        mock_db, _, _ = make_mock_db(snapshot_exists=True, snapshot_data=snapshot_data)
        meta = get_pool_metadata(mock_db, "user123")
        assert meta["last_refresh"] == "2026-04-01T10:00:00+00:00"
        assert meta["refresh_reason"] == "scheduled"
        assert meta["pool_size"] == 7

    def test_pool_size_defaults_to_zero_when_key_missing(self):
        snapshot_data = {"last_refresh": "2026-01-01", "refresh_reason": "test"}
        mock_db, _, _ = make_mock_db(snapshot_exists=True, snapshot_data=snapshot_data)
        meta = get_pool_metadata(mock_db, "user123")
        assert meta["pool_size"] == 0

    def test_firestore_path_correct(self):
        mock_db, _, _ = make_mock_db(snapshot_exists=False)
        get_pool_metadata(mock_db, "meta_user")
        mock_db.collection.assert_called_with("users")
        mock_db.collection.return_value.document.assert_called_with("meta_user")


# ---------------------------------------------------------------------------
# _score_today_slot()
# ---------------------------------------------------------------------------

class TestScoreTodaySlot:
    def test_morning_keyword_scores_high_in_morning(self):
        s = make_suggestion(title="Morning meditation", subtitle="Start your day", slot_type="today")
        score_morning = _score_today_slot(s, hour=8)
        score_evening = _score_today_slot(s, hour=19)
        assert score_morning > score_evening

    def test_afternoon_keyword_scores_high_in_afternoon(self):
        s = make_suggestion(title="Afternoon review", subtitle="Check progress", slot_type="today")
        score_afternoon = _score_today_slot(s, hour=14)
        score_night = _score_today_slot(s, hour=23)
        assert score_afternoon > score_night

    def test_evening_keyword_scores_high_in_evening(self):
        s = make_suggestion(title="Evening wind-down", subtitle="Relax", slot_type="today")
        score_evening = _score_today_slot(s, hour=19)
        score_morning = _score_today_slot(s, hour=8)
        assert score_evening > score_morning

    def test_night_keyword_scores_high_at_night(self):
        s = make_suggestion(title="Night reflection", subtitle="Review the day", slot_type="today")
        score_night = _score_today_slot(s, hour=23)
        score_afternoon = _score_today_slot(s, hour=14)
        assert score_night > score_afternoon

    def test_no_keyword_returns_zero(self):
        s = make_suggestion(title="Generic task", subtitle="Do something", slot_type="today")
        score = _score_today_slot(s, hour=10)
        assert score == 0

    def test_subtitle_keywords_also_matched(self):
        s = make_suggestion(title="Daily task", subtitle="Do your morning routine", slot_type="today")
        score_morning = _score_today_slot(s, hour=7)
        score_evening = _score_today_slot(s, hour=20)
        assert score_morning > score_evening

    def test_night_hours_wrap_around(self):
        """Night = hours 22-4; hour=2 should match 'night' keyword."""
        s = make_suggestion(title="Night owl session", subtitle="Late work", slot_type="today")
        score_night_late = _score_today_slot(s, hour=2)
        score_midday = _score_today_slot(s, hour=12)
        assert score_night_late > score_midday


# ---------------------------------------------------------------------------
# select_display_slots()
# ---------------------------------------------------------------------------

class TestSelectDisplaySlots:
    def test_empty_pool_returns_empty(self):
        result = select_display_slots([], hour=10)
        assert result == []

    def test_picks_one_per_slot_type(self):
        pool = [
            make_suggestion(title="C1", slot_type="continue"),
            make_suggestion(title="T1", slot_type="today"),
            make_suggestion(title="D1", slot_type="discover"),
        ]
        result = select_display_slots(pool, hour=10)
        assert len(result) == 3
        slot_types = {s.slot_type for s in result}
        assert slot_types == {"continue", "today", "discover"}

    def test_at_most_one_per_slot_type(self):
        pool = [
            make_suggestion(title="C1", slot_type="continue"),
            make_suggestion(title="C2", slot_type="continue"),
            make_suggestion(title="T1", slot_type="today"),
            make_suggestion(title="D1", slot_type="discover"),
        ]
        result = select_display_slots(pool, hour=10)
        continue_slots = [s for s in result if s.slot_type == "continue"]
        assert len(continue_slots) == 1

    def test_missing_slot_type_returns_fewer_than_3(self):
        """Pool with no 'discover' suggestions yields only 2 results."""
        pool = [
            make_suggestion(title="C1", slot_type="continue"),
            make_suggestion(title="T1", slot_type="today"),
        ]
        result = select_display_slots(pool, hour=10)
        assert len(result) == 2
        slot_types = {s.slot_type for s in result}
        assert "discover" not in slot_types

    def test_continue_slot_prefers_lowest_shown_count(self):
        pool = [
            make_suggestion(title="C_high", slot_type="continue", shown_count=10),
            make_suggestion(title="C_low", slot_type="continue", shown_count=1),
            make_suggestion(title="C_mid", slot_type="continue", shown_count=5),
        ]
        # Run multiple times to ensure it's deterministic (not just lucky)
        for _ in range(10):
            result = select_display_slots(pool, hour=10)
            chosen = next(s for s in result if s.slot_type == "continue")
            assert chosen.title == "C_low"

    def test_discover_slot_prefers_lowest_shown_count(self):
        pool = [
            make_suggestion(title="D_high", slot_type="discover", shown_count=8),
            make_suggestion(title="D_low", slot_type="discover", shown_count=0),
            make_suggestion(title="D_mid", slot_type="discover", shown_count=4),
        ]
        for _ in range(10):
            result = select_display_slots(pool, hour=10)
            chosen = next(s for s in result if s.slot_type == "discover")
            assert chosen.title == "D_low"

    def test_today_slot_prefers_time_matching_suggestion(self):
        """A 'morning' suggestion should win at hour=8 over a generic one."""
        pool = [
            make_suggestion(title="Morning yoga", slot_type="today"),
            make_suggestion(title="Generic daily task", slot_type="today"),
        ]
        result = select_display_slots(pool, hour=8)
        chosen = next(s for s in result if s.slot_type == "today")
        assert chosen.title == "Morning yoga"

    def test_today_slot_picks_highest_scorer(self):
        """Evening suggestion wins in the evening."""
        pool = [
            make_suggestion(title="Morning run", slot_type="today"),
            make_suggestion(title="Evening walk", slot_type="today"),
            make_suggestion(title="Afternoon snack", slot_type="today"),
        ]
        result = select_display_slots(pool, hour=19)
        chosen = next(s for s in result if s.slot_type == "today")
        assert chosen.title == "Evening walk"

    def test_default_hour_is_current_utc(self):
        """Calling without hour= should not raise an error."""
        pool = [make_suggestion(title="T1", slot_type="today")]
        result = select_display_slots(pool)
        assert len(result) == 1

    def test_only_continue_suggestions(self):
        pool = [make_suggestion(title="C1", slot_type="continue")]
        result = select_display_slots(pool, hour=10)
        assert len(result) == 1
        assert result[0].slot_type == "continue"

    def test_returns_suggestion_objects(self):
        pool = [
            make_suggestion(title="C1", slot_type="continue"),
            make_suggestion(title="T1", slot_type="today"),
            make_suggestion(title="D1", slot_type="discover"),
        ]
        result = select_display_slots(pool, hour=10)
        for item in result:
            assert isinstance(item, Suggestion)


# ---------------------------------------------------------------------------
# build_generation_prompt()
# ---------------------------------------------------------------------------

def make_context(**kwargs):
    defaults = dict(
        memories=[
            {"type": "preference", "key": "language", "value": "Python"},
            {"type": "fact", "key": "job", "value": "Software engineer"},
        ],
        recent_topics=["machine learning", "productivity"],
        suggestion_profile={
            "top_categories": ["work", "learning"],
            "avoided_categories": ["gaming"],
        },
        hour=14,
        day_of_week="Tuesday",
        exclusion_titles=["Write a poem"],
    )
    defaults.update(kwargs)
    return defaults


class TestBuildGenerationPrompt:
    def test_returns_string(self):
        prompt = build_generation_prompt(make_context())
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_includes_memories_section(self):
        prompt = build_generation_prompt(make_context())
        assert "## User Memories" in prompt

    def test_includes_memory_values(self):
        prompt = build_generation_prompt(make_context())
        assert "Python" in prompt
        assert "Software engineer" in prompt

    def test_includes_recent_topics_section(self):
        prompt = build_generation_prompt(make_context())
        assert "## Recent Topics" in prompt
        assert "machine learning" in prompt
        assert "productivity" in prompt

    def test_includes_suggestion_profile_section(self):
        prompt = build_generation_prompt(make_context())
        assert "## Suggestion Profile" in prompt
        assert "work" in prompt
        assert "learning" in prompt

    def test_includes_avoided_categories(self):
        prompt = build_generation_prompt(make_context())
        assert "gaming" in prompt

    def test_includes_current_context_section(self):
        prompt = build_generation_prompt(make_context())
        assert "## Current Context" in prompt
        assert "14" in prompt
        assert "Tuesday" in prompt

    def test_includes_exclusion_section(self):
        prompt = build_generation_prompt(make_context())
        assert "## Do NOT Suggest" in prompt
        assert "Write a poem" in prompt

    def test_caps_memories_at_20(self):
        many_memories = [{"type": "fact", "key": f"k{i}", "value": f"v{i}"} for i in range(30)]
        ctx = make_context(memories=many_memories)
        prompt = build_generation_prompt(ctx)
        # v20 and beyond should not appear (capped at first 20)
        assert "v20" not in prompt
        assert "v19" in prompt

    def test_caps_topics_at_5(self):
        many_topics = [f"topic{i}" for i in range(10)]
        ctx = make_context(recent_topics=many_topics)
        prompt = build_generation_prompt(ctx)
        assert "topic5" not in prompt
        assert "topic4" in prompt

    def test_caps_exclusions_at_20(self):
        many_exclusions = [f"Title {i}" for i in range(25)]
        ctx = make_context(exclusion_titles=many_exclusions)
        prompt = build_generation_prompt(ctx)
        assert "Title 20" not in prompt
        assert "Title 19" in prompt

    def test_empty_memories_graceful(self):
        ctx = make_context(memories=[])
        prompt = build_generation_prompt(ctx)
        assert "## User Memories" in prompt

    def test_empty_exclusions_graceful(self):
        ctx = make_context(exclusion_titles=[])
        prompt = build_generation_prompt(ctx)
        assert "## Do NOT Suggest" in prompt


# ---------------------------------------------------------------------------
# parse_llm_suggestions()
# ---------------------------------------------------------------------------

VALID_JSON_ARRAY = """[
  {
    "title": "Learn Python decorators",
    "subtitle": "Deepen your Python skills",
    "prompt": "Explain Python decorators with examples",
    "slot_type": "discover",
    "category": "learning",
    "icon": "book",
    "reasoning": "User is interested in Python"
  },
  {
    "title": "Morning productivity plan",
    "subtitle": "Plan your day",
    "prompt": "Help me plan my morning for maximum productivity",
    "slot_type": "today",
    "category": "work",
    "icon": "sun",
    "reasoning": "It is morning"
  }
]"""

MARKDOWN_WRAPPED = f"```json\n{VALID_JSON_ARRAY}\n```"


class TestParseLlmSuggestions:
    def test_parses_valid_json_array(self):
        result = parse_llm_suggestions(VALID_JSON_ARRAY)
        assert len(result) == 2
        assert all(isinstance(s, Suggestion) for s in result)

    def test_returns_suggestion_with_correct_fields(self):
        result = parse_llm_suggestions(VALID_JSON_ARRAY)
        titles = [s.title for s in result]
        assert "Learn Python decorators" in titles
        assert "Morning productivity plan" in titles

    def test_parses_markdown_wrapped_json(self):
        result = parse_llm_suggestions(MARKDOWN_WRAPPED)
        assert len(result) == 2

    def test_returns_empty_on_none(self):
        result = parse_llm_suggestions(None)
        assert result == []

    def test_returns_empty_on_empty_string(self):
        result = parse_llm_suggestions("")
        assert result == []

    def test_returns_empty_on_malformed_json(self):
        result = parse_llm_suggestions("{ this is not valid json [[[")
        assert result == []

    def test_skips_items_missing_title(self):
        raw = '[{"subtitle": "sub", "prompt": "p", "slot_type": "today", "category": "c", "icon": "i", "reasoning": "r"}]'
        result = parse_llm_suggestions(raw)
        assert result == []

    def test_skips_items_missing_prompt(self):
        raw = '[{"title": "T", "subtitle": "sub", "slot_type": "today", "category": "c", "icon": "i", "reasoning": "r"}]'
        result = parse_llm_suggestions(raw)
        assert result == []

    def test_returns_empty_on_json_object_not_array(self):
        raw = '{"title": "T", "prompt": "p"}'
        result = parse_llm_suggestions(raw)
        assert result == []

    def test_partial_valid_items_returned(self):
        """Items with both title and prompt are kept; incomplete items are skipped."""
        raw = '[{"title": "Good", "prompt": "do it", "subtitle": "", "slot_type": "discover", "category": "c", "icon": "star", "reasoning": ""}, {"subtitle": "no title or prompt"}]'
        result = parse_llm_suggestions(raw)
        assert len(result) == 1
        assert result[0].title == "Good"

    def test_suggestion_defaults_applied(self):
        """Missing optional fields use defaults from Suggestion.from_dict."""
        raw = '[{"title": "Minimal", "prompt": "do minimal"}]'
        result = parse_llm_suggestions(raw)
        assert len(result) == 1
        assert result[0].icon == "star"  # default from from_dict
        assert result[0].shown_count == 0


# ---------------------------------------------------------------------------
# Helpers for click tracking tests
# ---------------------------------------------------------------------------

def make_mock_db_with_profile(snapshot_exists=False, snapshot_data=None):
    """Build a Firestore mock that supports both pool and profile documents."""
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_subcollection = MagicMock()
    mock_document = MagicMock()
    mock_profile_doc = MagicMock()
    mock_snapshot = MagicMock()

    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_document
    mock_document.collection.return_value = mock_subcollection
    mock_subcollection.document.return_value = mock_profile_doc
    mock_profile_doc.get.return_value = mock_snapshot
    mock_profile_doc.set.return_value = None
    mock_profile_doc.update.return_value = None
    mock_snapshot.exists = snapshot_exists
    mock_snapshot.to_dict.return_value = snapshot_data or {}

    return mock_db, mock_profile_doc, mock_snapshot


# ---------------------------------------------------------------------------
# record_click()
# ---------------------------------------------------------------------------

from app.services.smart_suggestions import record_click


class TestRecordClick:
    def test_calls_firestore_set_with_merge(self):
        mock_db, mock_profile_doc, _ = make_mock_db_with_profile()
        click_data = {"category": "work", "slot_type": "continue", "title": "Finish report"}
        record_click(mock_db, "user123", click_data)
        mock_profile_doc.set.assert_called_once()
        _, kwargs = mock_profile_doc.set.call_args
        assert kwargs.get("merge") is True

    def test_click_entry_has_required_fields(self):
        from google.cloud.firestore_v1.transforms import ArrayUnion
        mock_db, mock_profile_doc, _ = make_mock_db_with_profile()
        click_data = {"category": "learning", "slot_type": "discover", "title": "Learn Rust"}
        record_click(mock_db, "user123", click_data)
        call_args = mock_profile_doc.set.call_args[0][0]
        assert "click_history" in call_args
        entry = call_args["click_history"]
        assert isinstance(entry, ArrayUnion)

    def test_click_entry_contains_click_data_fields(self):
        mock_db, mock_profile_doc, _ = make_mock_db_with_profile()
        click_data = {"category": "health", "slot_type": "today", "title": "Morning run"}
        record_click(mock_db, "user123", click_data)
        call_args = mock_profile_doc.set.call_args[0][0]
        # The click_history should use ArrayUnion — check the value passed
        # We check the underlying entry in the ArrayUnion wrapper
        import google.cloud.firestore
        array_union_val = call_args["click_history"]
        # ArrayUnion wraps values; access internal values list
        entry = array_union_val.values[0]
        assert entry["category"] == "health"
        assert entry["slot_type"] == "today"
        assert entry["title"] == "Morning run"
        assert "clicked_at" in entry

    def test_firestore_path_uses_profile_document(self):
        mock_db, _, _ = make_mock_db_with_profile()
        click_data = {"category": "work", "slot_type": "continue", "title": "T"}
        record_click(mock_db, "user_abc", click_data)
        mock_db.collection.assert_called_with("users")
        mock_db.collection.return_value.document.assert_called_with("user_abc")
        mock_db.collection.return_value.document.return_value.collection.assert_called_with("smart_suggestions")
        (mock_db.collection.return_value.document.return_value
         .collection.return_value.document.assert_called_with("profile"))

    def test_handles_firestore_error_gracefully(self):
        mock_db, mock_profile_doc, _ = make_mock_db_with_profile()
        mock_profile_doc.set.side_effect = Exception("Firestore unavailable")
        click_data = {"category": "work", "slot_type": "continue", "title": "T"}
        # Should not raise
        record_click(mock_db, "user123", click_data)


# ---------------------------------------------------------------------------
# increment_shown_counts()
# ---------------------------------------------------------------------------

from app.services.smart_suggestions import increment_shown_counts


class TestIncrementShownCounts:
    def _make_pool_db(self, pool_suggestions):
        """Build a mock that returns pool suggestions from Firestore."""
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_subcollection = MagicMock()
        mock_document = MagicMock()
        mock_pool_doc = MagicMock()
        mock_snapshot = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_document
        mock_document.collection.return_value = mock_subcollection
        mock_subcollection.document.return_value = mock_pool_doc
        mock_pool_doc.get.return_value = mock_snapshot
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            "suggestions": [s.to_dict() for s in pool_suggestions]
        }
        return mock_db, mock_pool_doc

    def test_increments_shown_count_for_matching_titles(self):
        s1 = make_suggestion(title="Alpha", shown_count=2)
        s2 = make_suggestion(title="Beta", shown_count=0)
        mock_db, mock_pool_doc = self._make_pool_db([s1, s2])
        increment_shown_counts(mock_db, "user123", ["Alpha"])
        mock_pool_doc.update.assert_called_once()
        updated_pool = mock_pool_doc.update.call_args[0][0]["pool"]
        alpha = next(s for s in updated_pool if s["title"] == "Alpha")
        beta = next(s for s in updated_pool if s["title"] == "Beta")
        assert alpha["shown_count"] == 3
        assert beta["shown_count"] == 0

    def test_increments_multiple_titles(self):
        s1 = make_suggestion(title="Alpha", shown_count=1)
        s2 = make_suggestion(title="Beta", shown_count=1)
        s3 = make_suggestion(title="Gamma", shown_count=0)
        mock_db, mock_pool_doc = self._make_pool_db([s1, s2, s3])
        increment_shown_counts(mock_db, "user123", ["Alpha", "Beta"])
        updated_pool = mock_pool_doc.update.call_args[0][0]["pool"]
        alpha = next(s for s in updated_pool if s["title"] == "Alpha")
        beta = next(s for s in updated_pool if s["title"] == "Beta")
        gamma = next(s for s in updated_pool if s["title"] == "Gamma")
        assert alpha["shown_count"] == 2
        assert beta["shown_count"] == 2
        assert gamma["shown_count"] == 0

    def test_does_nothing_when_no_titles_match(self):
        s1 = make_suggestion(title="Alpha", shown_count=1)
        mock_db, mock_pool_doc = self._make_pool_db([s1])
        increment_shown_counts(mock_db, "user123", ["Nonexistent"])
        # update is still called (we write back the pool even without changes)
        updated_pool = mock_pool_doc.update.call_args[0][0]["pool"]
        alpha = next(s for s in updated_pool if s["title"] == "Alpha")
        assert alpha["shown_count"] == 1

    def test_handles_firestore_error_gracefully(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore error")
        # Should not raise
        increment_shown_counts(mock_db, "user123", ["Alpha"])

    def test_handles_empty_pool_gracefully(self):
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_subcollection = MagicMock()
        mock_document = MagicMock()
        mock_pool_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_document
        mock_document.collection.return_value = mock_subcollection
        mock_subcollection.document.return_value = mock_pool_doc
        mock_pool_doc.get.return_value = mock_snapshot
        mock_snapshot.exists = False
        increment_shown_counts(mock_db, "user123", ["Alpha"])
        mock_pool_doc.update.assert_not_called()


# ---------------------------------------------------------------------------
# build_suggestion_profile()
# ---------------------------------------------------------------------------

from app.services.smart_suggestions import build_suggestion_profile


class TestBuildSuggestionProfile:
    def test_empty_history_returns_empty_profile(self):
        profile = build_suggestion_profile([])
        assert profile["top_categories"] == []
        assert profile["avoided_categories"] == []
        assert profile["preferred_slot"] is None
        assert profile["engagement_by_category"] == {}

    def test_top_categories_most_clicked_first(self):
        history = [
            {"category": "work", "slot_type": "continue", "title": "T1"},
            {"category": "work", "slot_type": "continue", "title": "T2"},
            {"category": "work", "slot_type": "continue", "title": "T3"},
            {"category": "learning", "slot_type": "discover", "title": "T4"},
            {"category": "learning", "slot_type": "discover", "title": "T5"},
            {"category": "health", "slot_type": "today", "title": "T6"},
        ]
        profile = build_suggestion_profile(history)
        assert profile["top_categories"][0] == "work"
        assert profile["top_categories"][1] == "learning"
        assert "health" in profile["top_categories"]

    def test_preferred_slot_most_clicked(self):
        history = [
            {"category": "work", "slot_type": "continue", "title": "T1"},
            {"category": "work", "slot_type": "continue", "title": "T2"},
            {"category": "learning", "slot_type": "discover", "title": "T3"},
        ]
        profile = build_suggestion_profile(history)
        assert profile["preferred_slot"] == "continue"

    def test_engagement_by_category_sums_to_one(self):
        history = [
            {"category": "work", "slot_type": "continue", "title": "T1"},
            {"category": "work", "slot_type": "continue", "title": "T2"},
            {"category": "learning", "slot_type": "discover", "title": "T3"},
            {"category": "health", "slot_type": "today", "title": "T4"},
        ]
        profile = build_suggestion_profile(history)
        total = sum(profile["engagement_by_category"].values())
        assert abs(total - 1.0) < 1e-9

    def test_engagement_by_category_correct_fractions(self):
        history = [
            {"category": "work", "slot_type": "continue", "title": "T1"},
            {"category": "work", "slot_type": "continue", "title": "T2"},
            {"category": "learning", "slot_type": "discover", "title": "T3"},
            {"category": "learning", "slot_type": "discover", "title": "T4"},
        ]
        profile = build_suggestion_profile(history)
        assert profile["engagement_by_category"]["work"] == 0.5
        assert profile["engagement_by_category"]["learning"] == 0.5

    def test_avoided_categories_is_empty_list(self):
        history = [{"category": "work", "slot_type": "continue", "title": "T"}]
        profile = build_suggestion_profile(history)
        assert profile["avoided_categories"] == []

    def test_single_click_history(self):
        history = [{"category": "health", "slot_type": "today", "title": "Morning run"}]
        profile = build_suggestion_profile(history)
        assert profile["top_categories"] == ["health"]
        assert profile["preferred_slot"] == "today"
        assert profile["engagement_by_category"]["health"] == 1.0


# ---------------------------------------------------------------------------
# load_suggestion_profile()
# ---------------------------------------------------------------------------

from app.services.smart_suggestions import load_suggestion_profile


class TestLoadSuggestionProfile:
    def test_returns_empty_profile_when_doc_missing(self):
        mock_db, _, _ = make_mock_db_with_profile(snapshot_exists=False)
        profile = load_suggestion_profile(mock_db, "user123")
        assert profile["top_categories"] == []
        assert profile["avoided_categories"] == []
        assert profile["preferred_slot"] is None
        assert profile["engagement_by_category"] == {}

    def test_returns_built_profile_from_click_history(self):
        click_history = [
            {"category": "work", "slot_type": "continue", "title": "T1"},
            {"category": "work", "slot_type": "continue", "title": "T2"},
            {"category": "learning", "slot_type": "discover", "title": "T3"},
        ]
        mock_db, _, _ = make_mock_db_with_profile(
            snapshot_exists=True,
            snapshot_data={"click_history": click_history}
        )
        profile = load_suggestion_profile(mock_db, "user123")
        assert profile["top_categories"][0] == "work"
        assert profile["preferred_slot"] == "continue"

    def test_firestore_path_uses_profile_document(self):
        mock_db, _, _ = make_mock_db_with_profile(snapshot_exists=False)
        load_suggestion_profile(mock_db, "user_xyz")
        mock_db.collection.assert_called_with("users")
        mock_db.collection.return_value.document.assert_called_with("user_xyz")
        mock_db.collection.return_value.document.return_value.collection.assert_called_with("smart_suggestions")
        (mock_db.collection.return_value.document.return_value
         .collection.return_value.document.assert_called_with("profile"))

    def test_returns_empty_profile_when_click_history_missing(self):
        mock_db, _, _ = make_mock_db_with_profile(
            snapshot_exists=True,
            snapshot_data={}
        )
        profile = load_suggestion_profile(mock_db, "user123")
        assert profile["top_categories"] == []

    def test_handles_firestore_error_gracefully(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore error")
        profile = load_suggestion_profile(mock_db, "user123")
        assert profile["top_categories"] == []
        assert profile["preferred_slot"] is None

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

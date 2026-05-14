import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from app.services.reminder_intent import (
    is_reminder_request, extract_reminder, needs_clarification,
)


class TestIsReminderRequest:
    @pytest.mark.parametrize("prompt", [
        "remind me to call mom tomorrow at 9am",
        "leave a reminder for me to pay rent",
        "set a reminder for the meeting at 3pm",
        "remind me in 2 hours to take the cake out",
        "Remind my customer to pay £184 in the morning",
    ])
    def test_positive(self, prompt):
        assert is_reminder_request(prompt) is True

    @pytest.mark.parametrize("prompt", [
        "what's a reminder app?",
        "I'm trying to remember the email",
        "remember when we used to play games?",
        "",
        "x",
        None,
    ])
    def test_negative(self, prompt):
        assert is_reminder_request(prompt) is False


class TestExtractReminder:
    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_returns_structured_fields(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "name": "Pay customer £184",
            "instructions": "collect payment",
            "fireAtLocal": "2026-05-15T09:00:00",
            "timezone": "Europe/London",
        })
        result = extract_reminder(
            prompt="remind me to pay tomorrow morning",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="Europe/London",
        )
        assert result["name"] == "Pay customer £184"
        assert result["fireAtLocal"] == "2026-05-15T09:00:00"
        assert result["timezone"] == "Europe/London"

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_handles_garbage_llm_output(self, mock_llm):
        mock_llm.return_value = "not json at all"
        result = extract_reminder(
            prompt="remind me",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="UTC",
        )
        assert result == {"name": None, "instructions": None, "fireAtLocal": None, "timezone": "UTC"}

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_handles_empty_llm_output(self, mock_llm):
        mock_llm.return_value = ""
        result = extract_reminder(
            prompt="remind me",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="UTC",
        )
        assert result["name"] is None
        assert result["fireAtLocal"] is None

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_returns_partial_on_missing_time(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "name": "Call mom",
            "instructions": None,
            "fireAtLocal": None,
            "timezone": "Europe/London",
        })
        result = extract_reminder(
            prompt="remind me to call mom",
            now_utc=datetime(2026, 5, 14, 22, 0, tzinfo=timezone.utc),
            user_timezone="Europe/London",
        )
        assert result["name"] == "Call mom"
        assert result["fireAtLocal"] is None

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_extract_handles_non_dict_json(self, mock_llm):
        mock_llm.return_value = "[1, 2, 3]"  # valid JSON but not a dict
        result = extract_reminder(
            prompt="remind me",
            now_utc=datetime(2026, 5, 14, tzinfo=timezone.utc),
            user_timezone="UTC",
        )
        assert all(v is None or v == "UTC" for v in result.values())


class TestNeedsClarification:
    def test_yes_when_intent_and_no_time(self):
        assert needs_clarification(
            "remind me to call mom",
            {"name": "Call mom", "instructions": None, "fireAtLocal": None, "timezone": "UTC"},
        ) is True

    def test_no_when_time_present(self):
        assert needs_clarification(
            "remind me to call mom at 9am",
            {"name": "Call mom", "instructions": None, "fireAtLocal": "2026-05-15T09:00", "timezone": "UTC"},
        ) is False

    def test_no_when_intent_doesnt_match(self):
        assert needs_clarification(
            "what time is it",
            {"name": None, "instructions": None, "fireAtLocal": None, "timezone": "UTC"},
        ) is False

    def test_no_when_no_name(self):
        # Even if intent matches, no name means we couldn't extract anything useful
        assert needs_clarification(
            "remind me",
            {"name": None, "instructions": None, "fireAtLocal": None, "timezone": "UTC"},
        ) is False

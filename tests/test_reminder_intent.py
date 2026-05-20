import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from app.services.reminder_intent import (
    is_reminder_request, extract_reminder, needs_clarification,
    _build_extract_user_msg,
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


class TestBuildExtractUserMsg:
    """Regression guard for the timezone bug: the extraction LLM must be fed
    the current time in the user's LOCAL zone, not UTC. Feeding UTC made the
    8B model emit a fireAtLocal in the past for any tz ahead of UTC, so
    "remind me ... in one minute" was silently dropped."""

    NOW_UTC = datetime(2026, 5, 20, 10, 17, 0, tzinfo=timezone.utc)

    def test_uses_local_time_for_positive_offset_tz(self):
        # Europe/Bucharest is UTC+3 (EEST) on 2026-05-20 -> 13:17 local.
        msg = _build_extract_user_msg(
            "remind me to call mom in one minute", self.NOW_UTC, "Europe/Bucharest"
        )
        assert "2026-05-20T13:17:00" in msg
        assert "10:17" not in msg  # the UTC clock must NOT leak through
        assert "Europe/Bucharest" in msg

    def test_uses_local_time_for_negative_offset_tz(self):
        # America/New_York is UTC-4 (EDT) on 2026-05-20 -> 06:17 local.
        msg = _build_extract_user_msg("remind me", self.NOW_UTC, "America/New_York")
        assert "2026-05-20T06:17:00" in msg
        assert "10:17" not in msg

    def test_emitted_time_is_naive_no_offset_no_micros(self):
        # Clean naive format so the model echoes a naive fireAtLocal that
        # compute_fire_at_utc interprets against the named timezone.
        msg = _build_extract_user_msg("remind me", self.NOW_UTC, "Europe/Bucharest")
        assert "2026-05-20T13:17:00." not in msg  # no microseconds
        assert "2026-05-20T13:17:00+" not in msg  # no offset suffix

    def test_falls_back_to_utc_on_bad_timezone(self):
        # Garbage tz must not raise — extract_reminder promises to never raise.
        msg = _build_extract_user_msg("remind me", self.NOW_UTC, "Not/AZone")
        assert "2026-05-20T10:17:00" in msg

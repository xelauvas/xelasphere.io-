import pytest
from unittest.mock import patch, MagicMock

from app.services.reminder_chat_hook import maybe_create_reminder_from_prompt


class TestMaybeCreate:
    @patch("app.config.db")
    @patch("app.services.reminder_intent._llm_extract_json")
    def test_creates_reminder_for_full_intent(self, mock_llm, mock_db):
        import json
        mock_llm.return_value = json.dumps({
            "name": "Call mom",
            "instructions": "from kitchen",
            "fireAtLocal": "2099-01-01T09:00:00",
            "timezone": "Europe/London",
        })
        doc_ref = MagicMock()
        doc_ref.id = "rem_abc"
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = doc_ref

        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="remind me to call mom tomorrow at 9am",
            user_id="u1",
            user_email="a@b.com",
            user_timezone="Europe/London",
            source_chat_id="chat_x",
        )
        assert rid == "rem_abc"
        assert needs_clar is False
        assert doc_ref.set.called
        # Now also asserts the reminder dict comes back directly (no re-read)
        assert reminder is not None
        assert reminder["id"] == "rem_abc"
        assert reminder["name"] == "Call mom"

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_returns_clarification_when_no_time(self, mock_llm):
        import json
        mock_llm.return_value = json.dumps({
            "name": "Call mom",
            "instructions": None,
            "fireAtLocal": None,
            "timezone": "Europe/London",
        })
        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="remind me to call mom",
            user_id="u1",
            user_email="a@b.com",
            user_timezone="Europe/London",
        )
        assert rid is None
        assert reminder is None
        assert needs_clar is True

    def test_returns_none_when_not_a_reminder_request(self):
        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="what time is it?",
            user_id="u1",
            user_email="a@b.com",
            user_timezone="UTC",
        )
        assert rid is None
        assert needs_clar is False

    def test_returns_none_when_user_id_missing(self):
        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="remind me to call mom tomorrow",
            user_id=None,
            user_email="a@b.com",
            user_timezone="UTC",
        )
        assert rid is None
        assert needs_clar is False

    @patch("app.config.Config")
    def test_returns_none_when_feature_disabled(self, mock_cfg):
        mock_cfg.REMINDERS_ENABLED = False
        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="remind me tomorrow at 9am",
            user_id="u1",
            user_email="a@b.com",
            user_timezone="UTC",
        )
        assert rid is None
        assert needs_clar is False

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_returns_none_when_llm_returns_garbage(self, mock_llm):
        mock_llm.return_value = "garbage not json"
        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="remind me to do stuff",
            user_id="u1",
            user_email="a@b.com",
            user_timezone="UTC",
        )
        assert rid is None
        # No name extracted → no clarification needed (we don't even know what to ask about)
        assert needs_clar is False

    @patch("app.services.reminder_intent._llm_extract_json")
    def test_returns_none_when_extracted_fire_at_in_past(self, mock_llm):
        import json
        mock_llm.return_value = json.dumps({
            "name": "Done already",
            "fireAtLocal": "2020-01-01T09:00:00",
            "timezone": "UTC",
        })
        rid, reminder, needs_clar = maybe_create_reminder_from_prompt(
            user_prompt="remind me at 9am",
            user_id="u1",
            user_email="a@b.com",
            user_timezone="UTC",
        )
        assert rid is None
        assert needs_clar is False

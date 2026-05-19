"""Unit tests for app.services.push_dispatcher.

Pure tests of payload building and truncation. The actual send logic
is tested as an integration test in Task 4."""
import json
import pytest


class TestBuildPayload:
    def test_payload_has_required_fields(self):
        from app.services.push_dispatcher import build_payload
        reminder = {
            "id": "rid_abc", "name": "Standup", "instructions": "10-min sync",
            "sourceChatId": "session_xyz",
        }
        payload = json.loads(build_payload(reminder, snooze_token="tok_123"))
        assert payload["title"] == "Standup"
        assert payload["body"] == "10-min sync"
        assert payload["data"]["rid"] == "rid_abc"
        assert payload["data"]["chat"] == "session_xyz"
        assert payload["data"]["snoozeToken"] == "tok_123"
        assert len(payload["actions"]) == 2
        assert payload["actions"][0]["action"] == "snooze_10m"
        assert payload["actions"][1]["action"] == "snooze_1h"

    def test_body_falls_back_to_name_when_no_instructions(self):
        from app.services.push_dispatcher import build_payload
        reminder = {"id": "rid_abc", "name": "Just a title", "sourceChatId": None}
        payload = json.loads(build_payload(reminder, snooze_token="tok"))
        assert payload["body"] == "Just a title"

    def test_chat_defaults_to_null_when_no_sourceChatId(self):
        from app.services.push_dispatcher import build_payload
        reminder = {"id": "rid_abc", "name": "x"}
        payload = json.loads(build_payload(reminder, snooze_token="tok"))
        assert payload["data"]["chat"] is None


class TestTruncate:
    def test_short_body_unchanged(self):
        from app.services.push_dispatcher import _truncate
        assert _truncate("hello", 100) == "hello"

    def test_long_body_truncated_with_ellipsis(self):
        from app.services.push_dispatcher import _truncate
        text = "x" * 100
        result = _truncate(text, 50)
        assert len(result) <= 50
        assert result.endswith("…")

    def test_truncate_at_exact_limit(self):
        from app.services.push_dispatcher import _truncate
        assert _truncate("abcdef", 6) == "abcdef"

    def test_truncate_handles_multibyte_emoji(self):
        from app.services.push_dispatcher import _truncate
        # '👨‍👩‍👧' is a multi-codepoint family emoji. Truncation should not
        # produce a malformed string (no unpaired surrogates).
        text = "Hello 👨‍👩‍👧 there!"
        result = _truncate(text, 8)
        assert len(result) <= 8
        # No errors when encoding the result as UTF-8
        result.encode("utf-8")

    def test_truncate_handles_none(self):
        from app.services.push_dispatcher import _truncate
        assert _truncate(None, 10) == ""

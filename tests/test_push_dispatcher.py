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


from unittest.mock import MagicMock, patch


class TestSendPushForReminder:
    """send_push_for_reminder iterates the user's pushSubscriptions and
    calls pywebpush.webpush for each. Tests cover the success path, 410
    cleanup, 413 retry, and no-op when flag off / VAPID missing."""

    def _reminder(self):
        return {
            "id": "rid_x", "name": "Standup", "instructions": "team sync",
            "sourceChatId": "session_abc",
        }

    def _sub_doc(self, subId="sub_1", endpoint="https://fcm.../send/abc"):
        snap = MagicMock()
        snap.id = subId
        snap.exists = True
        snap.to_dict.return_value = {
            "endpoint": endpoint,
            "keys": {"p256dh": "p", "auth": "a"},
        }
        snap.reference = MagicMock()
        return snap

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.Config")
    def test_no_op_when_flag_off(self, mock_config, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = False
        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")
        mock_pywebpush.webpush.assert_not_called()

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.Config")
    def test_no_op_when_vapid_missing(self, mock_config, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = ""  # missing
        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")
        mock_pywebpush.webpush.assert_not_called()

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.make_snooze_token")
    @patch("app.services.push_dispatcher.Config")
    def test_happy_path_sends_to_each_sub(self, mock_config, mock_token, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = "PEM"
        mock_config.VAPID_CLAIMS_SUB = "mailto:test@x.io"
        mock_token.return_value = "tok_xyz"

        sub1 = self._sub_doc(subId="sub_1")
        sub2 = self._sub_doc(subId="sub_2", endpoint="https://updates.../sub2")
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub1, sub2]

        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")

        assert mock_pywebpush.webpush.call_count == 2
        # Each sub's reference.update is called with lastSeenAt
        assert sub1.reference.update.called
        assert sub2.reference.update.called

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.make_snooze_token")
    @patch("app.services.push_dispatcher.Config")
    def test_410_deletes_subscription(self, mock_config, mock_token, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = "PEM"
        mock_config.VAPID_CLAIMS_SUB = "mailto:t@x.io"
        mock_token.return_value = "tok"

        sub = self._sub_doc()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub]

        # Mock pywebpush.webpush raising 410
        class _FakeResponse:
            status_code = 410
        class _FakeException(Exception):
            response = _FakeResponse()
        mock_pywebpush.WebPushException = _FakeException
        mock_pywebpush.webpush.side_effect = _FakeException("Gone")

        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(self._reminder(), user_id="u1")

        assert sub.reference.delete.called

    @patch("app.services.push_dispatcher.db")
    @patch("app.services.push_dispatcher.pywebpush")
    @patch("app.services.push_dispatcher.make_snooze_token")
    @patch("app.services.push_dispatcher.Config")
    def test_413_retries_with_truncated_body(self, mock_config, mock_token, mock_pywebpush, mock_db):
        mock_config.PUSH_ENABLED = True
        mock_config.VAPID_PRIVATE_KEY = "PEM"
        mock_config.VAPID_CLAIMS_SUB = "mailto:t@x.io"
        mock_token.return_value = "tok"

        sub = self._sub_doc()
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.stream.return_value = [sub]

        class _FakeResponse413:
            status_code = 413
        class _FakeException(Exception):
            response = _FakeResponse413()
        mock_pywebpush.WebPushException = _FakeException
        # First call raises 413, second call succeeds
        mock_pywebpush.webpush.side_effect = [_FakeException("Too Large"), None]

        # Use a reminder with a long body so payload > PAYLOAD_BODY_RETRY_LIMIT (256)
        # and the retry payload is measurably shorter.
        long_reminder = {
            "id": "rid_x", "name": "Standup",
            "instructions": "x" * 1000,
            "sourceChatId": "session_abc",
        }

        from app.services.push_dispatcher import send_push_for_reminder
        send_push_for_reminder(long_reminder, user_id="u1")

        assert mock_pywebpush.webpush.call_count == 2
        first_data = mock_pywebpush.webpush.call_args_list[0].kwargs.get("data", "")
        second_data = mock_pywebpush.webpush.call_args_list[1].kwargs.get("data", "")
        assert len(second_data) < len(first_data)

# tests/test_action_parser.py
"""Tests for ActionParser — replaces META tag parsing."""

import pytest


class TestActionParserChat:
    def test_parse_chat_action(self):
        from app.context.action_parser import ActionParser
        text = "ACTION: chat\n\nHere is my response about Python."
        result = ActionParser.parse(text)
        assert result.action == "chat"
        assert "Here is my response about Python." in result.chat_text
        assert result.artifact_payload is None
        assert result.patch_payload is None

    def test_strips_action_line_from_chat_text(self):
        from app.context.action_parser import ActionParser
        text = "ACTION: chat\n\nHello world"
        result = ActionParser.parse(text)
        assert "ACTION:" not in result.chat_text
        assert "Hello world" in result.chat_text


class TestActionParserCreateArtifact:
    def test_parse_create_artifact(self):
        from app.context.action_parser import ActionParser
        text = (
            'ACTION: create_artifact\n\n'
            'Building a counter app.\n\n'
            '```artifact\n'
            '{"title": "Counter", "entrypoint": "main.py", '
            '"files": [{"path": "main.py", "language": "python", "content": "x=1"}], '
            '"meta": {}}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None
        assert result.artifact_payload["title"] == "Counter"
        assert "Building a counter app" in result.chat_text
        assert "```artifact" not in result.chat_text

    def test_parse_create_artifact_without_action_line(self):
        """Fallback: artifact fence present but no ACTION line."""
        from app.context.action_parser import ActionParser
        text = (
            'Here is your app.\n\n'
            '```artifact\n'
            '{"title": "App", "entrypoint": "main.py", '
            '"files": [{"path": "main.py", "language": "python", "content": "x=1"}], '
            '"meta": {}}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None


class TestActionParserPatchArtifact:
    def test_parse_patch_artifact(self):
        from app.context.action_parser import ActionParser
        text = (
            'ACTION: patch_artifact\n\n'
            'Fixing the bug.\n\n'
            '```artifact_patch\n'
            '{"patches": [{"file": "main.py", "old_string": "x=1", "new_string": "x=2"}]}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "patch_artifact"
        assert result.patch_payload is not None
        assert len(result.patch_payload["patches"]) == 1
        assert "Fixing the bug" in result.chat_text
        assert "```artifact_patch" not in result.chat_text

    def test_parse_patch_without_action_line(self):
        """Fallback: artifact_patch fence but no ACTION line."""
        from app.context.action_parser import ActionParser
        text = (
            'Fixed it.\n\n'
            '```artifact_patch\n'
            '{"patches": [{"file": "main.py", "old_string": "x=1", "new_string": "x=2"}]}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "patch_artifact"
        assert result.patch_payload is not None


class TestActionParserFallback:
    def test_no_action_no_fence_defaults_to_chat(self):
        from app.context.action_parser import ActionParser
        text = "Just a normal response with some text."
        result = ActionParser.parse(text)
        assert result.action == "chat"
        assert result.chat_text == text

    def test_empty_response(self):
        from app.context.action_parser import ActionParser
        result = ActionParser.parse("")
        assert result.action == "chat"
        assert result.chat_text == ""

    def test_preserves_raw_response(self):
        from app.context.action_parser import ActionParser
        text = "ACTION: chat\n\nHello"
        result = ActionParser.parse(text)
        assert result.raw_response == text


class TestStreamingBuffer:
    def test_buffer_accumulates_until_newline(self):
        from app.context.action_parser import StreamingActionBuffer
        buf = StreamingActionBuffer()
        assert buf.feed("ACT") is None
        assert buf.feed("ION: patch_") is None
        result = buf.feed("artifact\nHere is")
        assert result == "patch_artifact"

    def test_buffer_returns_action_on_first_newline(self):
        from app.context.action_parser import StreamingActionBuffer
        buf = StreamingActionBuffer()
        result = buf.feed("ACTION: chat\nHello")
        assert result == "chat"
        assert buf.remainder == "Hello"

    def test_buffer_fallback_when_no_action(self):
        from app.context.action_parser import StreamingActionBuffer
        buf = StreamingActionBuffer()
        result = buf.feed("Just some text\nMore text")
        assert result == "chat"  # Default fallback
        assert "Just some text" in buf.remainder

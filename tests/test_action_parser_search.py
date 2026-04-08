"""Tests for ACTION: web_search parsing in ActionParser and StreamingActionBuffer."""

import sys
import importlib.util

# Import action_parser directly to avoid Firebase init from app/__init__.py
spec = importlib.util.spec_from_file_location('action_parser', 'app/context/action_parser.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['action_parser'] = mod
spec.loader.exec_module(mod)
ActionParser = mod.ActionParser
StreamingActionBuffer = mod.StreamingActionBuffer


class TestActionParserWebSearch:
    """Tests for ActionParser.parse() with web_search action."""

    def test_parses_web_search_action(self):
        response = (
            "ACTION: web_search\n"
            "query: best python web frameworks 2024\n"
            "search_type: general\n"
            "reason: user asked for framework recommendations\n"
            "\n"
            "Here are some results for you."
        )
        result = ActionParser.parse(response)
        assert result.action == "web_search"
        assert result.search_payload is not None
        assert result.search_payload["query"] == "best python web frameworks 2024"
        assert result.search_payload["search_type"] == "general"
        assert result.search_payload["reason"] == "user asked for framework recommendations"
        assert result.used_fallback is False

    def test_web_search_with_embed_video(self):
        response = (
            "ACTION: web_search\n"
            "query: how to make sourdough bread\n"
            "search_type: video\n"
            "reason: user wants video tutorial\n"
            "embed_video: dQw4w9WgXcQ\n"
            "\n"
        )
        result = ActionParser.parse(response)
        assert result.action == "web_search"
        assert result.search_payload is not None
        assert result.search_payload["embed_video"] == "dQw4w9WgXcQ"
        assert result.search_payload["search_type"] == "video"

    def test_web_search_defaults(self):
        """Missing search_type defaults to 'general', missing embed_video is None."""
        response = (
            "ACTION: web_search\n"
            "query: climate change news\n"
            "\n"
        )
        result = ActionParser.parse(response)
        assert result.action == "web_search"
        assert result.search_payload is not None
        assert result.search_payload["search_type"] == "general"
        assert result.search_payload["embed_video"] is None

    def test_web_search_missing_query_falls_back(self):
        """No query field → action falls back to 'chat'."""
        response = (
            "ACTION: web_search\n"
            "search_type: general\n"
            "reason: something\n"
            "\n"
            "Let me help you with that."
        )
        result = ActionParser.parse(response)
        assert result.action == "chat"
        assert result.search_payload is None

    def test_web_search_missing_reason_still_works(self):
        """Missing reason field defaults to empty string."""
        response = (
            "ACTION: web_search\n"
            "query: latest AI news\n"
            "search_type: news\n"
            "\n"
        )
        result = ActionParser.parse(response)
        assert result.action == "web_search"
        assert result.search_payload is not None
        assert result.search_payload["reason"] == ""
        assert result.search_payload["query"] == "latest AI news"


class TestStreamingBufferWebSearch:
    """Tests for StreamingActionBuffer with web_search action."""

    def test_chunk_by_chunk(self):
        """Feed headers one line at a time."""
        buf = StreamingActionBuffer()

        chunks = [
            "ACTION: web_search\n",
            "query: flask vs django\n",
            "search_type: general\n",
            "reason: comparison request\n",
            "\n",
        ]

        result = None
        for chunk in chunks:
            result = buf.feed(chunk)
            if result is not None:
                break

        assert result == "web_search"
        assert buf.header_fields["query"] == "flask vs django"
        assert buf.header_fields["search_type"] == "general"
        assert buf.header_fields["reason"] == "comparison request"

    def test_all_at_once(self):
        """Feed entire response in one call."""
        buf = StreamingActionBuffer()

        full_text = (
            "ACTION: web_search\n"
            "query: best coffee shops in NYC\n"
            "search_type: general\n"
            "reason: location query\n"
            "embed_video: \n"
            "\n"
            "Here are some great spots!"
        )

        result = buf.feed(full_text)
        assert result == "web_search"
        assert buf.header_fields["query"] == "best coffee shops in NYC"
        assert buf.header_fields["search_type"] == "general"
        assert buf.header_fields.get("embed_video", "") == ""
        assert "Here are some great spots!" in buf.remainder

"""Tests for StreamNormalizer."""
import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.stream_normalizer import StreamNormalizer


class TestBasicLineBuffering:
    def test_complete_line_passes_through(self):
        n = StreamNormalizer()
        result = n.feed("ACTION: web_search\n")
        assert result == ["ACTION: web_search\n"]

    def test_partial_line_held(self):
        n = StreamNormalizer()
        result = n.feed("ACT")
        assert result == []

    def test_partial_line_released_on_completion(self):
        n = StreamNormalizer()
        n.feed("ACT")
        n.feed("ION: web_search")
        result = n.feed("\n")
        assert result == ["ACTION: web_search\n"]

    def test_multiple_lines_in_one_chunk(self):
        n = StreamNormalizer()
        result = n.feed("ACTION: web_search\nquery: test\n")
        assert result == ["ACTION: web_search\n", "query: test\n"]

    def test_blank_line_passes_through(self):
        n = StreamNormalizer()
        result = n.feed("\n")
        assert result == ["\n"]

    def test_empty_chunk_returns_empty(self):
        n = StreamNormalizer()
        assert n.feed("") == []
        assert n.feed(None) == []


class TestHeaderFieldDetection:
    def test_field_without_newline_held_until_flush(self):
        """Fields without trailing \\n are held in buffer until flush()."""
        n = StreamNormalizer(model_family='deepseek')
        result = n.feed("query: weather in London today")
        assert result == []  # held
        result = n.flush()
        assert any("query: weather in London today" in c for c in result)

    def test_partial_field_name_not_flushed(self):
        n = StreamNormalizer(model_family='deepseek')
        result = n.feed("rea")
        assert result == []

    def test_incomplete_value_not_flushed(self):
        n = StreamNormalizer(model_family='deepseek')
        result = n.feed("query:")
        assert result == []

    def test_field_with_newline_passes_immediately(self):
        n = StreamNormalizer(model_family='deepseek')
        result = n.feed("query: test query\n")
        assert result == ["query: test query\n"]


class TestFlush:
    def test_flush_releases_remaining_buffer(self):
        n = StreamNormalizer(model_family='deepseek')
        n.feed("reason: user wants news")
        result = n.flush()
        assert any("reason: user wants news" in c for c in result)
        assert '\n' in result

    def test_flush_always_emits_blank_line(self):
        n = StreamNormalizer()
        result = n.flush()
        assert '\n' in result

    def test_flush_empty_buffer(self):
        n = StreamNormalizer()
        result = n.flush()
        assert result == ['\n']

    def test_flush_partial_line_on_non_flush_model(self):
        n = StreamNormalizer(model_family='openai')
        n.feed("reason: test reason")  # not flushed by feed for openai
        result = n.flush()
        assert any("reason: test reason" in c for c in result)
        assert '\n' in result


class TestDeepSeekSimulation:
    """Simulate DeepSeek's token-by-token streaming."""

    def test_token_by_token_action_block(self):
        n = StreamNormalizer(model_family='deepseek')
        tokens = [
            "ACTION", ":", " web", "_search", "\n",
            "query", ":", " weather", " in", " London", "\n",
            "search", "_type", ":", " news", "\n",
            "reason", ":", " user", " wants", " current", " news",
            # No trailing \n — DeepSeek drops it
        ]
        output = []
        for token in tokens:
            output.extend(n.feed(token))
        output.extend(n.flush())

        full = ''.join(output)
        assert "ACTION: web_search" in full
        assert "query: weather in London" in full
        assert "search_type: news" in full
        assert "reason: user wants current news" in full

    def test_content_text_passes_through_unchanged(self):
        n = StreamNormalizer(model_family='deepseek')
        result = []
        for token in ["Here", " is", " the", " weather", " today"]:
            result.extend(n.feed(token))
        result.extend(n.flush())
        assert "Here is the weather today" in ''.join(result)


class TestAllModelFamilies:
    """Verify normalizer works for every model family."""

    def test_all_families_handle_complete_block(self):
        block = (
            "ACTION: web_search\n"
            "query: test query\n"
            "search_type: general\n"
            "reason: testing\n"
            "\n"
        )
        families = ['deepseek', 'llama', 'openai', 'kimi', 'qwen', 'default']
        for family in families:
            n = StreamNormalizer(model_family=family)
            output = []
            for char in block:
                output.extend(n.feed(char))
            output.extend(n.flush())
            full = ''.join(output)
            assert "ACTION: web_search" in full, f"Failed for {family}"
            assert "query: test query" in full, f"Failed for {family}"

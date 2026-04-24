"""Tests for server-side prose finalize transforms.

These mirror app/static/js/stream/renderers/prose.js transforms
(stripTrailingPreamble, wrapInlineQuotesAsBlockquotes) so persisted
response_text matches the post-finalize DOM. The client still applies
these transforms on replay — keeping them idempotent is the contract.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.utilities import (
    strip_trailing_ai_preamble,
    wrap_inline_quotes_as_blockquotes,
)


class TestStripTrailingAIPreamble:
    def test_strips_how_can_i_help_tail(self):
        assert (
            strip_trailing_ai_preamble("Here is the answer. How can I help you today?")
            == "Here is the answer."
        )

    def test_strips_how_may_i_assist_variant(self):
        assert (
            strip_trailing_ai_preamble("Got it. How may I assist you today?")
            == "Got it."
        )

    def test_strips_let_me_know_if_tail(self):
        assert (
            strip_trailing_ai_preamble("Done. Let me know if you need anything else.")
            == "Done."
        )

    def test_strips_is_there_anything_else(self):
        assert (
            strip_trailing_ai_preamble("Result shown. Is there anything else I can help with?")
            == "Result shown."
        )

    def test_preserves_text_without_preamble(self):
        text = "This is a regular answer with no trailing fluff."
        assert strip_trailing_ai_preamble(text) == text

    def test_idempotent_on_already_stripped(self):
        once = strip_trailing_ai_preamble("Answer. How can I help you today?")
        twice = strip_trailing_ai_preamble(once)
        assert once == twice

    def test_idempotent_on_clean_text(self):
        text = "No preamble here."
        assert strip_trailing_ai_preamble(text) == text
        assert strip_trailing_ai_preamble(strip_trailing_ai_preamble(text)) == text

    def test_trailing_comma_replaced_by_period(self):
        # prose.js does the same: leading "Hello," → "Hello." after preamble strip.
        result = strip_trailing_ai_preamble("Hello, how can I help you today?")
        assert result == "Hello."

    def test_empty_and_none_safe(self):
        assert strip_trailing_ai_preamble("") == ""
        assert strip_trailing_ai_preamble(None) is None


class TestWrapInlineQuotesAsBlockquotes:
    def test_promotes_long_inline_quote(self):
        text = (
            'Einstein famously said "Imagination is more important than knowledge '
            'for knowledge is limited" — that quote has shaped physics for decades.'
        )
        out = wrap_inline_quotes_as_blockquotes(text)
        assert "> Imagination is more important than knowledge" in out

    def test_skips_short_quotes(self):
        text = 'He said "yes" and walked away.'
        assert wrap_inline_quotes_as_blockquotes(text) == text

    def test_skips_paragraphs_already_blockquote(self):
        text = "> Existing blockquote with long content inside the arrow syntax."
        assert wrap_inline_quotes_as_blockquotes(text) == text

    def test_idempotent_on_promoted_quote(self):
        text = (
            'The author wrote "Life is what happens to you while you are busy '
            'making other plans" in a letter to his son.'
        )
        once = wrap_inline_quotes_as_blockquotes(text)
        twice = wrap_inline_quotes_as_blockquotes(once)
        assert once == twice, (
            "non-idempotent transform would re-promote or mangle on replay"
        )

    def test_respects_prose_ratio_guard(self):
        # Paragraph with very long prose and medium-length quoted term — not promoted.
        # Crafted so the quote is just over 35 chars but prose around it is >3x length.
        long_prose_prefix = "In economics, " + "this is a very long sentence with many facts and details " * 5
        text = f'{long_prose_prefix}we note "The invisible hand guides market equilibrium" as a concept.'
        out = wrap_inline_quotes_as_blockquotes(text)
        # Ratio guard: prose length vs quote length. Quote is ~47 chars, prose is
        # hundreds of chars — ratio exceeds 3x, so the quote stays inline.
        assert ">" not in out.split('\n')[0], "long-prose paragraph should not be promoted"

    def test_multi_paragraph_handled_per_paragraph(self):
        text = (
            'First paragraph with "Short quote."\n\n'
            'Second paragraph where Author says "This is a substantial literary '
            'quote that deserves promotion" and continues.'
        )
        out = wrap_inline_quotes_as_blockquotes(text)
        assert "Short quote." in out  # short one stays inline
        assert "> This is a substantial literary quote" in out  # long one promoted

    def test_empty_and_none_safe(self):
        assert wrap_inline_quotes_as_blockquotes("") == ""
        assert wrap_inline_quotes_as_blockquotes(None) is None

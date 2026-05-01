"""Tests for response_style — six-style content classifier."""

import pytest


class TestResponseStyleEnum:
    def test_enum_has_six_values(self):
        from app.services.response_style import ResponseStyle
        values = {s.value for s in ResponseStyle}
        assert values == {"editorial", "modern_blog", "technical", "chat", "comparison", "creative"}

    def test_default_is_modern_blog(self):
        from app.services.response_style import ResponseStyle
        assert ResponseStyle.default() == ResponseStyle.MODERN_BLOG

    def test_enum_string_value(self):
        from app.services.response_style import ResponseStyle
        # str-Enum so values can be safely JSON-serialised
        assert ResponseStyle.EDITORIAL == "editorial"
        assert isinstance(ResponseStyle.EDITORIAL.value, str)


class TestClassifyByRules:
    """Regex fast path — first-match-wins, returns None on no match."""

    @pytest.mark.parametrize("msg, expected", [
        # Greetings / chat / very short
        ("hi", "chat"),
        ("hello", "chat"),
        ("hey", "chat"),
        ("thanks!", "chat"),
        ("thank you", "chat"),
        ("ok", "chat"),
        ("sure", "chat"),
        ("yes", "chat"),
        ("nope", "chat"),
        ("yo", "chat"),
        ("", "chat"),
        ("?", "chat"),
        # Comparison
        ("react vs vue?", "comparison"),
        ("python versus ruby for backend", "comparison"),
        ("which is better postgres or mysql", "comparison"),
        ("differences between rest and graphql", "comparison"),
        ("should I use docker or podman", "comparison"),
        # Creative
        ("write me a story about a lighthouse keeper", "creative"),
        ("write a haiku about coffee", "creative"),
        ("describe a scene in tokyo at night", "creative"),
        ("tell me a story about loss", "creative"),
        # Technical
        ("write a python function to debounce calls", "technical"),
        ("fix this bug in my react component", "technical"),
        ("debug the migration that's failing", "technical"),
        ("implement a stack class", "technical"),
        # Tips / how-to → modern_blog
        ("give me tips on time management", "modern_blog"),
        ("how do i improve my sleep", "modern_blog"),
        ("ways to reduce anxiety", "modern_blog"),
        ("best practices for code review", "modern_blog"),
        # Editorial — explain longform
        ("explain how transformer attention works in detail and why scaled dot product was chosen", "editorial"),
        ("why does the moon affect the tides over geological timescales", "editorial"),
        # Ambiguous → None (defer to LLM)
        ("the vibes are off in this codebase", None),
        ("anything good lately", None),
        ("not sure where to start", None),
    ])
    def test_classifies_correctly(self, msg, expected):
        from app.services.response_style import classify_by_rules, ResponseStyle
        result = classify_by_rules(msg)
        if expected is None:
            assert result is None
        else:
            assert result == ResponseStyle(expected), f"input={msg!r} got={result}"

    def test_code_fence_in_message_is_technical(self):
        from app.services.response_style import classify_by_rules, ResponseStyle
        msg = "I see this error:\n```\nTypeError: undefined\n```\nwhat's wrong?"
        assert classify_by_rules(msg) == ResponseStyle.TECHNICAL

    def test_in_python_at_end_is_technical(self):
        from app.services.response_style import classify_by_rules, ResponseStyle
        assert classify_by_rules("how do i sort a list in python") == ResponseStyle.TECHNICAL

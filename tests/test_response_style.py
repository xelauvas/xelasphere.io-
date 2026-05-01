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

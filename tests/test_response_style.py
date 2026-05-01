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


class TestStyleCharters:
    def test_every_style_has_a_charter(self):
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        assert set(STYLE_CHARTERS.keys()) == set(ResponseStyle)

    def test_charter_mentions_its_style_name(self):
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        for style, charter in STYLE_CHARTERS.items():
            # Charter should name-drop the style so the model has a label
            # to ground its behaviour against.
            assert style.value.upper().replace("_", " ") in charter.upper() or \
                   style.value.upper() in charter.upper(), (
                       f"{style.value} charter does not reference its name"
                   )

    def test_charter_size_under_3000_chars(self):
        # Charters are appended to the existing ~3000-line dev instructions.
        # Cap each at 3000 chars so the system prompt stays under model limits.
        from app.services.response_style import STYLE_CHARTERS
        for style, charter in STYLE_CHARTERS.items():
            assert len(charter) < 3000, f"{style.value} charter is {len(charter)} chars"

    def test_chat_charter_forbids_headings(self):
        # Smoke that the chat charter actually says "no headings"
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        chat = STYLE_CHARTERS[ResponseStyle.CHAT].lower()
        assert "no heading" in chat or "no ##" in chat or "plain prose" in chat

    def test_creative_charter_forbids_emoji(self):
        from app.services.response_style import STYLE_CHARTERS, ResponseStyle
        creative = STYLE_CHARTERS[ResponseStyle.CREATIVE].lower()
        assert "no emoji" in creative or "without emoji" in creative


class TestClassifyResponseStyle:
    """Orchestrator: regex fast-path → LLM fallback → default."""

    def test_uses_regex_when_it_matches(self, monkeypatch):
        from app.services import response_style as rs
        called = {"llm": False}
        def _no_llm(msg):
            called["llm"] = True
            return None
        monkeypatch.setattr(rs, "_classify_via_llm", _no_llm)

        result = rs.classify_response_style("hi there")
        assert result == rs.ResponseStyle.CHAT
        assert called["llm"] is False, "regex matched; LLM should not have run"

    def test_falls_back_to_llm_on_ambiguous(self, monkeypatch):
        from app.services import response_style as rs
        monkeypatch.setattr(rs, "_classify_via_llm",
                            lambda msg: rs.ResponseStyle.EDITORIAL)
        result = rs.classify_response_style("the vibes are off in this codebase")
        assert result == rs.ResponseStyle.EDITORIAL

    def test_returns_default_when_llm_fails(self, monkeypatch):
        from app.services import response_style as rs
        monkeypatch.setattr(rs, "_classify_via_llm", lambda msg: None)
        result = rs.classify_response_style("the vibes are off in this codebase")
        assert result == rs.ResponseStyle.default()  # MODERN_BLOG

    def test_returns_default_on_empty_input(self, monkeypatch):
        from app.services import response_style as rs
        monkeypatch.setattr(rs, "_classify_via_llm", lambda msg: None)
        # empty falls through regex (returns CHAT) before reaching LLM
        assert rs.classify_response_style("") == rs.ResponseStyle.CHAT

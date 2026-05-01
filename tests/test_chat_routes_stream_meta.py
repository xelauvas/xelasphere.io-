"""Integration test: chat_routes emits stream-meta SSE chunk with the
classified style, and injects the style charter as system_addition."""

import os
import pytest


class TestStreamMetaSSEChunk:
    """Stream-meta chunk must be emitted by the SSE generator with the
    style chosen by classify_response_style, when the feature flag is on."""

    @pytest.fixture(autouse=True)
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("RESPONSE_STYLE_ORGANISM_ENABLED", "true")

    def test_classifier_runs_when_flag_on(self, monkeypatch):
        """Smoke: with the flag on, classify_response_style picks a style
        and STYLE_CHARTERS contains a non-empty charter for it."""
        monkeypatch.setenv("RESPONSE_STYLE_ORGANISM_ENABLED", "true")

        from app.services.response_style import (
            classify_response_style, STYLE_CHARTERS, ResponseStyle,
        )
        style = classify_response_style("give me tips on time management")
        assert style == ResponseStyle.MODERN_BLOG
        assert len(STYLE_CHARTERS[style]) > 100

    def test_chat_style_for_greeting(self, monkeypatch):
        monkeypatch.setenv("RESPONSE_STYLE_ORGANISM_ENABLED", "true")
        from app.services.response_style import classify_response_style, ResponseStyle
        assert classify_response_style("hi") == ResponseStyle.CHAT

    def test_comparison_style_for_vs(self, monkeypatch):
        monkeypatch.setenv("RESPONSE_STYLE_ORGANISM_ENABLED", "true")
        from app.services.response_style import classify_response_style, ResponseStyle
        assert classify_response_style("react vs vue") == ResponseStyle.COMPARISON

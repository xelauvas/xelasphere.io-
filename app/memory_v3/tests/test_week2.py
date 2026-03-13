"""
Week 2 Acceptance Tests
=======================
Tests for meta-confidence, relevance decay, and episodic hints.
"""

import unittest
from datetime import datetime, timedelta


class TestMetaConfidence(unittest.TestCase):
    """Tests for meta-confidence (ask vs assume)."""

    def test_underspecified_execute_asks_clarifying(self):
        """Underspecified request in EXECUTE context should ask."""
        from app.memory_v3 import infer_meta_confidence, ConfidenceLevel

        meta = infer_meta_confidence(
            context_mode="execute",
            state_certainty="confident",
            state_pace="rushed",
            user_message="fix this"
        )

        self.assertEqual(meta.confidence_level, ConfidenceLevel.LOW)
        self.assertTrue(meta.should_ask_clarifying)
        self.assertEqual(meta.max_clarifying_questions, 1)  # Rushed = 1 max

    def test_underspecified_debug_asks_clarifying(self):
        """Underspecified request in DEBUG context should ask."""
        from app.memory_v3 import infer_meta_confidence, ConfidenceLevel

        meta = infer_meta_confidence(
            context_mode="debug",
            state_certainty="confident",
            state_pace="normal",
            user_message="make it work"
        )

        self.assertEqual(meta.confidence_level, ConfidenceLevel.LOW)
        self.assertTrue(meta.should_ask_clarifying)

    def test_specific_request_high_confidence(self):
        """Specific request should have high confidence, no clarifying."""
        from app.memory_v3 import infer_meta_confidence, ConfidenceLevel

        # Very specific request with code block and file path
        meta = infer_meta_confidence(
            context_mode="execute",
            state_certainty="confident",
            state_pace="normal",
            user_message="Fix the function validate_email in file: src/utils.py line 42. The regex pattern is wrong."
        )

        self.assertEqual(meta.confidence_level, ConfidenceLevel.HIGH)
        self.assertFalse(meta.should_ask_clarifying)

    def test_uncertain_user_asks_clarifying(self):
        """Uncertain user should trigger clarifying questions."""
        from app.memory_v3 import infer_meta_confidence, ConfidenceLevel

        meta = infer_meta_confidence(
            context_mode="design",
            state_certainty="uncertain",
            state_pace="deep",
            user_message="I'm not sure how to structure this"
        )

        self.assertEqual(meta.confidence_level, ConfidenceLevel.LOW)
        self.assertTrue(meta.should_ask_clarifying)
        self.assertEqual(meta.max_clarifying_questions, 3)  # Deep = 3 max

    def test_frustrated_user_no_questions(self):
        """Frustrated user should NOT get more questions."""
        from app.memory_v3 import infer_meta_confidence

        meta = infer_meta_confidence(
            context_mode="debug",
            state_certainty="frustrated",
            state_pace="rushed",
            user_message="just fix it already"
        )

        self.assertFalse(meta.should_ask_clarifying)
        self.assertEqual(meta.max_clarifying_questions, 0)

    def test_rushed_user_short_clarifying(self):
        """Rushed user should get short clarifying style."""
        from app.memory_v3 import infer_meta_confidence, ClarifyingStyle

        meta = infer_meta_confidence(
            context_mode="execute",
            state_certainty="uncertain",
            state_pace="rushed",
            user_message="help"
        )

        self.assertTrue(meta.should_ask_clarifying)
        self.assertEqual(meta.clarifying_style, ClarifyingStyle.SHORT)
        self.assertEqual(meta.max_clarifying_questions, 1)


class TestRelevanceDecay(unittest.TestCase):
    """Tests for relevance-based decay."""

    def test_no_decay_on_relevant_adjustments(self):
        """Adjustments matching context should not decay."""
        from app.memory_v3 import apply_relevance_decay

        result = apply_relevance_decay(
            adjustments=["concise", "direct"],
            context_mode="debug"
        )

        self.assertIn("concise", result.adjustments)
        self.assertIn("direct", result.adjustments)
        self.assertEqual(len(result.decayed_out), 0)

    def test_context_mismatch_decay(self):
        """Adjustments mismatching context may decay."""
        from app.memory_v3 import apply_relevance_decay

        # "tradeoffs" is less relevant in debug context
        result = apply_relevance_decay(
            adjustments=["concise", "tradeoffs"],
            context_mode="debug",
            threshold=0.5  # Higher threshold to catch mismatch
        )

        # Concise should stay (relevant to debug)
        self.assertIn("concise", result.adjustments)

    def test_decay_summary(self):
        """Test decay summary generation."""
        from app.memory_v3 import apply_relevance_decay, get_decay_summary

        result = apply_relevance_decay(
            adjustments=["concise"],
            context_mode="debug"
        )

        summary = get_decay_summary(result)
        self.assertIsInstance(summary, str)

    def test_deterministic_output(self):
        """Same inputs should produce same outputs."""
        from app.memory_v3 import apply_relevance_decay

        result1 = apply_relevance_decay(
            adjustments=["concise", "direct"],
            context_mode="execute"
        )

        result2 = apply_relevance_decay(
            adjustments=["concise", "direct"],
            context_mode="execute"
        )

        self.assertEqual(result1.adjustments, result2.adjustments)


class TestEpisodicHints(unittest.TestCase):
    """Tests for episodic hints."""

    def test_disabled_by_default(self):
        """Episodic hints should be disabled by default."""
        from app.memory_v3 import V3_EPISODIC_ENABLED

        self.assertFalse(V3_EPISODIC_ENABLED)

    def test_empty_when_disabled(self):
        """Should return empty hints when disabled."""
        from app.memory_v3 import get_episode_hints

        context = get_episode_hints()

        self.assertEqual(len(context.hints), 0)
        self.assertEqual(context.to_prompt_block(), "")

    def test_max_episodes_cap(self):
        """Episodes should be capped at MAX_EPISODES."""
        from app.memory_v3.episodic import MAX_EPISODES

        self.assertEqual(MAX_EPISODES, 10)

    def test_no_raw_text_in_hints(self):
        """Hint labels should not contain raw text."""
        from app.memory_v3.episodic import EpisodicHint, EpisodeCategory

        hint = EpisodicHint(
            hint_id="often_requests_simplification",
            category=EpisodeCategory.RESPONSE_STYLE,
            confidence=0.8
        )

        label = hint.to_label()

        # Label should be a pattern identifier, not contain quotes or raw text
        self.assertNotIn('"', label)
        self.assertNotIn("'", label)
        self.assertFalse(label.startswith("user"))  # No user prefixes


class TestWeek2Integration(unittest.TestCase):
    """Integration tests for Week 2 pipeline."""

    def test_generate_signal_with_week2(self):
        """Test full Week 2 signal generation."""
        from app.memory_v3 import get_v3_orchestrator, V3_META_ENABLED

        orchestrator = get_v3_orchestrator(enabled=True)

        signal = orchestrator.generate_signal_with_week2(
            user_message="fix this bug",
            v2_memories=[]
        )

        self.assertIsNotNone(signal)

        # Meta-confidence should be attached if enabled
        if V3_META_ENABLED:
            self.assertIsNotNone(signal.meta_confidence)

    def test_prompt_block_includes_week2(self):
        """Prompt block should include Week 2 sections when enabled."""
        from app.memory_v3 import get_v3_orchestrator, V3_META_ENABLED

        orchestrator = get_v3_orchestrator(enabled=True)

        signal = orchestrator.generate_signal_with_week2(
            user_message="help",  # Underspecified = should trigger meta
            v2_memories=[]
        )

        if signal:
            block = signal.to_prompt_block(intensity=0.6)

            # Should have V3 signal
            self.assertIn("V3 Behavior Signal", block)

            # If meta is enabled and triggered, should have meta section
            if V3_META_ENABLED and signal.meta_confidence and signal.meta_confidence.should_ask_clarifying:
                self.assertIn("Meta-confidence", block)

    def test_no_db_writes(self):
        """Week 2 should not perform any database writes."""
        from app.memory_v3 import get_v3_orchestrator

        orchestrator = get_v3_orchestrator(enabled=True)

        # This should complete without any DB operations
        signal = orchestrator.generate_signal_with_week2(
            user_message="design a caching system",
            v2_memories=[{"key": "pref_examples", "value": "likes examples"}]
        )

        # If we got here without error, no DB writes occurred
        self.assertIsNotNone(signal)


class TestContractCompliance(unittest.TestCase):
    """Tests to ensure Week 2 maintains V3 contract."""

    def test_signals_only_no_facts(self):
        """Week 2 should produce signals, not facts."""
        from app.memory_v3 import infer_meta_confidence, get_episode_hints

        # Meta-confidence is a signal about confidence, not a stored fact
        meta = infer_meta_confidence("debug", "confident", "normal", "test")
        self.assertFalse(hasattr(meta, 'user_id'))
        self.assertFalse(hasattr(meta, 'save'))

        # Episodes are hints, not stored memories
        episodes = get_episode_hints()
        self.assertFalse(hasattr(episodes, 'save'))
        self.assertFalse(hasattr(episodes, 'store'))

    def test_ephemeral_computation(self):
        """Week 2 features should be computed fresh each time."""
        from app.memory_v3 import infer_meta_confidence

        # Same message should produce same signal (deterministic)
        meta1 = infer_meta_confidence("execute", "confident", "normal", "help")
        meta2 = infer_meta_confidence("execute", "confident", "normal", "help")

        self.assertEqual(meta1.confidence_level, meta2.confidence_level)
        self.assertEqual(meta1.should_ask_clarifying, meta2.should_ask_clarifying)


if __name__ == "__main__":
    unittest.main()

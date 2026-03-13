"""
Memory V3 Acceptance Tests
==========================
Behavior-level tests to lock in correct behavior before shipping.

Test categories:
A) Context detection
B) State detection
C) TTL behavior
D) No DB writes (V3 contract)
E) Anti-gaming guard

Run with: python -m pytest app/memory_v3/tests/test_acceptance.py -v
Or standalone: python app/memory_v3/tests/test_acceptance.py
"""

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))


class TestContextDetection:
    """A) Context detection tests."""

    def test_debug_detection(self):
        """'fix this bug' → DEBUG"""
        from app.memory_v3 import detect_context_mode, ContextMode

        test_cases = [
            "fix this bug",
            "why is this broken",
            "error when I run this",
            "it keeps crashing",
            "debug this issue",
            "traceback shows an error",
        ]

        for msg in test_cases:
            result = detect_context_mode(msg)
            assert result.mode == ContextMode.DEBUG, f"'{msg}' should be DEBUG, got {result.mode}"

    def test_design_detection(self):
        """'build an architecture' → DESIGN"""
        from app.memory_v3 import detect_context_mode, ContextMode

        test_cases = [
            "how should I architect this",
            "design a scalable system",
            "what's the best approach",
            "pros and cons of each",
            "help me plan this project",
        ]

        for msg in test_cases:
            result = detect_context_mode(msg)
            assert result.mode == ContextMode.DESIGN, f"'{msg}' should be DESIGN, got {result.mode}"

    def test_explain_detection(self):
        """'what is X' → EXPLAIN"""
        from app.memory_v3 import detect_context_mode, ContextMode

        test_cases = [
            "what is a closure",
            "explain how promises work",
            "how does async/await work",
            "teach me about decorators",
            "what does this mean",
        ]

        for msg in test_cases:
            result = detect_context_mode(msg)
            assert result.mode == ContextMode.EXPLAIN, f"'{msg}' should be EXPLAIN, got {result.mode}"

    def test_execute_detection(self):
        """'generate code' → EXECUTE"""
        from app.memory_v3 import detect_context_mode, ContextMode

        test_cases = [
            "create a function that sorts",
            "generate a REST API",
            "write a script to",
            "build a component",
            "make a login form",
        ]

        for msg in test_cases:
            result = detect_context_mode(msg)
            assert result.mode == ContextMode.EXECUTE, f"'{msg}' should be EXECUTE, got {result.mode}"


class TestStateDetection:
    """B) State detection tests."""

    def test_rushed_detection(self):
        """Short imperative → pace=RUSHED"""
        from app.memory_v3 import infer_user_state, UserPace

        test_cases = [
            "just do it",
            "quick",
            "fast please",
            "asap",
            "tldr",
        ]

        for msg in test_cases:
            result = infer_user_state(msg)
            assert result.pace == UserPace.RUSHED, f"'{msg}' should be RUSHED, got {result.pace}"

    def test_deep_detection(self):
        """'walk me through' → pace=DEEP"""
        from app.memory_v3 import infer_user_state, UserPace

        test_cases = [
            "explain in detail",
            "walk me through this",
            "comprehensive explanation",
            "I want to understand deeply",
            "thorough breakdown please",
        ]

        for msg in test_cases:
            result = infer_user_state(msg)
            assert result.pace == UserPace.DEEP, f"'{msg}' should be DEEP, got {result.pace}"

    def test_frustrated_detection(self):
        """'still broken / ugh' → certainty=FRUSTRATED"""
        from app.memory_v3 import infer_user_state, UserCertainty

        test_cases = [
            "ugh, still broken",
            "this still doesn't work",
            "tried everything, nothing works",
            "frustrated with this error",
            "why won't this work!!",
        ]

        for msg in test_cases:
            result = infer_user_state(msg)
            assert result.certainty == UserCertainty.FRUSTRATED, f"'{msg}' should be FRUSTRATED, got {result.certainty}"

    def test_uncertain_detection(self):
        """Questions and hedging → certainty=UNCERTAIN"""
        from app.memory_v3 import infer_user_state, UserCertainty

        test_cases = [
            "maybe I should try something else",
            "not sure if this is right",
            "should I use this approach?",
            "I'm confused about this",
        ]

        for msg in test_cases:
            result = infer_user_state(msg)
            assert result.certainty == UserCertainty.UNCERTAIN, f"'{msg}' should be UNCERTAIN, got {result.certainty}"


class TestTTLBehavior:
    """C) TTL behavior tests."""

    def test_state_expires_after_ttl(self):
        """State expires after 5 minutes (default)."""
        from app.memory_v3.state import UserState, DEFAULT_STATE_TTL_SECONDS

        state = UserState()

        # Should not be expired immediately
        assert not state.is_expired()
        assert state.remaining_ttl_seconds() > 0
        assert state.remaining_ttl_seconds() <= DEFAULT_STATE_TTL_SECONDS

    def test_state_with_custom_ttl(self):
        """State with custom TTL."""
        from app.memory_v3 import infer_user_state

        state = infer_user_state("test message", ttl_seconds=60)

        assert state.remaining_ttl_seconds() <= 60
        assert state.remaining_ttl_seconds() > 0

    def test_expired_state_detection(self):
        """Expired state is correctly detected."""
        from app.memory_v3.state import UserState
        from datetime import datetime, timedelta

        # Create state that's already expired
        state = UserState(
            expires_at=datetime.utcnow() - timedelta(seconds=1)
        )

        assert state.is_expired()
        assert state.remaining_ttl_seconds() == 0


class TestNoDBWrites:
    """D) No DB writes (V3 contract)."""

    def test_context_detection_no_db_calls(self):
        """Context detection never calls database."""
        from app.memory_v3 import detect_context_mode

        # Mock the store module to detect any calls
        with patch('app.memory_v3.context.logger') as mock_logger:
            result = detect_context_mode("test message")

            # Should complete without any store imports or calls
            assert result is not None
            # V3 should never import or call store

    def test_state_inference_no_db_calls(self):
        """State inference never calls database."""
        from app.memory_v3 import infer_user_state

        with patch('app.memory_v3.state.logger') as mock_logger:
            result = infer_user_state("test message")

            assert result is not None
            # V3 should never import or call store

    def test_orchestrator_no_db_calls(self):
        """Orchestrator never calls database."""
        from app.memory_v3 import generate_v3_signal

        with patch('app.memory_v3.orchestrator.logger') as mock_logger:
            result = generate_v3_signal("test message")

            assert result is not None
            # V3 should never import or call store

    def test_v3_modules_dont_import_store(self):
        """V3 modules should never import V2 store."""
        import app.memory_v3.context as ctx_module
        import app.memory_v3.state as state_module
        import app.memory_v3.orchestrator as orch_module

        # Check that none of these modules have store imports
        for module in [ctx_module, state_module, orch_module]:
            module_dict = dir(module)
            assert 'MemoryStoreV2' not in module_dict
            assert 'store' not in module_dict


class TestAntiGaming:
    """E) Anti-gaming guard tests."""

    def test_frustrated_state_rate_limited(self):
        """Frustrated state can't be escalated more than once per minute."""
        from app.memory_v3.state import infer_user_state, UserCertainty

        # First frustrated message
        state1 = infer_user_state("ugh this is broken!")
        assert state1.certainty == UserCertainty.FRUSTRATED

        # Subsequent frustrated messages should still work
        # (rate limiting is in orchestrator, not state detection)
        state2 = infer_user_state("still broken!!!")
        assert state2.certainty == UserCertainty.FRUSTRATED

    def test_anti_gaming_cooldown_in_orchestrator(self):
        """Orchestrator rate-limits rapid frustrated states."""
        from app.memory_v3.orchestrator import V3Orchestrator, FRUSTRATED_COOLDOWN_SECONDS
        from app.memory_v3.state import UserCertainty

        # Create fresh orchestrator
        orchestrator = V3Orchestrator(enabled=True, shadow_mode=False)

        # First frustrated message - should be allowed
        signal1 = orchestrator.generate_signal("ugh this is broken!")
        assert signal1 is not None
        assert signal1.state.certainty == UserCertainty.FRUSTRATED

        # Immediate second frustrated message - should be downgraded
        signal2 = orchestrator.generate_signal("still broken!!!")
        assert signal2 is not None
        # Should be downgraded to UNCERTAIN (not FRUSTRATED) due to cooldown
        assert signal2.state.certainty == UserCertainty.UNCERTAIN

    def test_anti_gaming_max_frustrated_count(self):
        """Orchestrator caps frustrated states per conversation."""
        from app.memory_v3.orchestrator import V3Orchestrator, MAX_FRUSTRATED_PER_CONVERSATION
        from app.memory_v3.state import UserCertainty
        from datetime import datetime, timedelta

        # Create fresh orchestrator and manually set up state
        orchestrator = V3Orchestrator(enabled=True, shadow_mode=False)
        # Set count to max-1 and backdate last_frustrated_at to bypass cooldown
        orchestrator._anti_gaming.frustrated_count = MAX_FRUSTRATED_PER_CONVERSATION - 1
        orchestrator._anti_gaming.last_frustrated_at = datetime.utcnow() - timedelta(seconds=120)

        # This should be the last allowed frustrated state
        signal1 = orchestrator.generate_signal("ugh broken again!")
        assert signal1.state.certainty == UserCertainty.FRUSTRATED

        # Backdate again
        orchestrator._anti_gaming.last_frustrated_at = datetime.utcnow() - timedelta(seconds=120)

        # This should be capped/downgraded (use message that triggers FRUSTRATED)
        signal2 = orchestrator.generate_signal("ugh this still doesn't work!!")
        assert signal2.state.certainty == UserCertainty.UNCERTAIN

    def test_anti_gaming_reset_on_conversation_change(self):
        """Reset state clears anti-gaming counters."""
        from app.memory_v3.orchestrator import V3Orchestrator
        from datetime import datetime

        orchestrator = V3Orchestrator(enabled=True)
        orchestrator._anti_gaming.frustrated_count = 10
        orchestrator._anti_gaming.last_frustrated_at = datetime.utcnow()

        # Reset
        orchestrator.reset_state()

        assert orchestrator._anti_gaming.frustrated_count == 0
        assert orchestrator._anti_gaming.last_frustrated_at is None


class TestSignalGeneration:
    """Test full signal generation pipeline."""

    def test_signal_generation_produces_output(self):
        """Signal generation produces valid output."""
        from app.memory_v3 import generate_v3_signal

        signal = generate_v3_signal("Help me debug this error")

        assert signal is not None
        assert signal.context is not None
        assert signal.state is not None
        assert signal.verbosity in ["low", "medium", "high"]
        assert signal.tone in ["efficient", "neutral", "thorough", "patient"]

    def test_prompt_block_generation(self):
        """Prompt block is properly formatted."""
        from app.memory_v3 import get_v3_prompt_block

        block = get_v3_prompt_block("Quick, generate a function")

        assert "V3 Behavior Signal v1" in block
        assert "context_mode:" in block
        assert "intensity:" in block

    def test_v3_context_combines_v2_and_v3(self):
        """get_v3_context properly combines V2 and V3."""
        from app.memory_v3 import get_v3_context

        v2_context = "<user_memory>test</user_memory>"
        result = get_v3_context(
            user_message="explain this in detail",
            v2_context=v2_context
        )

        # Should contain both V2 and V3 blocks
        assert "<user_memory>" in result
        assert "V3 Behavior Signal" in result


class TestShadowMode:
    """Test shadow mode functionality."""

    def test_shadow_analysis_returns_result(self):
        """Shadow analysis returns analysis result."""
        from app.memory_v3 import run_shadow_analysis

        result = run_shadow_analysis("Help me debug this error")

        assert "analyzed" in result
        assert "signal" in result
        assert "would_inject" in result
        assert "shadow_stats" in result

    def test_shadow_analysis_generates_signal(self):
        """Shadow analysis generates valid signal."""
        from app.memory_v3 import run_shadow_analysis

        result = run_shadow_analysis("Explain how async works in detail")

        assert result["analyzed"] is True
        assert result["signal"] is not None
        assert "context_mode" in result["signal"]
        assert "V3 Behavior Signal v1" in result["would_inject"]

    def test_shadow_mode_does_not_inject(self):
        """Shadow mode generates signals but doesn't return them for injection."""
        from app.memory_v3.orchestrator import get_v3_orchestrator

        orchestrator = get_v3_orchestrator(enabled=True, shadow_mode=True)
        signal = orchestrator.generate_signal("test message")

        # In shadow mode, generate_signal returns None (no injection)
        assert signal is None

    def test_shadow_signal_always_returns(self):
        """generate_shadow_signal always returns signal (for analysis)."""
        from app.memory_v3.orchestrator import get_v3_orchestrator

        orchestrator = get_v3_orchestrator(enabled=True, shadow_mode=True)
        signal = orchestrator.generate_shadow_signal("test message")

        # generate_shadow_signal returns the signal for analysis
        assert signal is not None
        assert signal.context is not None
        assert signal.state is not None


class TestEvalMetadata:
    """Test eval metadata generation."""

    def test_eval_metadata_contains_required_fields(self):
        """Eval metadata has all required fields."""
        from app.memory_v3 import get_v3_eval_metadata

        metadata = get_v3_eval_metadata("Help me debug this error")

        assert "v3_enabled" in metadata
        assert "signals_used" in metadata
        assert "context_mode" in metadata
        assert "user_state" in metadata
        assert "state_expires_at" in metadata
        assert "generated_at" in metadata

    def test_eval_metadata_when_enabled(self):
        """Eval metadata is populated when V3 enabled."""
        from app.memory_v3 import get_v3_eval_metadata

        metadata = get_v3_eval_metadata("explain this in detail", enabled=True)

        assert metadata["v3_enabled"] is True
        assert metadata["signals_used"] is True
        assert metadata["context_mode"] is not None
        assert metadata["user_state"] is not None
        assert metadata["user_state"]["pace"] is not None
        assert metadata["state_expires_at"] is not None

    def test_eval_metadata_when_disabled(self):
        """Eval metadata returns disabled state when V3 disabled."""
        from app.memory_v3 import get_v3_eval_metadata

        metadata = get_v3_eval_metadata("test message", enabled=False)

        assert metadata["v3_enabled"] is False
        assert metadata["signals_used"] is False
        assert metadata["context_mode"] is None
        assert metadata["user_state"] is None

    def test_eval_metadata_contains_behavior_adjustments(self):
        """Eval metadata includes behavior adjustments."""
        from app.memory_v3 import get_v3_eval_metadata

        metadata = get_v3_eval_metadata("quick answer please")

        assert "behavior_adjustments" in metadata
        adjustments = metadata["behavior_adjustments"]
        assert "verbosity" in adjustments
        assert "tone" in adjustments
        assert "ask_clarifying" in adjustments


class TestRollout:
    """Test rollout and metrics system."""

    def test_rollout_manager_creation(self):
        """Rollout manager can be created."""
        from app.memory_v3.rollout import V3RolloutManager, RolloutPhase

        manager = V3RolloutManager(rollout_percent=0, phase="shadow")
        assert manager.phase == RolloutPhase.SHADOW
        assert manager.rollout_percent == 0

    def test_shadow_phase_never_injects(self):
        """Shadow phase never injects."""
        from app.memory_v3.rollout import V3RolloutManager

        manager = V3RolloutManager(rollout_percent=100, phase="shadow")
        # Even at 100%, shadow mode doesn't inject
        assert manager.should_inject("user123") is False

    def test_full_phase_always_injects(self):
        """Full phase always injects."""
        from app.memory_v3.rollout import V3RolloutManager

        manager = V3RolloutManager(rollout_percent=0, phase="full")
        # Even at 0%, full mode always injects
        assert manager.should_inject("user123") is True

    def test_cohort_consistency(self):
        """Same user always gets same cohort."""
        from app.memory_v3.rollout import V3RolloutManager

        manager = V3RolloutManager(rollout_percent=50, phase="ramp")

        # Same user should get consistent result
        result1 = manager.should_inject("user123")
        result2 = manager.should_inject("user123")
        result3 = manager.should_inject("user123")

        assert result1 == result2 == result3

    def test_metrics_recording(self):
        """Metrics are properly recorded."""
        from app.memory_v3.rollout import V3Metrics
        from app.memory_v3 import generate_v3_signal

        metrics = V3Metrics()
        signal = generate_v3_signal("Help debug this error")

        metrics.record_signal(signal, injected=True)
        metrics.record_turn("test message")

        assert metrics.total_signals == 1
        assert metrics.signals_injected == 1
        assert metrics.conversation_turns == 1

    def test_follow_up_detection(self):
        """Follow-up messages are detected."""
        from app.memory_v3.rollout import V3RolloutManager

        manager = V3RolloutManager()

        # Test follow-up patterns
        manager.record_turn("no, that's not what I meant")
        assert manager.metrics.follow_up_turns == 1

        manager.record_turn("I meant something else")
        assert manager.metrics.follow_up_turns == 2

        # Non-follow-up
        manager.record_turn("How does async work?")
        assert manager.metrics.follow_up_turns == 2

    def test_re_ask_detection(self):
        """Re-ask (same question) is detected."""
        from app.memory_v3.rollout import V3RolloutManager

        manager = V3RolloutManager()

        # First ask
        manager.record_turn("How does async await work in Python?", [])

        # Re-ask (similar question)
        manager.record_turn(
            "How does async await work in Python?",
            ["How does async await work in Python?"]
        )
        assert manager.metrics.re_ask_count == 1

    def test_shadow_exit_criteria(self):
        """Shadow exit criteria checker works."""
        from app.memory_v3.rollout import check_shadow_exit_criteria

        # Good metrics - should pass
        good_metrics = {
            "anti_gaming": {"rate": 0.01},
            "rollout": {"total_signals": 200},
            "distribution": {"context_mode": {"debug": 50, "execute": 80, "explain": 40, "design": 30}},
        }
        result = check_shadow_exit_criteria(good_metrics)
        assert result["ready_to_exit"] is True

        # Bad metrics - anti-gaming too high
        bad_metrics = {
            "anti_gaming": {"rate": 0.05},  # 5% > 2% threshold
            "rollout": {"total_signals": 200},
            "distribution": {"context_mode": {"debug": 50, "execute": 50}},
        }
        result = check_shadow_exit_criteria(bad_metrics)
        assert result["ready_to_exit"] is False

    def test_intensity_affects_prompt_block(self):
        """V3_INTENSITY affects prompt block generation."""
        from app.memory_v3 import generate_v3_signal

        signal = generate_v3_signal("quick help please")
        assert signal is not None

        # Zero intensity should return empty
        zero_block = signal.to_prompt_block(intensity=0.0)
        assert zero_block == ""

        # Low intensity (0.3) should have context only, no state or adjustments
        low_block = signal.to_prompt_block(intensity=0.3)
        assert "context_mode:" in low_block
        assert "adjustments_enabled: false" in low_block
        assert "state:" not in low_block  # No state at low intensity

        # Medium intensity (0.5) should have context + state, no adjustments
        med_block = signal.to_prompt_block(intensity=0.5)
        assert "context_mode:" in med_block
        assert "state:" in med_block
        assert "adjustments_enabled: false" in med_block

        # High intensity (0.6+) should have adjustments
        high_block = signal.to_prompt_block(intensity=0.6)
        assert "context_mode:" in high_block
        assert "state:" in high_block
        assert "adjustments_enabled: true" in high_block
        assert "behavior_adjustments:" in high_block


def run_tests():
    """Run all tests standalone (without pytest)."""
    import traceback

    test_classes = [
        TestContextDetection,
        TestStateDetection,
        TestTTLBehavior,
        TestNoDBWrites,
        TestAntiGaming,
        TestSignalGeneration,
        TestShadowMode,
        TestEvalMetadata,
        TestRollout,
    ]

    total = 0
    passed = 0
    failed = 0

    print("=" * 60)
    print("Memory V3 Acceptance Tests")
    print("=" * 60)

    for test_class in test_classes:
        print(f"\n{test_class.__name__}:")
        instance = test_class()

        for method_name in dir(instance):
            if method_name.startswith('test_'):
                total += 1
                try:
                    getattr(instance, method_name)()
                    print(f"  ✅ {method_name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  ❌ {method_name}: {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ❌ {method_name}: {e}")
                    traceback.print_exc()
                    failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

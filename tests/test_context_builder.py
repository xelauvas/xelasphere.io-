# tests/test_context_builder.py
"""Tests for ContextBuilder — smart prompt assembly."""

import pytest


class TestTokenEstimation:
    def test_english_text_estimate(self):
        from app.context.context_builder import estimate_tokens
        text = "Hello world, this is a test of token estimation."
        tokens = estimate_tokens(text)
        assert 10 <= tokens <= 20

    def test_code_text_estimate(self):
        from app.context.context_builder import estimate_tokens
        code = "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n-1)"
        tokens = estimate_tokens(code, is_code=True)
        assert 20 <= tokens <= 35

    def test_empty_string(self):
        from app.context.context_builder import estimate_tokens
        assert estimate_tokens("") == 0


class TestMicroCompact:
    def test_replaces_long_code_blocks(self):
        from app.context.context_builder import microcompact_message
        code_lines = ["    line {}".format(i) for i in range(60)]
        code_block = "```python\n" + "\n".join(code_lines) + "\n```"
        message = {"response": f"Here is the code:\n{code_block}"}
        result = microcompact_message(message, artifact_version=3)
        assert "[Code block: python, 60 lines" in result["response"]
        assert "line 0" not in result["response"]

    def test_preserves_short_code_blocks(self):
        from app.context.context_builder import microcompact_message
        code_block = "```python\nprint('hello')\n```"
        message = {"response": f"Example:\n{code_block}"}
        result = microcompact_message(message, artifact_version=None)
        assert "print('hello')" in result["response"]

    def test_does_not_modify_user_prompt(self):
        from app.context.context_builder import microcompact_message
        message = {"prompt": "Please write some code", "response": "Sure!"}
        result = microcompact_message(message, artifact_version=None)
        assert result["prompt"] == "Please write some code"


class TestMetadataExtraction:
    def test_extracts_chat_action(self):
        from app.context.context_builder import extract_turn_metadata
        meta = extract_turn_metadata(
            turn_number=1,
            user_message="How do I sort a list?",
            action_type="chat",
            had_error=False,
            artifact_mutation=None,
            model="deepseek-chat",
        )
        assert meta["turn_number"] == 1
        assert meta["action_type"] == "chat"
        assert meta["user_intent_summary"] == "How do I sort a list?"
        assert meta["had_error"] is False

    def test_strips_code_blocks_from_intent(self):
        from app.context.context_builder import extract_turn_metadata
        msg = "```python\nprint('hello')\n```\nFix the bug above"
        meta = extract_turn_metadata(
            turn_number=5,
            user_message=msg,
            action_type="patch_artifact",
            had_error=False,
            artifact_mutation={"artifact_id": "art_1", "to_version": "ver_2", "files_changed": ["main.py"]},
            model="deepseek-chat",
        )
        assert meta["user_intent_summary"] == "Fix the bug above"
        assert "print" not in meta["user_intent_summary"]

    def test_truncates_long_intent(self):
        from app.context.context_builder import extract_turn_metadata
        long_msg = "x" * 200
        meta = extract_turn_metadata(
            turn_number=1,
            user_message=long_msg,
            action_type="chat",
            had_error=False,
            artifact_mutation=None,
            model="deepseek-chat",
        )
        assert len(meta["user_intent_summary"]) <= 80

    def test_records_error(self):
        from app.context.context_builder import extract_turn_metadata
        meta = extract_turn_metadata(
            turn_number=8,
            user_message="Fix the bug",
            action_type="patch_artifact",
            had_error=True,
            error_detail="old_string mismatch on line 42",
            artifact_mutation=None,
            model="deepseek-chat",
        )
        assert meta["had_error"] is True
        assert "old_string mismatch" in meta["error_detail"]


class TestBuildTier2Summary:
    def test_builds_summary_from_metadata(self):
        from app.context.context_builder import build_tier2_summary
        metadata_list = [
            {
                "turn_number": 1,
                "user_intent_summary": "Build a Snake game",
                "action_type": "chat",
                "had_error": False,
                "artifact_mutation": None,
                "model": "deepseek-chat",
            },
            {
                "turn_number": 3,
                "user_intent_summary": "Here is the game",
                "action_type": "create_artifact",
                "had_error": False,
                "artifact_mutation": {
                    "artifact_id": "art_1",
                    "from_version": None,
                    "to_version": "ver_1",
                    "files_changed": ["main.py", "style.css"],
                },
                "model": "deepseek-chat",
            },
            {
                "turn_number": 5,
                "user_intent_summary": "Fix the food spawning",
                "action_type": "patch_artifact",
                "had_error": True,
                "error_detail": "old_string mismatch",
                "artifact_mutation": None,
                "model": "deepseek-chat",
            },
        ]
        summary = build_tier2_summary(metadata_list)
        assert "Turn 1" in summary
        assert "Build a Snake game" in summary
        assert "Created artifact" in summary or "create_artifact" in summary
        assert "[ERROR]" in summary or "error" in summary.lower()


class TestContextBuilderBuild:
    def _make_message(self, turn, prompt, response, action_type="chat",
                      had_error=False, artifact_mutation=None, error_detail=None):
        msg = {
            "prompt": prompt,
            "response": response,
            "timestamp": f"2026-04-06T12:{turn:02d}:00",
        }
        if artifact_mutation:
            msg["artifact_id"] = artifact_mutation.get("artifact_id")
        msg["turn_metadata"] = {
            "turn_number": turn,
            "user_intent_summary": prompt[:80],
            "action_type": action_type,
            "had_error": had_error,
            "error_detail": error_detail,
            "artifact_mutation": artifact_mutation,
            "model": "test-model",
        }
        return msg

    def test_build_with_short_history(self):
        from app.context.context_builder import ContextBuilder
        messages = [self._make_message(i, f"prompt {i}", f"response {i}") for i in range(3)]
        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="You are helpful.",
            full_history=messages,
            artifact_state=None,
            user_message="What next?",
            memory_context=None,
        )
        assert result.compression_level == "none"
        assert "What next?" in result.prompt
        assert len(result.artifact_files) == 0

    def test_build_includes_tier1_verbatim(self):
        from app.context.context_builder import ContextBuilder
        messages = [self._make_message(i, f"prompt {i}", f"response {i}") for i in range(10)]
        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="You are helpful.",
            full_history=messages,
            artifact_state=None,
            user_message="New question",
            memory_context=None,
        )
        assert "prompt 9" in result.prompt
        assert "prompt 8" in result.prompt
        assert "prompt 7" in result.prompt
        assert "prompt 6" in result.prompt
        assert "prompt 5" in result.prompt

    def test_build_includes_tier2_summary(self):
        from app.context.context_builder import ContextBuilder
        messages = [self._make_message(i, f"prompt {i}", f"response {i}") for i in range(12)]
        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="You are helpful.",
            full_history=messages,
            artifact_state=None,
            user_message="Next?",
            memory_context=None,
        )
        assert "Conversation Summary" in result.prompt

    def test_build_with_artifact_injection(self):
        from app.context.context_builder import ContextBuilder
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        tracker.sync_from_files([
            {"path": "main.py", "language": "python", "content": "x = 1"},
        ])
        messages = [self._make_message(1, "build something", "ok")]
        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="System.",
            full_history=messages,
            artifact_state=tracker,
            user_message="Fix the code",
            memory_context=None,
        )
        assert len(result.artifact_files) == 1
        assert result.artifact_files[0]["path"] == "main.py"
        assert "main.py" in result.prompt

    def test_build_includes_memory_context(self):
        from app.context.context_builder import ContextBuilder
        messages = [self._make_message(1, "hi", "hello")]
        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="System.",
            full_history=messages,
            artifact_state=None,
            user_message="Tell me about X",
            memory_context="User prefers dark mode. User works at Acme Corp.",
        )
        assert "dark mode" in result.prompt

    def test_microcompact_applied_to_older_tier1_messages(self):
        from app.context.context_builder import ContextBuilder
        messages = []
        for i in range(8):
            if i == 4:
                code_lines = "\n".join([f"    line_{j} = {j}" for j in range(60)])
                resp = f"Here is the code:\n```python\n{code_lines}\n```"
            else:
                resp = f"response {i}"
            messages.append(self._make_message(i, f"prompt {i}", resp))

        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="System.",
            full_history=messages,
            artifact_state=None,
            user_message="Next",
            memory_context=None,
        )
        assert "line_0" not in result.prompt
        assert "[Code block:" in result.prompt

    def test_proper_role_interleaving(self):
        from app.context.context_builder import ContextBuilder
        messages = [self._make_message(1, "hello", "world")]
        builder = ContextBuilder(token_budget=8000)
        result = builder.build(
            system_prompt="System.",
            full_history=messages,
            artifact_state=None,
            user_message="Next",
            memory_context=None,
        )
        assert "User: hello" in result.prompt
        assert "Assistant: world" in result.prompt

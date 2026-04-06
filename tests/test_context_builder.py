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

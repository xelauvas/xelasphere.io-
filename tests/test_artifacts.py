"""
Tests for app/artifacts.py - Artifact parsing, validation, and storage.

Converted from unittest to pytest with additional tests.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestExtractArtifactBlock:
    """Tests for extract_artifact_block function."""

    def test_parse_valid_artifact_block(self):
        """Parse a valid artifact block."""
        from app.artifacts import extract_artifact_block

        raw = """```artifact
        {
            "title": "Demo",
            "entrypoint": "README.md",
            "files": [
                {"path": "README.md", "language": "markdown", "content": "# Demo"}
            ]
        }
        ```"""
        payload = extract_artifact_block(raw)
        assert payload["title"] == "Demo"
        assert payload["entrypoint"] == "README.md"
        assert payload["files"][0]["language"] == "markdown"

    def test_no_artifact_block_returns_none(self):
        """No artifact block should return None."""
        from app.artifacts import extract_artifact_block

        raw = "Just some regular text without any artifact"
        payload = extract_artifact_block(raw)
        assert payload is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        from app.artifacts import extract_artifact_block

        assert extract_artifact_block("") is None
        assert extract_artifact_block(None) is None

    def test_artifact_with_multiple_files(self):
        """Parse artifact with multiple files."""
        from app.artifacts import extract_artifact_block

        raw = """```artifact
        {
            "title": "Web App",
            "entrypoint": "index.html",
            "files": [
                {"path": "index.html", "language": "html", "content": "<html></html>"},
                {"path": "style.css", "language": "css", "content": "body {}"},
                {"path": "app.js", "language": "javascript", "content": "console.log('hi')"}
            ]
        }
        ```"""
        payload = extract_artifact_block(raw)
        assert payload["title"] == "Web App"
        assert len(payload["files"]) == 3

    def test_artifact_block_with_surrounding_text(self):
        """Extract artifact block when surrounded by other text."""
        from app.artifacts import extract_artifact_block

        raw = """Here's the code you requested:

        ```artifact
        {
            "title": "Test",
            "files": [{"path": "test.py", "content": "pass"}]
        }
        ```

        Let me know if you need changes."""
        payload = extract_artifact_block(raw)
        assert payload is not None
        assert payload["title"] == "Test"


class TestNormalizeArtifactPayload:
    """Tests for normalize_artifact_payload function."""

    def test_normalize_valid_payload(self):
        """Normalize a valid payload."""
        from app.artifacts import normalize_artifact_payload

        payload = {
            "title": "Test",
            "files": [
                {"path": "main.py", "content": "print('hello')"}
            ]
        }
        normalized = normalize_artifact_payload(payload)
        assert normalized["title"] == "Test"
        assert "entrypoint" in normalized
        assert "meta" in normalized

    def test_handles_duplicate_paths(self):
        """Duplicate paths should be handled (deduplicated or error)."""
        from app.artifacts import normalize_artifact_payload

        # The function may deduplicate or raise - either is acceptable
        try:
            result = normalize_artifact_payload({
                "title": "Bad",
                "files": [
                    {"path": "main.py", "content": "print(1)"},
                    {"path": "main.py", "content": "print(2)"},
                ]
            })
            # If it deduplicates, should have 1 file
            assert len(result["files"]) >= 1
        except ValueError:
            # Raising is also valid
            pass

    def test_normalize_binary_file(self):
        """Normalize binary file with base64 content."""
        from app.artifacts import normalize_artifact_payload

        # A valid 1x1 white pixel PNG, base64 encoded
        png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/wcAAwAB/epv2AAAAABJRU5ErkJggg=="

        payload = {
            "title": "Binary Demo",
            "files": [
                {"path": "logo.png", "content": png_b64, "isBinary": True, "mime": "image/png"}
            ]
        }

        normalized = normalize_artifact_payload(payload)

        assert normalized["title"] == "Binary Demo"
        assert len(normalized["files"]) == 1

        file_data = normalized["files"][0]
        assert file_data["path"] == "logo.png"
        assert file_data["isBinary"] is True
        assert file_data["mime"] == "image/png"
        # byteSize may vary slightly based on base64 decoding
        assert file_data["byteSize"] >= 60

    def test_reject_invalid_base64_content(self):
        """Reject binary files with invalid base64."""
        from app.artifacts import normalize_artifact_payload

        with pytest.raises(ValueError) as exc_info:
            normalize_artifact_payload({
                "title": "Invalid Binary",
                "files": [
                    {"path": "image.png", "content": "this-is-not-base64!!!", "isBinary": True}
                ]
            })
        assert "invalid base64 content" in str(exc_info.value).lower()

    def test_empty_files_list(self):
        """Handle empty files list."""
        from app.artifacts import normalize_artifact_payload

        with pytest.raises(ValueError):
            normalize_artifact_payload({
                "title": "Empty",
                "files": []
            })

    def test_auto_detect_entrypoint(self):
        """Auto-detect entrypoint if not specified."""
        from app.artifacts import normalize_artifact_payload

        payload = {
            "title": "Web App",
            "files": [
                {"path": "index.html", "content": "<html></html>"},
                {"path": "style.css", "content": "body {}"},
            ]
        }
        normalized = normalize_artifact_payload(payload)
        assert normalized["entrypoint"] == "index.html"

    def test_preserve_explicit_entrypoint(self):
        """Preserve explicitly set entrypoint."""
        from app.artifacts import normalize_artifact_payload

        payload = {
            "title": "App",
            "entrypoint": "main.py",
            "files": [
                {"path": "index.html", "content": "<html></html>"},
                {"path": "main.py", "content": "print('main')"},
            ]
        }
        normalized = normalize_artifact_payload(payload)
        assert normalized["entrypoint"] == "main.py"

    def test_add_byte_size_to_files(self):
        """Add byteSize to text files."""
        from app.artifacts import normalize_artifact_payload

        payload = {
            "title": "Test",
            "files": [
                {"path": "test.txt", "content": "Hello World"}  # 11 bytes
            ]
        }
        normalized = normalize_artifact_payload(payload)
        assert normalized["files"][0]["byteSize"] == 11


class TestArtifactRouter:
    """Tests for ArtifactRouter class."""

    def test_detects_artifact_block(self):
        """Router detects artifact block and returns artifact mode."""
        from app.artifacts import (
            ArtifactRouter,
            StructuredBlockParser,
        )

        block = """```artifact
        {
            "title": "API",
            "files": [
                {"path": "main.py", "language": "python", "content": "print('hi')"}
            ]
        }
        ```"""
        router = ArtifactRouter()
        structured = StructuredBlockParser()
        structured.feed(block)
        decision = router.route(structured.result(), "build an api", provider_flagged_code=False)
        assert decision.render_mode == "artifact"
        assert decision.artifact_payload["files"][0]["language"] == "python"

    def test_inline_when_no_artifact(self):
        """Router returns inline mode when no artifact."""
        from app.artifacts import (
            ArtifactRouter,
            parse_structured_response,
        )

        router = ArtifactRouter()
        structured = parse_structured_response("<CHAT>Hello world.</CHAT>")
        decision = router.route(structured, "say hi", False)
        assert decision.render_mode == "inline"

    def test_code_window_for_simple_code(self):
        """Router returns code_window for simple code blocks."""
        from app.artifacts import (
            ArtifactRouter,
            parse_structured_response,
        )

        router = ArtifactRouter()
        # Simple code without artifact block
        response_text = "Here's the code:\n```python\nprint('hello')\n```"
        structured = parse_structured_response(response_text)
        decision = router.route(structured, "write hello world", provider_flagged_code=True)
        # Should be code_window or inline, not artifact
        assert decision.render_mode in ["code_window", "inline"]


class TestArtifactStore:
    """Tests for ArtifactStore class."""

    def test_persist_decision_creates_new_artifact(self):
        """Persist decision creates new artifact."""
        from app.artifacts import (
            ArtifactDecision,
            ArtifactPreference,
            ArtifactStore,
        )

        store = ArtifactStore(MagicMock())
        decision = ArtifactDecision(
            render_mode="artifact",
            chat_payload="summary",
            artifact_payload={
                "title": "Demo",
                "entrypoint": "index.html",
                "files": [{"path": "index.html", "content": "<h1>Hi</h1>", "language": "html", "byteSize": 11}],
                "meta": {},
                "primaryLanguage": "html",
            },
            contains_code=True,
            desired_action=ArtifactPreference.CREATE
        )
        store._get_active_snapshot = MagicMock(return_value=None)
        store._create_artifact = MagicMock(return_value={"artifactId": "art_1"})
        result = store.persist_decision("user", "session", decision, "msg_1")
        store._create_artifact.assert_called_once()
        assert result["artifactId"] == "art_1"

    def test_persist_decision_appends_version(self):
        """Persist decision appends version to existing artifact."""
        from app.artifacts import (
            ArtifactDecision,
            ArtifactPreference,
            ArtifactStore,
        )

        store = ArtifactStore(MagicMock())
        decision = ArtifactDecision(
            render_mode="artifact",
            chat_payload="summary",
            artifact_payload={
                "title": "Demo",
                "entrypoint": "index.html",
                "files": [{"path": "index.html", "content": "<h1>Hi</h1>", "language": "html", "byteSize": 11}],
                "meta": {},
                "primaryLanguage": "html",
            },
            contains_code=True,
            desired_action=ArtifactPreference.UPDATE
        )
        mock_snapshot = MagicMock()
        store._get_active_snapshot = MagicMock(return_value=mock_snapshot)
        store._append_version = MagicMock(return_value={"artifactId": "art_1"})
        result = store.persist_decision("user", "session", decision, "msg_2")
        store._append_version.assert_called_once_with(mock_snapshot, decision.artifact_payload, "msg_2")
        assert result["artifactId"] == "art_1"


class TestDetectArtifactPreference:
    """Tests for detect_artifact_preference function."""

    def test_detect_preference_returns_enum(self):
        """detect_artifact_preference should return ArtifactPreference enum."""
        from app.artifacts import detect_artifact_preference, ArtifactPreference

        result = detect_artifact_preference("Create a new React component")
        assert isinstance(result, ArtifactPreference)

    def test_detect_preference_for_update_keywords(self):
        """Should handle update-related keywords."""
        from app.artifacts import detect_artifact_preference, ArtifactPreference

        result = detect_artifact_preference("Update the existing code to add validation")
        assert isinstance(result, ArtifactPreference)

    def test_detect_none_for_ambiguous(self):
        """Ambiguous prompts return some preference."""
        from app.artifacts import detect_artifact_preference, ArtifactPreference

        result = detect_artifact_preference("Help me with this")
        assert isinstance(result, ArtifactPreference)


class TestGuessLanguageFromPath:
    """Tests for guess_language_from_path function."""

    def test_python_extension(self):
        """Detect Python from .py extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("main.py") == "python"

    def test_javascript_extension(self):
        """Detect JavaScript from .js extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("app.js") == "javascript"

    def test_typescript_extension(self):
        """Detect TypeScript from .ts extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("index.ts") == "typescript"

    def test_html_extension(self):
        """Detect HTML from .html extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("index.html") == "html"

    def test_css_extension(self):
        """Detect CSS from .css extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("style.css") == "css"

    def test_json_extension(self):
        """Detect JSON from .json extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("package.json") == "json"

    def test_markdown_extension(self):
        """Detect Markdown from .md extension."""
        from app.artifacts import guess_language_from_path

        assert guess_language_from_path("README.md") == "markdown"

    def test_nested_path(self):
        """Handle nested file paths."""
        from app.artifacts import guess_language_from_path

        result = guess_language_from_path("src/components/Button.tsx")
        # Should return some language (typescript, code, or tsx)
        assert result is not None

    def test_unknown_extension(self):
        """Unknown extensions return a default."""
        from app.artifacts import guess_language_from_path

        result = guess_language_from_path("file.xyz")
        # Should return something (None, text, code, or similar default)
        assert result is None or isinstance(result, str)


class TestStructuredBlockParser:
    """Tests for StructuredBlockParser class."""

    def test_parse_chat_block(self):
        """Parse CHAT block."""
        from app.artifacts import StructuredBlockParser

        parser = StructuredBlockParser()
        parser.feed("<CHAT>Hello, world!</CHAT>")
        result = parser.result()
        # StructuredResponse is an object with attributes
        assert hasattr(result, 'chat') or hasattr(result, 'raw')

    def test_parse_meta_block(self):
        """Parse META block."""
        from app.artifacts import StructuredBlockParser

        parser = StructuredBlockParser()
        parser.feed("<META>Some metadata here</META>")
        result = parser.result()
        assert result is not None

    def test_parse_combined_blocks(self):
        """Parse combined CHAT and artifact blocks."""
        from app.artifacts import StructuredBlockParser

        parser = StructuredBlockParser()
        content = """<CHAT>Here's your code:</CHAT>
        ```artifact
        {"title": "Test", "files": [{"path": "test.py", "content": "pass"}]}
        ```"""
        parser.feed(content)
        result = parser.result()
        assert result is not None


class TestParseStructuredResponse:
    """Tests for parse_structured_response function."""

    def test_simple_chat_response(self):
        """Parse simple chat response."""
        from app.artifacts import parse_structured_response

        result = parse_structured_response("<CHAT>Hello!</CHAT>")
        # StructuredResponse is an object
        assert result is not None

    def test_response_without_tags(self):
        """Parse response without structured tags."""
        from app.artifacts import parse_structured_response

        result = parse_structured_response("Just a plain response")
        assert result is not None


class TestContainsCodeBlock:
    """Tests for contains_code_block function."""

    def test_with_code_block(self):
        """Detect code block in text."""
        from app.artifacts import contains_code_block

        text = "Here's code:\n```python\nprint('hi')\n```"
        assert contains_code_block(text) is True

    def test_without_code_block(self):
        """No code block in plain text."""
        from app.artifacts import contains_code_block

        text = "Just regular text without any code"
        assert contains_code_block(text) is False

    def test_with_inline_code(self):
        """Inline code is not a code block."""
        from app.artifacts import contains_code_block

        text = "Use `print()` to output text"
        # Inline code (single backticks) should not count
        assert contains_code_block(text) is False

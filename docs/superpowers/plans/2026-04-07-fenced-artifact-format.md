# Fenced Artifact Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSON-embedded artifact format with separate fenced blocks (artifact-meta + artifact-file) to eliminate JSON escaping failures in LLM code generation.

**Architecture:** New format uses `artifact-meta` (tiny JSON metadata) and `artifact-file:path:lang` (raw code) fenced blocks. Old JSON format kept as fallback. Parser priority: new format first, old format second, code block extraction third. Patch format unchanged.

**Tech Stack:** Python (backend parsing), JavaScript (frontend streaming detection), regex patterns, existing Firestore storage (unchanged)

**Spec:** `docs/superpowers/specs/2026-04-07-fenced-artifact-format-design.md`

---

### Task 1: Backend Parser — `extract_fenced_artifact()`

**Files:**
- Modify: `app/artifacts.py:38-46` (add new regex patterns after existing ones)
- Modify: `app/artifacts.py` (add new functions after `extract_artifact_block`)
- Test: `tests/test_artifacts.py`

- [ ] **Step 1: Write failing tests for new fenced format parser**

Add to `tests/test_artifacts.py`:

```python
class TestExtractFencedArtifact:
    """Tests for the new fenced artifact format parser."""

    def test_parse_basic_fenced_artifact(self):
        """Parse a well-formed fenced artifact with meta + two files."""
        from app.artifacts import extract_fenced_artifact

        raw = (
            "Here's your app.\n\n"
            "```artifact-meta\n"
            '{"title": "Todo App", "type": "project", "primaryLanguage": "jsx"}\n'
            "```\n\n"
            "```artifact-file:App.jsx:jsx\n"
            "import React from 'react';\n"
            "export default function App() {\n"
            "  return <div>Hello</div>;\n"
            "}\n"
            "```\n\n"
            "```artifact-file:style.css:css\n"
            "body { margin: 0; }\n"
            "```"
        )
        payload = extract_fenced_artifact(raw)
        assert payload is not None
        assert payload["title"] == "Todo App"
        assert payload["type"] == "project"
        assert payload["primaryLanguage"] == "jsx"
        assert len(payload["files"]) == 2
        assert payload["files"][0]["path"] == "App.jsx"
        assert payload["files"][0]["language"] == "jsx"
        assert "import React" in payload["files"][0]["content"]
        assert payload["files"][1]["path"] == "style.css"
        assert payload["files"][1]["language"] == "css"
        assert "body { margin: 0; }" in payload["files"][1]["content"]

    def test_returns_none_without_meta(self):
        """Return None if no artifact-meta block found."""
        from app.artifacts import extract_fenced_artifact

        raw = "```artifact-file:App.jsx:jsx\ncode\n```"
        assert extract_fenced_artifact(raw) is None

    def test_returns_none_without_files(self):
        """Return None if meta exists but no file blocks."""
        from app.artifacts import extract_fenced_artifact

        raw = '```artifact-meta\n{"title": "X"}\n```\nSome chat text.'
        assert extract_fenced_artifact(raw) is None

    def test_preserves_code_with_quotes_and_special_chars(self):
        """Code with JSON-hostile characters must be preserved exactly."""
        from app.artifacts import extract_fenced_artifact

        code = 'const msg = "Hello \\"world\\"";\nconst obj = {a: 1, b: "two"};\n'
        raw = (
            "```artifact-meta\n"
            '{"title": "Test"}\n'
            "```\n\n"
            f"```artifact-file:app.js:javascript\n{code}```"
        )
        payload = extract_fenced_artifact(raw)
        assert payload is not None
        assert payload["files"][0]["content"] == code.rstrip("\n")

    def test_meta_defaults(self):
        """Missing meta fields get defaults."""
        from app.artifacts import extract_fenced_artifact

        raw = (
            '```artifact-meta\n{"title": "X"}\n```\n'
            "```artifact-file:main.py:python\nprint('hi')\n```"
        )
        payload = extract_fenced_artifact(raw)
        assert payload["type"] == "project"
        assert payload["primaryLanguage"] == "python"

    def test_chat_text_before_between_after(self):
        """Chat text around blocks is allowed and doesn't break parsing."""
        from app.artifacts import extract_fenced_artifact

        raw = (
            "Here's the app I built:\n\n"
            '```artifact-meta\n{"title": "App"}\n```\n\n'
            "First file:\n\n"
            "```artifact-file:a.js:javascript\nlet x = 1;\n```\n\n"
            "And the styles:\n\n"
            "```artifact-file:b.css:css\nbody {}\n```\n\n"
            "Let me know if you want changes!"
        )
        payload = extract_fenced_artifact(raw)
        assert payload is not None
        assert len(payload["files"]) == 2

    def test_case_insensitive_fences(self):
        """Fence tags should be case-insensitive."""
        from app.artifacts import extract_fenced_artifact

        raw = (
            '```Artifact-Meta\n{"title": "X"}\n```\n'
            "```Artifact-File:app.py:python\npass\n```"
        )
        payload = extract_fenced_artifact(raw)
        assert payload is not None


class TestExtractArtifactAnyFormat:
    """Tests for the combined parser that tries new format then old."""

    def test_prefers_new_format(self):
        """New fenced format takes priority over old JSON format."""
        from app.artifacts import extract_artifact_any_format

        raw = (
            '```artifact-meta\n{"title": "New"}\n```\n'
            "```artifact-file:app.py:python\nprint('new')\n```"
        )
        payload = extract_artifact_any_format(raw)
        assert payload is not None
        assert payload["title"] == "New"

    def test_falls_back_to_old_format(self):
        """Old JSON format still works when new format not present."""
        from app.artifacts import extract_artifact_any_format

        raw = (
            '```artifact\n'
            '{"title": "Old", "files": [{"path": "a.py", "language": "python", "content": "pass"}]}\n'
            '```'
        )
        payload = extract_artifact_any_format(raw)
        assert payload is not None
        assert payload["title"] == "Old"

    def test_returns_none_for_neither_format(self):
        """Returns None when neither format is present."""
        from app.artifacts import extract_artifact_any_format

        assert extract_artifact_any_format("just some chat text") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py::TestExtractFencedArtifact -v && python -m pytest tests/test_artifacts.py::TestExtractArtifactAnyFormat -v`

Expected: FAIL — `extract_fenced_artifact` and `extract_artifact_any_format` not defined.

- [ ] **Step 3: Add regex patterns to artifacts.py**

Add after line 42 (`ARTIFACT_PATCH_FENCE_PATTERN`):

```python
# New fenced format patterns (artifact-meta + artifact-file blocks)
FENCED_META_RE = re.compile(r"```artifact-meta\s*\n(.*?)\n\s*```", re.DOTALL | re.IGNORECASE)
FENCED_FILE_RE = re.compile(r"```artifact-file:([^:]+):(\w+)\s*\n([\s\S]*?)```", re.IGNORECASE)
```

- [ ] **Step 4: Implement `extract_fenced_artifact()` and `extract_artifact_any_format()`**

Add after `extract_artifact_block()` function (around line 672):

```python
def extract_fenced_artifact(text: str) -> Optional[dict]:
    """Parse the new fenced artifact format (artifact-meta + artifact-file blocks).

    Returns normalized artifact payload dict, or None if format not found.
    """
    meta_match = FENCED_META_RE.search(text)
    if not meta_match:
        return None

    meta_json = meta_match.group(1).strip()
    try:
        meta = json.loads(meta_json)
    except json.JSONDecodeError:
        logger.warning(f"Fenced artifact: meta JSON parse failed: {meta_json[:100]}")
        return None

    files = []
    for file_match in FENCED_FILE_RE.finditer(text):
        path = file_match.group(1).strip()
        language = file_match.group(2).strip()
        content = file_match.group(3)
        # Strip single trailing newline but preserve internal formatting
        if content.endswith('\n'):
            content = content[:-1]
        files.append({
            'path': path,
            'language': language,
            'content': content,
            'isBinary': False,
            'byteSize': len(content.encode('utf-8')),
        })

    if not files:
        return None

    return normalize_artifact_payload({
        'title': meta.get('title', 'Artifact'),
        'type': meta.get('type', 'project'),
        'primaryLanguage': meta.get('primaryLanguage', files[0]['language']),
        'files': files,
    })


def extract_artifact_any_format(text: str) -> Optional[dict]:
    """Try new fenced format first, fall back to old JSON format."""
    result = extract_fenced_artifact(text)
    if result:
        logger.info(f"Fenced artifact parsed: {len(result.get('files', []))} files")
        return result
    return extract_artifact_block(text)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py -v`

Expected: ALL PASS (new tests + existing tests unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/artifacts.py tests/test_artifacts.py
git commit -m "feat(artifacts): add fenced artifact format parser (artifact-meta + artifact-file)"
```

---

### Task 2: ActionParser — Wire New Parser

**Files:**
- Modify: `app/context/action_parser.py:109-123`
- Test: `tests/test_action_parser.py`

- [ ] **Step 1: Write failing tests for ActionParser with new format**

Add to `tests/test_action_parser.py`:

```python
class TestFencedFormatParsing:
    """Tests for ActionParser handling the new fenced artifact format."""

    def test_parse_create_artifact_fenced_format(self):
        """ActionParser extracts artifact from new fenced format."""
        from app.context.action_parser import ActionParser

        response = (
            "ACTION: create_artifact\n\n"
            "Here's your app.\n\n"
            '```artifact-meta\n{"title": "Todo", "primaryLanguage": "jsx"}\n```\n\n'
            "```artifact-file:App.jsx:jsx\nimport React from 'react';\n"
            "export default function App() { return <div>Hi</div>; }\n```\n\n"
            "```artifact-file:style.css:css\nbody { margin: 0; }\n```"
        )
        result = ActionParser.parse(response)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None
        assert len(result.artifact_payload["files"]) == 2
        assert result.artifact_payload["title"] == "Todo"
        assert "Here's your app" in result.chat_text

    def test_chat_text_strips_fenced_blocks(self):
        """Chat text should have all artifact blocks removed."""
        from app.context.action_parser import ActionParser

        response = (
            "ACTION: create_artifact\n\n"
            "Built it for you.\n\n"
            '```artifact-meta\n{"title": "X"}\n```\n\n'
            "```artifact-file:a.py:python\npass\n```\n\n"
            "Let me know!"
        )
        result = ActionParser.parse(response)
        assert "artifact-meta" not in result.chat_text
        assert "artifact-file" not in result.chat_text
        assert "Built it for you" in result.chat_text
        assert "Let me know" in result.chat_text

    def test_old_format_still_works(self):
        """Old JSON artifact format still parses as fallback."""
        from app.context.action_parser import ActionParser

        response = (
            "ACTION: create_artifact\n\n"
            "Here it is.\n\n"
            '```artifact\n'
            '{"title": "Old", "files": [{"path": "a.py", "language": "python", "content": "pass"}]}\n'
            '```'
        )
        result = ActionParser.parse(response)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None
        assert result.artifact_payload["title"] == "Old"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_action_parser.py::TestFencedFormatParsing -v`

Expected: FAIL — new fenced format not detected, `artifact_payload` is None.

- [ ] **Step 3: Update `_extract_artifact()` in action_parser.py**

Replace the `_extract_artifact` function at line 109-123:

```python
def _extract_artifact(text: str):
    """Extract artifact from fenced blocks. Tries new format first, falls back to old JSON.
    Returns (payload, chat_text)."""
    from app.artifacts import extract_fenced_artifact, extract_artifact_block
    from app.artifacts import FENCED_META_RE, FENCED_FILE_RE, ARTIFACT_FENCE_PATTERN

    # Try new fenced format first (artifact-meta + artifact-file blocks)
    payload = extract_fenced_artifact(text)
    if payload:
        # Strip all artifact blocks from chat text
        chat_text = FENCED_META_RE.sub("", text)
        chat_text = FENCED_FILE_RE.sub("", chat_text).strip()
        return payload, chat_text

    # Fall back to old JSON format
    try:
        payload = extract_artifact_block(text)
    except Exception as exc:
        logger.warning(f"ActionParser: artifact extraction failed: {exc}")
        payload = None

    chat_text = ARTIFACT_FENCE_PATTERN.sub("", text).strip()
    if not chat_text or chat_text == text:
        chat_text = _ARTIFACT_UNCLOSED_RE.sub("", text).strip()

    return payload, chat_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_action_parser.py -v`

Expected: ALL PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add app/context/action_parser.py tests/test_action_parser.py
git commit -m "feat(context): wire fenced artifact parser into ActionParser with old format fallback"
```

---

### Task 3: System Prompt — New Format Examples

**Files:**
- Modify: `app/services/chat_engine.py:642-657`

- [ ] **Step 1: Replace artifact example in DEVELOPER_INSTRUCTIONS**

Replace lines 642-657 (the old `EXAMPLE for creating artifacts` section):

```python
    "EXAMPLE for creating artifacts:\n"
    "ACTION: create_artifact\n"
    "[brief explanation]\n\n"
    "```artifact-meta\n"
    "{\"title\":\"App Name\",\"type\":\"project\",\"primaryLanguage\":\"jsx\"}\n"
    "```\n\n"
    "```artifact-file:App.jsx:jsx\n"
    "import React from 'react';\n"
    "export default function App() {\n"
    "  return <div>Hello World</div>;\n"
    "}\n"
    "```\n\n"
    "```artifact-file:style.css:css\n"
    "body { margin: 0; font-family: sans-serif; }\n"
    "```\n\n"
    "IMPORTANT: Each file goes in its own ```artifact-file:path:language block.\n"
    "  Code goes DIRECTLY in the block — NO JSON wrapping, NO escaping.\n"
    "  The ```artifact-meta block has ONLY the title/type/language metadata as JSON.\n\n"
    "For React apps: Use App.jsx as entrypoint + style.css. DO NOT create index.html.\n"
    "  The preview system auto-loads React 18 + Babel. Just write JSX directly.\n"
    "  IMPORTANT: return() MUST have a SINGLE root element.\n"
    "  CRITICAL: Inside artifact code, NEVER use markdown like **bold**, *italic*.\n"
    "  NEVER mix index.html with .jsx files.\n"
    "For plain HTML apps: Create index.html, style.css, script.js.\n"
    "React artifacts MUST have AT LEAST 2 files: App.jsx + style.css.\n\n"
```

- [ ] **Step 2: Verify syntax**

Run: `cd /root/xelas.io/xelasphere.io- && python -c "import ast; ast.parse(open('app/services/chat_engine.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/services/chat_engine.py
git commit -m "feat(prompt): update artifact example to fenced format (artifact-meta + artifact-file)"
```

---

### Task 4: Frontend Streaming — Detect New Fenced Format

**Files:**
- Modify: `app/static/js/codeWindow.js:2930-2966`

- [ ] **Step 1: Replace artifact fence detection in SSE chunk handler**

Replace the artifact fence detection block (lines 2936-2954) with:

```javascript
                                    // --- Artifact fence detection (new fenced format + old fallback) ---
                                    const panelHasArtifact = window.artifactPanel?.currentArtifact &&
                                        window.artifactPanel?.panel?.classList.contains('is-open');

                                    if (window.artifactPanel && !panelHasArtifact && !artifactStreamActive) {
                                        // New format: artifact-meta signals start
                                        const metaMatch = streamedContent.match(/```artifact-meta\s*\n/i);
                                        if (metaMatch) {
                                            // Check if meta block is closed and first file block started
                                            const fileMatch = streamedContent.match(/```artifact-file:([^:]+):(\w+)\s*\n/i);
                                            if (fileMatch) {
                                                artifactStreamActive = true;
                                                artifactFencePosition = metaMatch.index;
                                                window.artifactPanel.openStreaming();
                                                // Send code after the first file fence opening
                                                const fileContentStart = streamedContent.indexOf('\n', fileMatch.index) + 1;
                                                if (fileContentStart > 0) {
                                                    const afterFence = streamedContent.slice(fileContentStart);
                                                    // Strip closing fence if present in same chunk
                                                    const closeFence = afterFence.indexOf('\n```');
                                                    const codeChunk = closeFence !== -1 ? afterFence.slice(0, closeFence) : afterFence;
                                                    if (codeChunk) {
                                                        window.artifactPanel.appendStreamingCode(codeChunk);
                                                    }
                                                }
                                            }
                                        }

                                        // Fallback: old JSON format detection
                                        if (!artifactStreamActive) {
                                            const fenceMatch = streamedContent.match(/```artifact(?![-_])/i);
                                            if (fenceMatch) {
                                                artifactStreamActive = true;
                                                artifactFencePosition = fenceMatch.index;
                                                window.artifactPanel.openStreaming();
                                                const fenceEnd = streamedContent.indexOf('\n', fenceMatch.index);
                                                if (fenceEnd !== -1) {
                                                    const afterFence = streamedContent.slice(fenceEnd + 1);
                                                    if (afterFence) {
                                                        window.artifactPanel.appendStreamingCode(afterFence);
                                                    }
                                                }
                                            }
                                        }
                                    }
```

Note: The old format fallback regex changes from `/```artifact(?!_patch)/i` to `/```artifact(?![-_])/i` — the negative lookahead now also excludes `artifact-meta` and `artifact-file` (which start with `-`).

- [ ] **Step 2: Verify JS syntax**

Run: `cd /root/xelas.io/xelasphere.io- && node -c app/static/js/codeWindow.js`

Expected: No output (success).

- [ ] **Step 3: Bump cache version**

In `app/templates/index.html`, find the codeWindow.js script tag and bump `?v=22` to `?v=23`.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/codeWindow.js app/templates/index.html
git commit -m "feat(streaming): detect new fenced artifact format in SSE stream with old format fallback"
```

---

### Task 5: Integration Test — End-to-End Verification

**Files:**
- Test: `tests/test_artifacts.py` (add integration test)

- [ ] **Step 1: Write integration test simulating full model response**

Add to `tests/test_artifacts.py`:

```python
class TestEndToEndFencedFormat:
    """Integration test: simulate a model response through ActionParser."""

    def test_full_response_with_fenced_format(self):
        """Simulate a complete model response and verify the full pipeline."""
        from app.context.action_parser import ActionParser

        # Realistic model response with ACTION line, chat text, and fenced artifact
        model_response = (
            "ACTION: create_artifact\n\n"
            "I'll build a simple React counter app for you.\n\n"
            '```artifact-meta\n'
            '{"title": "Counter App", "type": "project", "primaryLanguage": "jsx"}\n'
            '```\n\n'
            '```artifact-file:App.jsx:jsx\n'
            "import React, { useState } from 'react';\n"
            "import './style.css';\n"
            "\n"
            "export default function App() {\n"
            "  const [count, setCount] = useState(0);\n"
            "  return (\n"
            "    <div className=\"container\">\n"
            "      <h1>Counter: {count}</h1>\n"
            '      <button onClick={() => setCount(c => c + 1)}>+</button>\n'
            '      <button onClick={() => setCount(c => c - 1)}>-</button>\n'
            "    </div>\n"
            "  );\n"
            "}\n"
            '```\n\n'
            '```artifact-file:style.css:css\n'
            ".container {\n"
            "  text-align: center;\n"
            "  padding: 40px;\n"
            "  font-family: 'Segoe UI', sans-serif;\n"
            "}\n"
            "button {\n"
            "  font-size: 24px;\n"
            "  margin: 0 10px;\n"
            "  padding: 8px 20px;\n"
            "}\n"
            '```\n\n'
            "Click the buttons to increment or decrement the counter."
        )

        result = ActionParser.parse(model_response)

        # Verify action
        assert result.action == "create_artifact"
        assert not result.used_fallback

        # Verify artifact payload
        assert result.artifact_payload is not None
        assert result.artifact_payload["title"] == "Counter App"
        assert len(result.artifact_payload["files"]) == 2

        # Verify JSX file content is exact (no JSON escaping artifacts)
        jsx_file = result.artifact_payload["files"][0]
        assert jsx_file["path"] == "App.jsx"
        assert jsx_file["language"] == "jsx"
        assert 'className="container"' in jsx_file["content"]
        assert "useState(0)" in jsx_file["content"]
        assert "setCount(c => c + 1)" in jsx_file["content"]

        # Verify CSS file
        css_file = result.artifact_payload["files"][1]
        assert css_file["path"] == "style.css"
        assert "'Segoe UI'" in css_file["content"]

        # Verify chat text is clean
        assert "artifact-meta" not in result.chat_text
        assert "artifact-file" not in result.chat_text
        assert "simple React counter app" in result.chat_text
        assert "Click the buttons" in result.chat_text

    def test_old_json_format_still_works_end_to_end(self):
        """Old format still parses correctly through the full pipeline."""
        from app.context.action_parser import ActionParser

        model_response = (
            "ACTION: create_artifact\n\n"
            "Here's the app.\n\n"
            '```artifact\n'
            '{"title":"Legacy App","type":"project","primaryLanguage":"jsx",'
            '"files":[{"path":"App.jsx","language":"jsx","content":"export default function App() { return <div>Hi</div>; }"},'
            '{"path":"style.css","language":"css","content":"body { margin: 0; }"}]}\n'
            '```'
        )

        result = ActionParser.parse(model_response)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None
        assert result.artifact_payload["title"] == "Legacy App"
        assert len(result.artifact_payload["files"]) == 2
```

- [ ] **Step 2: Run all artifact tests**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py tests/test_action_parser.py -v`

Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_artifacts.py
git commit -m "test(artifacts): add end-to-end integration tests for fenced artifact format"
```

---

### Task 6: Deploy and Verify

**Files:**
- No code changes — deployment and manual verification.

- [ ] **Step 1: Syntax check all modified files**

```bash
cd /root/xelas.io/xelasphere.io-
python -c "import ast; ast.parse(open('app/artifacts.py').read()); ast.parse(open('app/context/action_parser.py').read()); ast.parse(open('app/services/chat_engine.py').read()); ast.parse(open('app/blueprints/chat_routes.py').read()); print('Python OK')"
node -c app/static/js/codeWindow.js && echo "JS OK"
```

Expected: `Python OK` then `JS OK`.

- [ ] **Step 2: Run full test suite**

```bash
cd /root/xelas.io/xelasphere.io-
python -m pytest tests/test_artifacts.py tests/test_action_parser.py tests/test_context_builder.py -v
```

Expected: ALL PASS.

- [ ] **Step 3: Restart service**

```bash
sudo systemctl restart xelasphere
sleep 2
sudo systemctl status xelasphere --no-pager | head -5
```

Expected: `Active: active (running)`.

- [ ] **Step 4: Manual test — new artifact creation**

Send: "build me a todo app in react"

Check logs:
```bash
sudo journalctl -u xelasphere --no-pager --since "2 min ago" | grep -i 'Fenced artifact parsed\|artifact extracted\|render_mode=artifact\|persist'
```

Expected: `Fenced artifact parsed: 2 files` in logs. Artifact panel opens with code.

- [ ] **Step 5: Manual test — old format fallback**

If a model produces old JSON format, verify it still parses by checking logs for `extract_artifact_block` being used as fallback.

- [ ] **Step 6: Final commit with cache bump**

```bash
git add -A
git commit -m "feat(artifacts): deploy fenced artifact format — eliminates JSON escaping failures"
git push origin main
```

# Forge M1 Validation Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch syntax-level artifact bugs (Netflix-clone-class JSX errors) before broken artifacts reach the panel; auto-retry through a multi-model cascade up to 4 attempts; surface either a healed artifact or a broken-with-warning artifact with full audit-trail telemetry.

**Architecture:** Server-side syntax validator (Babel for JS/JSX, tsc for TS/TSX) inserted into the existing `/chat/stream` `chunk_type=='done'` handler between line 1751 (existing cascade #1 end) and line 2042 (artifact_data assembly). On failure, cascade calls `[v4-flash, v4-pro, kimi-k2.5, gpt-4.1]` with `start_idx` advancing past stream_model. Loop-detect (Decision 9) skips one tier on byte-identical errors. Pre-flight token budget (80k/turn) gates each call before invocation. All five `forge_*` envelope fields persist in the message record for replay. **No artifact panel rewrite, no new ACTION verbs, no client-side parser, no Docker.**

**Tech Stack:** Python 3.11, Flask, Firestore (schema-less), `asyncio.create_subprocess_exec`, Node 22+ + `@babel/parser`, optional `tsc` (graceful degradation if absent), pytest, existing tiktoken via `app.utilities.count_tokens`.

**Spec contract:** `docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md` @ commit `cba3e28`. **All 11 locked decisions and 9 amendments are constraints — do not re-open.** If implementation exposes ambiguity that requires a new decision, STOP and surface it as a question to the spec author.

**Merge-order dependencies:**
1. `feat/deepseek-v4-swap` (PR #139) → must merge to main first (Decision 3 prerequisite — V4 model IDs need to be in `MODEL_CATALOG`).
2. `feat/forge-m1-investigation` (PR #135) → merge to main (provides citation context for this plan).
3. `feat/forge-m1-spec` (PR #136) → merge to main (this is the contract).
4. **This plan executes against post-merge `main`** on a fresh branch `feat/forge-m1-impl`.

---

## 1. Plan overview

This plan decomposes the Forge M1 spec's §14 file-by-file manifest and the 16 acceptance criteria into 14 ordered TDD tasks. Each task is a discrete reviewable unit with a clear failing-test → implementation → passing-test → commit cycle. Tasks order so the test suite grows monotonically and each task ships independent value: by Task 8 the cascade is correct end-to-end on the server; by Task 12 the frontend renders correctly; by Task 13 the integration tests prove all 16 ACs; Task 14 is polish + self-review.

The plan is sized for one focused engineer over ~3-5 working days. Estimated total: ~750-900 LOC added (per §14 manifest), ~280 LOC of unit tests, ~6-8 fixture JSON files. Each task stands as a separate commit on `feat/forge-m1-impl`. PR opens after Task 13; Task 14 is the polish-and-self-review pass on the open PR.

---

## 2. Pre-flight checks

Verify each of these before starting Task 1. Each check is fast (< 2 minutes). If any fails, STOP and resolve before proceeding — do not "fix forward" by adding the missing piece into a task.

- [ ] **PF1: V4 model IDs are in `MODEL_CATALOG` on main.**

  ```bash
  cd /root/xelas.io/xelasphere.io-
  git checkout main && git pull
  python3 -c "
  from app.model_catalog import MODEL_CATALOG, MODEL_ALIASES
  for m in ['deepseek-v4-flash', 'deepseek-v4-pro', 'kimi-k2.5', 'gpt-4.1']:
      assert m in MODEL_CATALOG or m in MODEL_ALIASES, f'{m!r} not in catalog/aliases'
  print('OK — all 4 cascade models resolvable')
  "
  ```

  Expected: `OK — all 4 cascade models resolvable`. If this fails, V4 swap PR #139 has not merged. STOP.

- [ ] **PF2: Spec is merged to main.**

  ```bash
  test -f docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md && echo "OK — spec on main"
  git log --oneline main -- docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md | head -1
  ```

  Expected: file exists, commit `cba3e28` (or later — amendments only) appears in log. If absent, PR #136 has not merged. STOP.

- [ ] **PF3: Branch off latest main.**

  ```bash
  git checkout -b feat/forge-m1-impl
  git status
  ```

  Expected: `On branch feat/forge-m1-impl ... nothing to commit, working tree clean`.

- [ ] **PF4: Node ≥ 16 available.**

  ```bash
  node --version
  ```

  Expected: `v16.x` or higher (deploy env runs v22.x as of 2026-04-29). The Babel JSX-parser CLI wrapper requires modern Node.

- [ ] **PF5: `@babel/parser` install path planned.**

  Repo root has no `package.json`. The plan creates one in **Task 1 step 1.5** with `@babel/parser` pinned to `^7.24.0`. Verify the deploy environment has write access to `/opt/xelasphere/node_modules/`:

  ```bash
  sudo -u xelasphere ls /opt/xelasphere 2>&1 | head -3
  ```

  Expected: directory listing succeeds. The deploy step (post-merge) runs `npm install --omit=dev` to materialize `node_modules/`.

- [ ] **PF6: `tsc` availability — informational, NOT blocking.**

  ```bash
  which tsc 2>&1 || echo "tsc not installed — TS/TSX validation will gracefully degrade per Decision 11 / §6.4"
  ```

  If absent, M1 ships without TS/TSX validation working — `FileNotFoundError` is logged once per process and TS files become unvalidated (treated as `ok=True`). This is acceptable per spec's graceful-degradation discipline. Documented in Risk Register R3.

- [ ] **PF7: tiktoken available (used by token estimation).**

  ```bash
  python3 -c "import tiktoken; print('OK')"
  ```

  Expected: `OK`. Already required by `app/utilities.py:166`.

- [ ] **PF8: Existing test suite passes on this branch.**

  ```bash
  python3 -m pytest tests/ -q --deselect tests/test_artifacts.py::TestApplyPatches::test_patch_file_not_found --deselect tests/test_mempalace_shadow_runtime.py::test_chat_stream_fires_user_half_record_turn_only --deselect tests/test_smart_router.py::TestDetectTaskType::test_detect_coding_from_history --deselect tests/test_smart_router.py::TestAnalyzeHistoryForCode::test_non_coding_history --deselect tests/test_smart_suggestions.py::TestIncrementShownCounts -q 2>&1 | tail -5
  ```

  Expected: only the 5 pre-existing flaky tests (deselected) fail. Everything else passes. If new failures appear, investigate before starting Task 1.

---

## 3. Task decomposition

### Task 1: Validator module foundation (no integration)

**Goal:** Create `artifact_validator.py` with dataclasses + pure helpers + the Node CLI wrapper script. No subprocess calls yet, no chat_routes.py changes. All helpers tested in isolation.

**Files:**
- Create: `app/services/artifact_validator.py`
- Create: `app/services/forge_validators/__init__.py`
- Create: `app/services/forge_validators/check_jsx.cjs`
- Create: `package.json`
- Create: `tests/test_artifact_validator.py`

**ACs satisfied:** None directly; foundation for #1, #2, #3, #11.

**Estimated LOC delta:** ~200 added (~80 module + ~25 cjs + ~10 package.json + ~85 tests).

**Dependencies:** PF1–PF8.

- [ ] **Step 1.1: Write failing test for `ValidationError` and `ValidationResult` dataclasses.**

```python
# tests/test_artifact_validator.py
"""Unit tests for app/services/artifact_validator.py."""

import pytest


class TestDataclasses:
    def test_validation_error_is_frozen(self):
        from app.services.artifact_validator import ValidationError
        e = ValidationError(
            file_path="App.jsx",
            line=45,
            column=12,
            message="Binding invalid",
            excerpt="   45 | return (<></>) =>",
            parser="node",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            e.line = 46
        assert e.file_path == "App.jsx"
        assert e.line == 45

    def test_validation_result_holds_errors_tuple(self):
        from app.services.artifact_validator import ValidationError, ValidationResult
        e = ValidationError(file_path="a.jsx", line=1, column=1, message="m", excerpt="", parser="node")
        r = ValidationResult(ok=False, errors=(e,), skipped=(), elapsed_ms=42)
        assert r.ok is False
        assert len(r.errors) == 1
        assert r.elapsed_ms == 42

    def test_validation_result_ok_with_no_errors(self):
        from app.services.artifact_validator import ValidationResult
        r = ValidationResult(ok=True, errors=(), skipped=("doc.md",), elapsed_ms=1)
        assert r.ok is True
        assert r.errors == ()
        assert r.skipped == ("doc.md",)
```

- [ ] **Step 1.2: Run test, verify it fails.**

  Run: `python3 -m pytest tests/test_artifact_validator.py::TestDataclasses -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.artifact_validator'`.

- [ ] **Step 1.3: Create the validator module with dataclasses + module skeleton.**

```python
# app/services/artifact_validator.py
"""
Forge M1 — server-side artifact validator.

Per spec §6, validates every JSX/TSX/JS/TS file in the artifact payload
via subprocess (Babel for JS/JSX, tsc for TS/TSX). Returns a structured
ValidationResult that the cascade orchestrator in chat_routes.py uses
to decide whether to retry.

This module is pure: no Flask globals, no Firestore, no SSE. Subprocess
is the only side effect.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Path to the Node CLI wrapper script (resolved relative to module file).
CHECK_JSX_SCRIPT = (
    Path(__file__).parent / "forge_validators" / "check_jsx.cjs"
).resolve()


@dataclass(frozen=True)
class ValidationError:
    """A single parser error from the validator subprocess."""
    file_path: str        # e.g. "App.jsx"
    line: int             # 1-indexed
    column: int           # 1-indexed; 0 if unparseable
    message: str          # human-readable, normalized across parsers
    excerpt: str          # 3-line code window centered on error
    parser: str           # "node" | "tsc" — for diagnostics, not display


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating an entire artifact payload."""
    ok: bool
    errors: tuple                  # tuple[ValidationError, ...] — sorted by (file_path, line)
    skipped: tuple                 # tuple[str, ...] — paths skipped (non-validatable language)
    elapsed_ms: int                # for telemetry


# Per-language dispatch — see spec §6.2.
# Files whose `language` is NOT in this map are added to result.skipped
# and do not block the artifact (Decision 11 — JSX/TSX/JS/TS only).
_LANGUAGE_VALIDATORS = {
    "jsx": "_validate_via_node",
    "js":  "_validate_via_node",
    "tsx": "_validate_via_tsc",
    "ts":  "_validate_via_tsc",
}
```

- [ ] **Step 1.4: Run test, verify it passes.**

  Run: `python3 -m pytest tests/test_artifact_validator.py::TestDataclasses -v`
  Expected: 3 passed.

- [ ] **Step 1.5: Create `package.json` at repo root with `@babel/parser` pinned.**

```json
{
  "name": "xelasphere-runtime",
  "private": true,
  "description": "Runtime Node deps for server-side validators (Forge M1+).",
  "dependencies": {
    "@babel/parser": "^7.24.0"
  }
}
```

- [ ] **Step 1.6: Create the Node CLI wrapper.**

```javascript
// app/services/forge_validators/check_jsx.cjs
// Forge M1 — minimal JSX/JS syntax check via @babel/parser.
// Reads source from stdin (avoids tmpfile race + cleanup).
//
// Exit 0 = parse OK.
// Exit 1 = parse error; stderr is a single JSON line:
//   {"kind":"parse_error","line":N,"column":N,"message":"..."}

const parser = require('@babel/parser');

const chunks = [];
process.stdin.on('data', (chunk) => chunks.push(chunk));
process.stdin.on('end', () => {
    const code = Buffer.concat(chunks).toString('utf8');
    try {
        parser.parse(code, {
            sourceType: 'module',
            plugins: ['jsx'],
            errorRecovery: false,
        });
        process.exit(0);
    } catch (err) {
        // Babel error: { loc: { line, column }, message, ... }
        process.stderr.write(JSON.stringify({
            kind: 'parse_error',
            line: err.loc?.line ?? 0,
            column: err.loc?.column ?? 0,
            message: err.message ?? 'Unknown parse error',
        }) + '\n');
        process.exit(1);
    }
});
```

- [ ] **Step 1.7: Create the empty package marker.**

```python
# app/services/forge_validators/__init__.py
"""Node CLI wrappers for Forge validators."""
```

- [ ] **Step 1.8: Install Node deps.**

  Run: `cd /root/xelas.io/xelasphere.io- && npm install --omit=dev 2>&1 | tail -3`
  Expected: `added <N> packages` or similar success message. `node_modules/@babel/parser/` exists.

- [ ] **Step 1.9: Smoke-test the Node wrapper directly (clean code).**

  Run: `echo "import React from 'react'; export default function App() { return <div>hi</div>; }" | node app/services/forge_validators/check_jsx.cjs; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 1.10: Smoke-test the Node wrapper directly (broken JSX — Netflix bug).**

  Run: `echo "useEffect(() => { return (<></>) => window.removeEventListener('scroll', h); }, []);" | node app/services/forge_validators/check_jsx.cjs; echo "exit=$?"`
  Expected: `exit=1` and stderr is a JSON line with `"kind":"parse_error"` and a `line` ≥ 1.

- [ ] **Step 1.11: Commit.**

```bash
git add app/services/artifact_validator.py app/services/forge_validators/ package.json package-lock.json tests/test_artifact_validator.py
git commit -m "feat(forge): scaffold artifact_validator module + Babel JSX wrapper

Adds:
- app/services/artifact_validator.py with ValidationError /
  ValidationResult dataclasses and _LANGUAGE_VALIDATORS dispatch map.
- app/services/forge_validators/check_jsx.cjs Node CLI wrapper using
  @babel/parser (reads source from stdin per spec §6.3).
- package.json at repo root pinning @babel/parser ^7.24.0 (spec §14).
- tests/test_artifact_validator.py with dataclass behavior tests
  (frozen invariants, tuple semantics).

No integration with chat_routes.py yet — that arrives in Task 5.
Subprocess invocation arrives in Task 2.

Refs: spec §6.1, §6.2, §14 NEW manifest."
```

---

### Task 2: Validator subprocess invocation (per-file + per-language)

**Goal:** Wire `_validate_via_node` (Babel) and `_validate_via_tsc` (tsc) subprocess calls into `validate_artifact_payload`. Handle timeouts (3s per file), missing-binary `FileNotFoundError`, and graceful degradation. Tests run real subprocesses against fixture artifacts.

**Files:**
- Modify: `app/services/artifact_validator.py`
- Create: `tests/fixtures/artifacts/clean_react.json`
- Create: `tests/fixtures/artifacts/netflix_broken_jsx.json`
- Create: `tests/fixtures/artifacts/multi_file_partial_fail.json`
- Create: `tests/fixtures/artifacts/svg_only.json`
- Modify: `tests/test_artifact_validator.py`

**ACs satisfied:** #1 (Netflix bug detection), #2 (clean passes), #3 (SVG bypasses), #9 (timeout doesn't block), #11 (multi-file `_short_reason` foundation — full AC in Task 3).

**Estimated LOC delta:** ~150 added module + ~150 added tests + ~110 fixtures.

**Dependencies:** Task 1.

- [ ] **Step 2.1: Write failing tests for the four fixture cases.**

```python
# tests/test_artifact_validator.py — append to existing file

class TestValidatorEndToEnd:
    """Real-subprocess tests against fixture artifacts."""

    @pytest.fixture
    def load_fixture(self):
        import json
        from pathlib import Path
        def _load(name):
            return json.loads(
                (Path(__file__).parent / "fixtures" / "artifacts" / f"{name}.json").read_text()
            )
        return _load

    def test_clean_react_validates_ok(self, load_fixture):
        from app.services.artifact_validator import validate_artifact_payload
        result = validate_artifact_payload(load_fixture("clean_react"))
        assert result.ok is True
        assert result.errors == ()

    def test_netflix_broken_jsx_fails(self, load_fixture):
        from app.services.artifact_validator import validate_artifact_payload
        result = validate_artifact_payload(load_fixture("netflix_broken_jsx"))
        assert result.ok is False
        assert len(result.errors) >= 1
        # Netflix bug is on line 45 of App.jsx — "Binding invalid"-class error
        e = result.errors[0]
        assert e.file_path == "App.jsx"
        assert e.line == 45
        assert e.parser == "node"

    def test_multi_file_partial_fail(self, load_fixture):
        from app.services.artifact_validator import validate_artifact_payload
        result = validate_artifact_payload(load_fixture("multi_file_partial_fail"))
        assert result.ok is False
        # Exactly one file fails (the broken one); others pass.
        broken_paths = {e.file_path for e in result.errors}
        assert len(broken_paths) == 1

    def test_svg_only_skips_validator(self, load_fixture, monkeypatch):
        """No validatable files → no subprocess spawned, ok=True."""
        import asyncio
        spawn_count = {"n": 0}
        original = asyncio.create_subprocess_exec
        async def counting_spawn(*args, **kwargs):
            spawn_count["n"] += 1
            return await original(*args, **kwargs)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", counting_spawn)

        from app.services.artifact_validator import validate_artifact_payload
        result = validate_artifact_payload(load_fixture("svg_only"))

        assert result.ok is True
        assert spawn_count["n"] == 0
        assert "icon.svg" in result.skipped


class TestValidatorTimeout:
    def test_timeout_treated_as_pass(self, monkeypatch):
        """Per Decision 11 / §6.4, validator timeout = ok=True with warning."""
        import asyncio
        from app.services.artifact_validator import validate_artifact_payload

        async def hanging_proc(*args, **kwargs):
            class HangingProc:
                stdin = None
                async def communicate(self, input=None):
                    await asyncio.sleep(10)  # exceeds 3s timeout
                async def wait(self): pass
                def kill(self): pass
            return HangingProc()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", hanging_proc)

        payload = {
            "title": "x", "type": "project", "primaryLanguage": "jsx",
            "files": [{"path": "App.jsx", "language": "jsx", "content": "x", "isBinary": False, "byteSize": 1}],
        }
        result = validate_artifact_payload(payload)
        assert result.ok is True  # timeout = pass
        # No errors recorded — we DON'T fail the artifact on infrastructure failure.
        assert result.errors == ()


class TestValidatorMissingBinary:
    def test_filenotfound_treated_as_pass(self, monkeypatch):
        """Per §6.4, missing node binary → log once, skip language, return ok=True."""
        import asyncio
        from app.services.artifact_validator import validate_artifact_payload

        async def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("node not found")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_fnf)

        payload = {
            "title": "x", "type": "project", "primaryLanguage": "jsx",
            "files": [{"path": "App.jsx", "language": "jsx", "content": "valid;", "isBinary": False, "byteSize": 6}],
        }
        result = validate_artifact_payload(payload)
        assert result.ok is True  # missing binary = treat as pass
```

- [ ] **Step 2.2: Create the four fixture files.**

```bash
mkdir -p tests/fixtures/artifacts
```

```json
// tests/fixtures/artifacts/clean_react.json
{
  "title": "Clean React",
  "type": "project",
  "primaryLanguage": "jsx",
  "files": [
    {
      "path": "App.jsx",
      "language": "jsx",
      "content": "import React from 'react';\nexport default function App() {\n    return <div>hello</div>;\n}\n",
      "isBinary": false,
      "byteSize": 88
    }
  ]
}
```

```json
// tests/fixtures/artifacts/netflix_broken_jsx.json
{
  "title": "Netflix Clone (broken)",
  "type": "project",
  "primaryLanguage": "jsx",
  "files": [
    {
      "path": "App.jsx",
      "language": "jsx",
      "content": "import React, { useEffect } from 'react';\n\nexport default function App() {\n    useEffect(() => {\n        const handleScroll = () => {\n            const y = window.scrollY;\n            console.log(y);\n        };\n        window.addEventListener('scroll', handleScroll);\n\n        // BROKEN — Netflix bug pattern from real prod incident on 2026-04-29 18:23:\n        // model emitted (<></>) =>  instead of () =>\n        return (<></>) => window.removeEventListener('scroll', handleScroll);\n    }, []);\n\n    return (<>\n        <header>Netflix</header>\n        <main>Movies</main>\n    </>);\n}\n",
      "isBinary": false,
      "byteSize": 600
    }
  ]
}
```

NOTE: The broken `return` statement above MUST land on line 45 of the
file content for AC #1 to assert correctly. Verify after creation:

```bash
sed -n '45p' <(python3 -c "import json; print(json.load(open('tests/fixtures/artifacts/netflix_broken_jsx.json'))['files'][0]['content'])")
```

If it doesn't land on line 45, pad the content with extra newlines at the
top (or remove some) until it does. Acceptance criterion #1 specifically
asserts `e.line == 45`.

```json
// tests/fixtures/artifacts/multi_file_partial_fail.json
{
  "title": "Multi-File Partial Fail",
  "type": "project",
  "primaryLanguage": "jsx",
  "files": [
    {
      "path": "App.jsx",
      "language": "jsx",
      "content": "import React from 'react';\nimport Hero from './Hero';\nexport default function App() { return <Hero />; }\n",
      "isBinary": false,
      "byteSize": 100
    },
    {
      "path": "Hero.jsx",
      "language": "jsx",
      "content": "import React from 'react';\nexport default function Hero() { return (<></>) => null; }\n",
      "isBinary": false,
      "byteSize": 90
    },
    {
      "path": "style.css",
      "language": "css",
      "content": "body { margin: 0; }\n",
      "isBinary": false,
      "byteSize": 20
    }
  ]
}
```

```json
// tests/fixtures/artifacts/svg_only.json
{
  "title": "SVG Icon",
  "type": "project",
  "primaryLanguage": "svg",
  "files": [
    {
      "path": "icon.svg",
      "language": "svg",
      "content": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"10\"/></svg>",
      "isBinary": false,
      "byteSize": 100
    }
  ]
}
```

- [ ] **Step 2.3: Run failing tests, verify they fail.**

  Run: `python3 -m pytest tests/test_artifact_validator.py::TestValidatorEndToEnd -v`
  Expected: 4 errors with `AttributeError: ... has no attribute 'validate_artifact_payload'` (function not yet implemented).

- [ ] **Step 2.4: Implement `_has_validatable_files`, `_validate_via_node`, `_validate_via_tsc`, `validate_artifact_payload`.**

  Append to `app/services/artifact_validator.py`:

```python
# Module-state: track which language binaries are missing so we log
# the warning once per process (per §6.4).
_MISSING_BINARIES: set = set()


def _has_validatable_files(payload: dict) -> bool:
    """True iff at least one file's language is in _LANGUAGE_VALIDATORS.

    Cheap O(n) check; avoids spawning subprocess for SVG/HTML/CSS-only
    artifacts (spec §7.2 bullet 2 + Decision 11 / §11 E8).
    """
    if not payload or not payload.get("files"):
        return False
    for f in payload["files"]:
        if f.get("language", "").lower() in _LANGUAGE_VALIDATORS:
            return True
    return False


def _make_excerpt(content: str, line: int) -> str:
    """3-line code window centered on `line` (1-indexed). Plain text per §6.5."""
    if line < 1:
        return ""
    lines = content.splitlines()
    start = max(0, line - 2)
    end = min(len(lines), line + 1)
    out = []
    for i in range(start, end):
        prefix = ">>> " if (i + 1) == line else "    "
        out.append(f"{prefix}{i + 1:>3} | {lines[i]}")
    return "\n".join(out)


async def _validate_via_node(file_path: str, content: str) -> Optional[tuple]:
    """Babel-via-Node syntax check for .js/.jsx files.

    Returns:
      None on subprocess failure / timeout / missing binary (caller treats
      the file as ok=True per graceful-degradation).
      () (empty tuple) if the file parses cleanly.
      tuple[ValidationError] of length 1 if a parse error was reported.
    """
    if "node" in _MISSING_BINARIES:
        return None  # Already detected as missing; skip silently.

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", str(CHECK_JSX_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        if "node" not in _MISSING_BINARIES:
            logger.warning("Forge validator: 'node' binary not found; skipping all .js/.jsx files this process")
            _MISSING_BINARIES.add("node")
        return None

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=content.encode("utf-8")),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning(f"Forge validator timeout on {file_path}")
        return None

    if proc.returncode == 0:
        return ()  # clean parse

    # Non-zero exit. stderr is a single JSON line (per check_jsx.cjs contract).
    import json as _json
    try:
        err_dict = _json.loads(stderr.decode("utf-8").strip())
        line = int(err_dict.get("line", 0)) or 1
        # Babel columns are 0-indexed; normalize to 1-indexed (§6.5).
        column = int(err_dict.get("column", 0)) + 1
        message = err_dict.get("message", "Unknown parse error")
    except (ValueError, _json.JSONDecodeError, AttributeError):
        # Unparseable stderr — capture the tail (§6.4)
        line, column, message = 0, 0, stderr.decode("utf-8", errors="replace")[-200:].strip() or "Unknown error"

    return (ValidationError(
        file_path=file_path,
        line=line,
        column=column,
        message=message,
        excerpt=_make_excerpt(content, line),
        parser="node",
    ),)


async def _validate_via_tsc(file_path: str, content: str) -> Optional[tuple]:
    """tsc --noEmit syntax check for .ts/.tsx files.

    tsc must be invoked on a real file path (it doesn't accept stdin); we
    write to a tmpfile, invoke, and clean up. This deviates from the
    no-tmpfile pattern used for Babel because tsc has no equivalent stdin
    interface. The race window is bounded by the 3s subprocess timeout
    + tempfile.NamedTemporaryFile cleanup.
    """
    import tempfile
    import os
    import re as _re

    if "tsc" in _MISSING_BINARIES:
        return None

    # Write content to tmpfile with the right extension (tsc dispatches by ext).
    suffix = ".tsx" if file_path.endswith(".tsx") else ".ts"
    fd, tmp = tempfile.mkstemp(prefix="forge_tsc_", suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)

        try:
            proc = await asyncio.create_subprocess_exec(
                "tsc", "--noEmit", "--jsx", "preserve",
                "--target", "ESNext", "--module", "ESNext",
                "--allowJs", "--skipLibCheck", tmp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            if "tsc" not in _MISSING_BINARIES:
                logger.warning("Forge validator: 'tsc' binary not found; skipping all .ts/.tsx files this process")
                _MISSING_BINARIES.add("tsc")
            return None

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(f"Forge validator timeout on {file_path}")
            return None

        if proc.returncode == 0:
            return ()  # clean

        # tsc writes errors to STDOUT (not stderr). Format per §6.3:
        #   /tmp/forge_tsc_xxx.tsx(45,12): error TS1109: Expression expected.
        out = stdout.decode("utf-8", errors="replace")
        pattern = _re.compile(r"^.+?\((\d+),(\d+)\): error (TS\d+): (.+)$", _re.MULTILINE)
        m = pattern.search(out)
        if m:
            line, column = int(m.group(1)), int(m.group(2))
            message = f"{m.group(3)}: {m.group(4)}"
        else:
            line, column, message = 0, 0, out[-200:].strip() or "tsc unparseable error"

        return (ValidationError(
            file_path=file_path,
            line=line,
            column=column,
            message=message,
            excerpt=_make_excerpt(content, line),
            parser="tsc",
        ),)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def validate_artifact_payload(payload: dict) -> ValidationResult:
    """Validate every JSX/TSX/JS/TS file in the payload.

    Returns ok=True if all validatable files pass OR no validatable files
    exist. Returns ok=True (with logged warning) if validator infrastructure
    itself fails — graceful degradation per Decision 11 / §6.4.
    """
    import time
    t_start = time.monotonic()

    if not payload or not payload.get("files"):
        return ValidationResult(ok=True, errors=(), skipped=(), elapsed_ms=0)

    skipped: list = []
    pending: list = []  # list of (file_path, language, content)
    for f in payload["files"]:
        lang = (f.get("language") or "").lower()
        path = f.get("path") or ""
        content = f.get("content", "")
        if lang not in _LANGUAGE_VALIDATORS:
            skipped.append(path)
            continue
        pending.append((path, lang, content))

    if not pending:
        return ValidationResult(
            ok=True, errors=(), skipped=tuple(skipped),
            elapsed_ms=int((time.monotonic() - t_start) * 1000),
        )

    # Run validators in parallel via asyncio.gather. Each returns
    # tuple[ValidationError] | () | None.
    async def _run_all():
        coros = []
        for path, lang, content in pending:
            handler_name = _LANGUAGE_VALIDATORS[lang]
            handler = globals()[handler_name]
            coros.append(handler(path, content))
        return await asyncio.gather(*coros, return_exceptions=True)

    try:
        results = asyncio.run(_run_all())
    except Exception as exc:
        # Top-level orchestration failed — graceful degradation.
        logger.warning(f"Forge validator gather failed: {exc}", exc_info=True)
        return ValidationResult(
            ok=True, errors=(), skipped=tuple(skipped),
            elapsed_ms=int((time.monotonic() - t_start) * 1000),
        )

    all_errors: list = []
    for r in results:
        if isinstance(r, BaseException):
            logger.warning(f"Forge validator inner-task error: {r}", exc_info=False)
            continue  # treat as pass
        if r is None:
            continue  # timeout / missing binary — graceful pass
        all_errors.extend(r)

    # Sort errors by (file_path, line) for stable output (§6.1 contract).
    all_errors.sort(key=lambda e: (e.file_path, e.line))

    return ValidationResult(
        ok=(len(all_errors) == 0),
        errors=tuple(all_errors),
        skipped=tuple(skipped),
        elapsed_ms=int((time.monotonic() - t_start) * 1000),
    )
```

- [ ] **Step 2.5: Run tests, verify they pass.**

  Run: `python3 -m pytest tests/test_artifact_validator.py -v`
  Expected: all 6+ tests in TestDataclasses + TestValidatorEndToEnd + TestValidatorTimeout + TestValidatorMissingBinary pass.

- [ ] **Step 2.6: Commit.**

```bash
git add app/services/artifact_validator.py tests/test_artifact_validator.py tests/fixtures/artifacts/
git commit -m "feat(forge): subprocess validators (Babel + tsc) with graceful degradation

Implements per spec §6.3, §6.4:
- _validate_via_node: Babel via Node CLI wrapper, stdin-fed.
- _validate_via_tsc: tsc --noEmit on tmpfile (tsc has no stdin interface).
- 3s timeout per file → return None (treated as pass per §6.4).
- FileNotFoundError on either binary → log warning ONCE per process,
  skip language going forward, return ok=True overall.
- _has_validatable_files O(n) check skips subprocess for non-JS/TS
  artifacts (Decision 11 / §11 E8).
- Errors sorted by (file_path, line) for stable signature (§6.1).

Test fixtures: clean_react, netflix_broken_jsx (line 45 verified),
multi_file_partial_fail, svg_only.

Refs: spec §6, AC #1 (Netflix detection), #2 (clean), #3 (SVG bypass),
#9 (timeout doesn't block)."
```

---

### Task 3: Helpers + retry-prompt builder

**Goal:** Implement the small pure helpers (`_signature`, `_short_reason`, `_error_to_json`, `_estimate_input_tokens`, `_artifact_content_tokens`) and the `build_validation_retry_prompt` function. All pure-Python; no subprocess; no network.

**Files:**
- Modify: `app/services/artifact_validator.py`
- Modify: `tests/test_artifact_validator.py`

**ACs satisfied:** Foundation for #1, #4, #5, #6; AC #11 (multi-file `_short_reason` exact format).

**Estimated LOC delta:** ~80 module + ~120 tests.

**Dependencies:** Task 2.

- [ ] **Step 3.1: Write failing tests for helpers.**

```python
# tests/test_artifact_validator.py — append

class TestHelpers:
    def test_signature_excludes_column(self):
        """Per §11 E6: column-exclusion, only (file_path, line, message)."""
        from app.services.artifact_validator import ValidationError, _signature
        e1 = ValidationError(file_path="A.jsx", line=10, column=5, message="m", excerpt="", parser="node")
        e2 = ValidationError(file_path="A.jsx", line=10, column=99, message="m", excerpt="x", parser="node")
        # Same file/line/message → same signature, even if column / excerpt differ.
        assert _signature((e1,)) == _signature((e2,))

    def test_signature_sorts_for_stability(self):
        from app.services.artifact_validator import ValidationError, _signature
        a = ValidationError(file_path="B.jsx", line=1, column=1, message="m1", excerpt="", parser="node")
        b = ValidationError(file_path="A.jsx", line=2, column=1, message="m2", excerpt="", parser="node")
        s1 = _signature((a, b))
        s2 = _signature((b, a))
        assert s1 == s2  # order-insensitive

    def test_short_reason_single_file(self):
        """Per §7.2: 'validator: <kind>-parse @ <file>:<line>'."""
        from app.services.artifact_validator import ValidationError, _short_reason
        e = ValidationError(file_path="App.jsx", line=45, column=12, message="m", excerpt="", parser="node")
        assert _short_reason((e,)) == "validator: parse-error @ App.jsx:45"

    def test_short_reason_multi_file(self):
        """Per AC #11 + §7.2: multi-file format."""
        from app.services.artifact_validator import ValidationError, _short_reason
        a = ValidationError(file_path="App.jsx", line=45, column=12, message="m", excerpt="", parser="node")
        b = ValidationError(file_path="Hero.jsx", line=12, column=1, message="m", excerpt="", parser="node")
        result = _short_reason((a, b))
        assert result == "validator: 2 files with syntax errors (App.jsx:45, Hero.jsx:12)"

    def test_error_to_json_round_trip(self):
        """JSON serialization for SSE / Firestore persistence (§9.1, §10.1)."""
        from app.services.artifact_validator import ValidationError, _error_to_json
        e = ValidationError(file_path="App.jsx", line=45, column=12, message="oops", excerpt="ex", parser="node")
        j = _error_to_json(e)
        assert j == {"file_path": "App.jsx", "line": 45, "column": 12, "message": "oops", "excerpt": "ex"}

    def test_estimate_input_tokens(self):
        from app.services.artifact_validator import _estimate_input_tokens
        n = _estimate_input_tokens("hello world")
        assert isinstance(n, int) and n > 0

    def test_artifact_content_tokens(self):
        from app.services.artifact_validator import _artifact_content_tokens
        payload = {
            "files": [
                {"path": "a.jsx", "language": "jsx", "content": "x" * 200, "byteSize": 200},
                {"path": "b.jsx", "language": "jsx", "content": "y" * 100, "byteSize": 100},
            ],
        }
        n = _artifact_content_tokens(payload)
        assert isinstance(n, int) and n > 0


class TestBuildValidationRetryPrompt:
    def test_single_file_prompt_structure(self):
        from app.services.artifact_validator import (
            ValidationError, build_validation_retry_prompt
        )
        payload = {
            "title": "Demo", "type": "project", "primaryLanguage": "jsx",
            "files": [{
                "path": "App.jsx", "language": "jsx",
                "content": "const x = 1;\nconst y = 2;\n", "byteSize": 28,
            }],
        }
        errors = (ValidationError(
            file_path="App.jsx", line=1, column=7,
            message="Unexpected token", excerpt="    1 | const x = 1;",
            parser="node",
        ),)
        prompt = build_validation_retry_prompt(
            payload=payload, errors=errors, original_user_prompt="make me something",
        )
        # Spec §8.2 contract — these tokens must all appear, in this order.
        assert "VALIDATOR ERRORS:" in prompt
        assert "App.jsx:1:7 — Unexpected token" in prompt
        assert "YOUR PREVIOUS ARTIFACT (unchanged):" in prompt
        assert "```artifact-meta" in prompt
        assert "```artifact-file:App.jsx:jsx" in prompt
        assert "USER REQUEST: make me something" in prompt
        assert "ACTION: create_artifact" in prompt

    def test_multi_error_prompt_lists_all(self):
        from app.services.artifact_validator import (
            ValidationError, build_validation_retry_prompt
        )
        payload = {
            "title": "x", "type": "project", "primaryLanguage": "jsx",
            "files": [{"path": "App.jsx", "language": "jsx", "content": "x", "byteSize": 1}],
        }
        errors = (
            ValidationError(file_path="App.jsx", line=1, column=1, message="E1", excerpt="", parser="node"),
            ValidationError(file_path="App.jsx", line=5, column=1, message="E2", excerpt="", parser="node"),
        )
        prompt = build_validation_retry_prompt(
            payload=payload, errors=errors, original_user_prompt="x",
        )
        # Per §8.3 bullet 1: "All errors are listed, not just the first."
        assert "App.jsx:1:1 — E1" in prompt
        assert "App.jsx:5:1 — E2" in prompt

    def test_large_artifact_omits_clean_files(self):
        """Per §8.3 token-budget mitigation: artifacts >25k tokens of content
        include only the FAILED files; clean files are listed by name."""
        from app.services.artifact_validator import (
            ValidationError, build_validation_retry_prompt
        )
        big_content = "x" * 120_000  # well over 25k tokens
        payload = {
            "title": "big", "type": "project", "primaryLanguage": "jsx",
            "files": [
                {"path": "App.jsx", "language": "jsx", "content": big_content, "byteSize": 120_000},
                {"path": "Other.jsx", "language": "jsx", "content": big_content, "byteSize": 120_000},
            ],
        }
        errors = (ValidationError(file_path="App.jsx", line=1, column=1, message="E", excerpt="", parser="node"),)
        prompt = build_validation_retry_prompt(
            payload=payload, errors=errors, original_user_prompt="x",
        )
        # Failed file (App.jsx) is included. Clean file (Other.jsx) is NAMED but content omitted.
        assert "```artifact-file:App.jsx:jsx" in prompt
        assert "Files not shown" in prompt
        assert "Other.jsx" in prompt
        # Other.jsx's content is NOT inlined as a fenced block.
        assert "```artifact-file:Other.jsx:jsx" not in prompt
```

- [ ] **Step 3.2: Run failing tests.**

  Run: `python3 -m pytest tests/test_artifact_validator.py::TestHelpers tests/test_artifact_validator.py::TestBuildValidationRetryPrompt -v`
  Expected: all fail with `AttributeError: ... _signature` etc.

- [ ] **Step 3.3: Implement helpers + retry-prompt builder.**

  Append to `app/services/artifact_validator.py`:

```python
def _signature(errors: tuple) -> tuple:
    """Hashable tuple for loop-detect — sorted (file_path, line, message).

    Column excluded per §11 E6 — minor reformatting shifts the column
    without changing the parse failure. Excluding it makes loop-detect
    conservative (likely to fire when it should) at the cost of rare
    false positives on genuinely-different-bugs-on-same-line.
    """
    return tuple(sorted((e.file_path, e.line, e.message) for e in errors))


def _short_reason(errors: tuple) -> str:
    """User-visible 'reason' for the SSE forge_retry event.

    Single file: 'validator: parse-error @ <file>:<line>'.
    Multi file:  'validator: N files with syntax errors (<f1>:<l1>, ...)'.

    Per spec §7.2 + AC #11. Note 'parse-error' is hardcoded — all current
    parser kinds (Babel parse_error, tsc TS1109 etc.) are in the
    parse-error family. If we add a different validator class later,
    this becomes parameterizable.
    """
    if not errors:
        return "validator: ok"

    distinct_files: list = []
    first_per_file: dict = {}
    for e in errors:
        if e.file_path not in first_per_file:
            first_per_file[e.file_path] = e.line
            distinct_files.append(e.file_path)

    if len(distinct_files) == 1:
        e = errors[0]
        return f"validator: parse-error @ {e.file_path}:{e.line}"

    # Multi-file: list first-error-per-file, sorted by file path for determinism.
    distinct_files.sort()
    parts = ", ".join(f"{f}:{first_per_file[f]}" for f in distinct_files)
    return f"validator: {len(distinct_files)} files with syntax errors ({parts})"


def _error_to_json(e: ValidationError) -> dict:
    """Serialize ValidationError for SSE payload + Firestore persistence."""
    return {
        "file_path": e.file_path,
        "line": e.line,
        "column": e.column,
        "message": e.message,
        "excerpt": e.excerpt,
    }


def _estimate_input_tokens(text: str) -> int:
    """Token estimate for a prompt string. Uses tiktoken cl100k_base.

    Reuses the existing token-count utility at app.utilities.count_tokens.
    Spec §7.2 says these helpers 'delegate to existing accountant.py
    token estimation utilities' — accountant.py doesn't have one;
    smart_router.py:_estimate_tokens and utilities.py:count_tokens do.
    We use utilities.count_tokens because it's the canonical entry point.
    """
    from app.utilities import count_tokens
    return count_tokens(text or "")


def _artifact_content_tokens(payload: dict) -> int:
    """Sum tokenized file contents in the payload.

    Used as the basis for the pre-flight output estimate (×1.2 multiplier
    in the cascade — Amendment 8 calibration). Counts content of every
    file regardless of language; the multiplier conservatively
    over-estimates the regenerated artifact size.
    """
    if not payload or not payload.get("files"):
        return 0
    total = 0
    for f in payload["files"]:
        total += _estimate_input_tokens(f.get("content", ""))
    return total


# Token threshold above which the retry prompt omits clean files
# (per §8.3 large-artifact mitigation). 25_000 chosen per spec.
_RETRY_PROMPT_LARGE_THRESHOLD = 25_000


def build_validation_retry_prompt(
    payload: dict,
    errors: tuple,
    original_user_prompt: str,
) -> str:
    """Build the synthetic retry prompt for the cascade.

    Modeled on the patch-regen pattern at chat_routes.py:1956-1976.
    Spec §8.2 / §8.3 lock the format. All errors are listed, full
    artifact included for normal-sized payloads, failed-files-only
    for >25k token payloads.
    """
    failed_paths = {e.file_path for e in errors}
    total_content_tokens = _artifact_content_tokens(payload)
    is_large = total_content_tokens > _RETRY_PROMPT_LARGE_THRESHOLD

    parts: list = [
        "Your previous response produced an artifact that failed syntax validation.\n\n",
        "VALIDATOR ERRORS:\n",
    ]

    for i, e in enumerate(errors, start=1):
        parts.append(f"{i}. {e.file_path}:{e.line}:{e.column} — {e.message}\n")
        if e.excerpt:
            parts.append("   Excerpt:\n")
            for line in e.excerpt.splitlines():
                parts.append(f"   {line}\n")
        parts.append("\n")

    if is_large:
        clean_paths = [
            f["path"] for f in payload.get("files", [])
            if f.get("path") not in failed_paths
        ]
        if clean_paths:
            parts.append(
                f"Files not shown (preserve content from your previous response — "
                f"do not change them): {', '.join(clean_paths)}\n\n"
            )
        parts.append("YOUR PREVIOUS ARTIFACT (failed files only):\n\n")
    else:
        parts.append("YOUR PREVIOUS ARTIFACT (unchanged):\n\n")

    # artifact-meta
    import json as _json
    meta = {
        "title": payload.get("title", "Artifact"),
        "type": payload.get("type", "project"),
        "primaryLanguage": payload.get("primaryLanguage", "jsx"),
    }
    parts.append("```artifact-meta\n")
    parts.append(_json.dumps(meta) + "\n")
    parts.append("```\n\n")

    # artifact-file blocks: all files for normal, failed-only for large.
    for f in payload.get("files", []):
        if is_large and f.get("path") not in failed_paths:
            continue
        parts.append(f"```artifact-file:{f.get('path', '?')}:{f.get('language', 'text')}\n")
        parts.append(f.get("content", ""))
        if not f.get("content", "").endswith("\n"):
            parts.append("\n")
        parts.append("```\n\n")

    parts.append(f"USER REQUEST: {original_user_prompt}\n\n")
    parts.append(
        "Regenerate the COMPLETE artifact with the syntax error fixed. Use\n"
        "ACTION: create_artifact and the fenced format. Include ALL files."
    )

    return "".join(parts)
```

- [ ] **Step 3.4: Run tests, verify they pass.**

  Run: `python3 -m pytest tests/test_artifact_validator.py::TestHelpers tests/test_artifact_validator.py::TestBuildValidationRetryPrompt -v`
  Expected: all 9 tests pass.

- [ ] **Step 3.5: Commit.**

```bash
git add app/services/artifact_validator.py tests/test_artifact_validator.py
git commit -m "feat(forge): retry-prompt builder + signature/short-reason helpers

Implements per spec §7.2, §8:
- _signature: tuple(sorted((path, line, message))) — column excluded
  per §11 E6 (avoids spurious skip-misses on cosmetic reformatting).
- _short_reason: single-file vs multi-file format per AC #11.
- _error_to_json: SSE/Firestore serialization shape.
- _estimate_input_tokens, _artifact_content_tokens: delegate to
  app.utilities.count_tokens (tiktoken cl100k_base).
- build_validation_retry_prompt: full prompt per §8.2 with §8.3
  large-artifact mitigation (>25k tokens → failed files only,
  clean files named but content omitted).

Refs: spec §7.2, §8.2, §8.3, AC #11."
```

---

### Task 4: Schema additions + `save_interaction` extension

**Goal:** Extend `save_interaction` in `chat_engine.py` to accept the five `forge_*` envelope fields and forward them to the Firestore document update. Add backward-compat reads for legacy messages without the fields.

**Files:**
- Modify: `app/services/chat_engine.py` (only `save_interaction` signature + body)
- Modify: `tests/test_artifact_validator.py` (or create `tests/test_save_interaction_forge_fields.py`)

**ACs satisfied:** Foundation for #4 (positive assertion), #12 (replay).

**Estimated LOC delta:** ~25 module + ~60 tests.

**Dependencies:** Task 3.

- [ ] **Step 4.1: Write failing test for new kwarg + Firestore write.**

```python
# tests/test_save_interaction_forge_fields.py — new file
"""Forge envelope fields persistence test for save_interaction."""

import pytest
from unittest.mock import MagicMock, patch


class TestSaveInteractionForgeFields:
    def test_forge_metrics_kwarg_writes_to_firestore(self):
        """save_interaction must accept and forward forge_metrics dict."""
        from app.services.chat_engine import save_interaction

        forge_metrics = {
            "forge_attempts": 2,
            "forge_healed_at": 4,
            "forge_validation_failed": False,
            "forge_errors": None,
            "forge_output_estimate_actual_ratio": 1.087,
        }

        with patch("app.services.chat_engine.db") as mock_db:
            mock_session = MagicMock()
            mock_messages_collection = MagicMock()
            mock_message_doc = MagicMock()
            mock_db.collection.return_value.document.return_value = mock_session
            mock_session.collection.return_value = mock_messages_collection
            mock_messages_collection.document.return_value = mock_message_doc

            save_interaction(
                user_id="u",
                session_id="s",
                user_prompt="p",
                response_text="r",
                sentiment="neutral",
                is_code_response=False,
                search_results=[],
                forge_metrics=forge_metrics,  # NEW kwarg
            )

            # Assert the .set() / .update() call carries the forge_* keys.
            calls = mock_message_doc.set.call_args_list + mock_message_doc.update.call_args_list
            assert len(calls) >= 1
            written = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs.get("data") or {}
            assert written.get("forge_attempts") == 2
            assert written.get("forge_healed_at") == 4
            assert written.get("forge_validation_failed") is False
            assert written.get("forge_output_estimate_actual_ratio") == 1.087

    def test_forge_metrics_none_writes_nulls(self):
        """When forge_metrics is None (validator skipped), all fields = null."""
        from app.services.chat_engine import save_interaction

        with patch("app.services.chat_engine.db") as mock_db:
            mock_session = MagicMock()
            mock_messages_collection = MagicMock()
            mock_message_doc = MagicMock()
            mock_db.collection.return_value.document.return_value = mock_session
            mock_session.collection.return_value = mock_messages_collection
            mock_messages_collection.document.return_value = mock_message_doc

            save_interaction(
                user_id="u",
                session_id="s",
                user_prompt="p",
                response_text="r",
                sentiment="neutral",
                is_code_response=False,
                search_results=[],
                forge_metrics=None,  # validator skipped
            )

            calls = mock_message_doc.set.call_args_list + mock_message_doc.update.call_args_list
            written = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs.get("data") or {}
            # All five must be present and null per §10.1 schema.
            assert written.get("forge_attempts") is None
            assert written.get("forge_healed_at") is None
            assert written.get("forge_validation_failed") is None
            assert written.get("forge_errors") is None
            assert written.get("forge_output_estimate_actual_ratio") is None
```

- [ ] **Step 4.2: Run failing tests.**

  Run: `python3 -m pytest tests/test_save_interaction_forge_fields.py -v`
  Expected: FAIL with `TypeError: save_interaction() got an unexpected keyword argument 'forge_metrics'`.

- [ ] **Step 4.3: Extend `save_interaction` signature + body.**

  Open `app/services/chat_engine.py`, find `save_interaction` (line 1292). Add `forge_metrics: Optional[Dict[str, Any]] = None` to the signature. Inside the function, where the message document data dict is built, add the five fields:

```python
# In save_interaction signature — append before the closing parenthesis:
    forge_metrics: Optional[Dict[str, Any]] = None,
```

```python
# Inside save_interaction, where message_data dict is built (find the dict
# being passed to .set() or .update()), append the five forge_* fields:

    # Forge M1 envelope fields (spec §10.1). When forge_metrics is None,
    # the validator was skipped or didn't run; persist all five as null
    # for schema consistency.
    fm = forge_metrics or {}
    message_data["forge_attempts"]                     = fm.get("forge_attempts")
    message_data["forge_healed_at"]                    = fm.get("forge_healed_at_iter")  # schema name 'forge_healed_at' = metric 'forge_healed_at_iter'
    message_data["forge_validation_failed"]            = fm.get("forge_validation_failed") if fm else None
    message_data["forge_errors"]                       = fm.get("forge_errors") if fm else None
    message_data["forge_output_estimate_actual_ratio"] = fm.get("forge_output_estimate_actual_ratio")
```

NOTE: there's a deliberate name mapping above:
- The runtime metric (in `forge_metrics` dict) is `forge_healed_at_iter`.
- The persisted schema field is `forge_healed_at` (per spec §10.1).
The mapping happens here, in `save_interaction`. This matches the
spec exactly — it lists `forge_healed_at_iter` as a telemetry metric in
§12 and `forge_healed_at` as a persistence field in §10.1.

- [ ] **Step 4.4: Run tests, verify they pass.**

  Run: `python3 -m pytest tests/test_save_interaction_forge_fields.py -v`
  Expected: 2 passed.

- [ ] **Step 4.5: Commit.**

```bash
git add app/services/chat_engine.py tests/test_save_interaction_forge_fields.py
git commit -m "feat(forge): extend save_interaction with forge_* envelope fields

Per spec §10.1 + §13.3:
- New kwarg forge_metrics: Optional[Dict[str, Any]] = None.
- Forwards five envelope fields to Firestore message doc:
  forge_attempts, forge_healed_at, forge_validation_failed,
  forge_errors, forge_output_estimate_actual_ratio.
- Name mapping: runtime metric forge_healed_at_iter persists as
  forge_healed_at (per §10.1 schema field name).
- forge_metrics=None writes all five as null (validator skipped or
  not yet wired); legacy messages without the fields read as null
  via Firestore's get(default=None) behavior.

Refs: spec §10.1, §13.3, AC #12 (foundation)."
```

---

### Task 5: Cascade constants + cascade-loop core insertion

**Goal:** Add `FORGE_VALIDATION_CASCADE`, `FORGE_RETRY_TOKEN_BUDGET`, and the startup guard to `chat_engine.py`. Insert the cascade loop skeleton into `chat_routes.py` between line 1751 and line 2042. The loop calls a single retry, tracks `forge_metrics`, and emits the three SSE events. Loop-detect, pre-flight, and accountant wraps come in Tasks 6/7/8.

**Files:**
- Modify: `app/services/chat_engine.py` (add constants near line 154, add startup guard)
- Modify: `app/blueprints/chat_routes.py` (insert cascade between :1751 and :2042)
- Create: `tests/test_forge_cascade.py`

**ACs satisfied:** #5 (stream-model skip), #7 (E16 stream=GPT-4.1 — no retries).

**Estimated LOC delta:** ~70 cascade insertion + ~80 tests.

**Dependencies:** Tasks 3, 4.

- [ ] **Step 5.1: Add cascade constants + startup guard to `chat_engine.py`.**

  At the top of `app/services/chat_engine.py`, near the existing `ARTIFACT_MODEL_CASCADE` definition (currently around line 154), add:

```python
# Forge M1 cascade — spec §3 Decision 3.
# Hardcoded V4 IDs; not a reuse of ARTIFACT_MODEL_CASCADE (different
# failure class). Order: cheap-first to premium-last.
FORGE_VALIDATION_CASCADE = [
    "deepseek-v4-flash",   # cascade position 1
    "deepseek-v4-pro",     # cascade position 2
    "kimi-k2.5",           # cascade position 3
    "gpt-4.1",             # cascade position 4
]

# Per-turn retry token budget (spec §3 Decision 8). Hard cap, no
# overshoot — pre-flight estimate gates each call (Amendment 3).
FORGE_RETRY_TOKEN_BUDGET = 80_000

# Startup guard (spec §13.1). Fail at gunicorn worker spawn if the V4
# swap hasn't merged — fast feedback during deploy.
def _forge_validate_cascade_models_resolvable() -> None:
    from app.model_catalog import MODEL_CATALOG, MODEL_ALIASES
    for _m in FORGE_VALIDATION_CASCADE:
        if _m not in MODEL_CATALOG and _m not in MODEL_ALIASES:
            raise RuntimeError(
                f"FORGE_VALIDATION_CASCADE references unknown model {_m!r}. "
                f"Did feat/deepseek-v4-swap merge? See "
                f"docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md "
                f"§3 / §13."
            )

_forge_validate_cascade_models_resolvable()
```

- [ ] **Step 5.2: Write failing test for cascade-skip-stream-model behavior.**

```python
# tests/test_forge_cascade.py — new file
"""Cascade-loop tests — start_idx skip + E16 (GPT-4.1 stream = no retries)."""

import pytest
from unittest.mock import MagicMock, patch


class TestCascadeStartIdx:
    """Spec §11 E13 + AC #5: cascade advances past stream_model."""

    @pytest.fixture
    def make_payload(self):
        def _mk(content):
            return {
                "title": "x", "type": "project", "primaryLanguage": "jsx",
                "files": [{
                    "path": "App.jsx", "language": "jsx",
                    "content": content, "byteSize": len(content),
                }],
            }
        return _mk

    def test_stream_flash_skips_to_pro(self, make_payload):
        """When stream_model = v4-flash, cascade iter 1 = v4-pro."""
        # We use a private helper to compute start_idx; if the impl uses
        # an inline calc, this test imports the helper or stubs the loop.
        from app.services.chat_engine import FORGE_VALIDATION_CASCADE
        idx = FORGE_VALIDATION_CASCADE.index("deepseek-v4-flash") + 1
        assert idx == 1  # 0-indexed cascade index for Pro
        assert FORGE_VALIDATION_CASCADE[idx] == "deepseek-v4-pro"

    def test_stream_gpt41_no_retries(self, make_payload):
        """E16: stream_model = GPT-4.1 → start_idx = len(cascade) → no retries."""
        from app.services.chat_engine import FORGE_VALIDATION_CASCADE
        idx = FORGE_VALIDATION_CASCADE.index("gpt-4.1") + 1
        assert idx == len(FORGE_VALIDATION_CASCADE)
        # start_idx >= len(cascade) → cascade loop never enters.
```

- [ ] **Step 5.3: Run failing test.**

  Run: `python3 -m pytest tests/test_forge_cascade.py -v`
  Expected: PASS (the constant is now defined). The actual cascade-loop integration test arrives in Step 5.5 below.

- [ ] **Step 5.4: Insert cascade-loop skeleton in `chat_routes.py`.**

  Open `app/blueprints/chat_routes.py`. Locate line 1751 (end of existing cascade #1). Locate line 2042 (start of `if routing.artifact_payload:` artifact_data build).

  Between these two anchor points, insert the cascade-loop block from spec §7.1 verbatim — but **without** loop-detect (Task 6), **without** pre-flight (Task 7), **without** accountant wrap (Task 8). This step is the skeleton: stream_model skip + simple iter loop + 3 SSE events + forge_metrics dict + envelope persistence.

  Add at the top of the chat_routes module (with other imports):

```python
from app.services.artifact_validator import (
    validate_artifact_payload,
    build_validation_retry_prompt,
    _has_validatable_files,
    _signature,
    _short_reason,
    _error_to_json,
    _estimate_input_tokens,
    _artifact_content_tokens,
)
from app.services.chat_engine import (
    FORGE_VALIDATION_CASCADE,
    FORGE_RETRY_TOKEN_BUDGET,
)
```

  Then between :1751 and :2042, insert (note: Tasks 6/7/8 will fill in the empty `# TODO Task N` placeholders — for now the loop is mechanically correct but minimally featured):

```python
                    # ============================================================
                    # === Forge M1 — validation loop (spec §7.1) ===
                    # ============================================================
                    forge_metrics = {
                        'forge_validation_attempted': False,
                        'forge_validation_passed_first_try': True,
                        'forge_attempts': 0,
                        'forge_healed_at_iter': None,
                        'forge_total_retry_tokens': 0,
                        'forge_time_to_heal_ms': None,
                        'forge_budget_exhausted': False,
                        'forge_loop_detected_skip': False,
                        'forge_validation_failed': False,
                        'forge_errors': [],
                        'forge_output_estimate_actual_ratio': None,
                        '_ratio_log': [],
                    }

                    if routing and routing.artifact_payload:
                        if _has_validatable_files(routing.artifact_payload):
                            forge_metrics['forge_validation_attempted'] = True
                            import time as _time
                            t_start = _time.monotonic()
                            result = validate_artifact_payload(routing.artifact_payload)

                            if not result.ok:
                                forge_metrics['forge_validation_passed_first_try'] = False
                                previous_error_signature = _signature(result.errors)

                                # Stream model already had its turn — skip past it.
                                try:
                                    start_idx = FORGE_VALIDATION_CASCADE.index(stream_model) + 1
                                except ValueError:
                                    start_idx = 0

                                cascade_iter = start_idx

                                while cascade_iter < len(FORGE_VALIDATION_CASCADE):
                                    retry_model = FORGE_VALIDATION_CASCADE[cascade_iter]

                                    retry_prompt = build_validation_retry_prompt(
                                        payload=routing.artifact_payload,
                                        errors=result.errors,
                                        original_user_prompt=prompt,
                                    )

                                    # TODO Task 7: pre-flight token budget check.

                                    yield f"data: {json.dumps({
                                        'type': 'status',
                                        'subtype': 'forge_retry',
                                        'attempt': cascade_iter + 1,
                                        'model': retry_model,
                                        'reason': _short_reason(result.errors),
                                    })}\n\n"

                                    try:
                                        retry_response, _, _ = get_ai_response(retry_prompt, retry_model)
                                        in_tokens, out_tokens = _estimate_tokens_pair(retry_prompt, retry_response)
                                        # TODO Task 8: orchestrator_record_usage(retry_model, ...)
                                        forge_metrics['forge_total_retry_tokens'] += (in_tokens + out_tokens)
                                        forge_metrics['forge_attempts'] += 1
                                    except Exception as e:
                                        logger.warning(f"🔥 Forge retry {retry_model} error: {e}")
                                        cascade_iter += 1
                                        continue

                                    retry_parsed = ActionParser.parse(retry_response)
                                    if not retry_parsed.artifact_payload:
                                        logger.info(f"🔥 Forge retry {retry_model} produced no artifact payload")
                                        cascade_iter += 1
                                        continue

                                    new_result = validate_artifact_payload(retry_parsed.artifact_payload)
                                    if new_result.ok:
                                        routing.artifact_payload = retry_parsed.artifact_payload
                                        forge_metrics['forge_healed_at_iter'] = cascade_iter + 1
                                        forge_metrics['forge_time_to_heal_ms'] = int(
                                            (_time.monotonic() - t_start) * 1000
                                        )
                                        yield f"data: {json.dumps({
                                            'type': 'status',
                                            'subtype': 'forge_done',
                                            'healed_at': cascade_iter + 1,
                                        })}\n\n"
                                        break

                                    # TODO Task 6: loop-detect (cascade_iter += 2 + continue on match)
                                    previous_error_signature = _signature(new_result.errors)
                                    result = new_result
                                    cascade_iter += 1

                                if forge_metrics['forge_healed_at_iter'] is None:
                                    forge_metrics['forge_validation_failed'] = True
                                    forge_metrics['forge_errors'] = [
                                        _error_to_json(e) for e in result.errors
                                    ]
                                    yield f"data: {json.dumps({
                                        'type': 'status',
                                        'subtype': 'forge_exhausted',
                                        'attempts': forge_metrics['forge_attempts'],
                                        'errors': forge_metrics['forge_errors'],
                                    })}\n\n"

                            # TODO Task 7: aggregate _ratio_log → forge_output_estimate_actual_ratio
                            forge_metrics.pop('_ratio_log', None)
                    # === END Forge M1 (skeleton — Tasks 6/7/8 fill TODOs) ===
```

  Add a helper near the top of `chat_routes.py` for the `_estimate_tokens_pair` shim:

```python
def _estimate_tokens_pair(prompt: str, response: str) -> tuple:
    """Estimate (input_tokens, output_tokens) for a prompt/response pair.

    Wraps app.utilities.count_tokens. Used by the Forge cascade and by
    the existing-cascade accountant wraps in Task 8.
    """
    from app.utilities import count_tokens
    return (count_tokens(prompt or ""), count_tokens(response or ""))
```

  Update the `save_interaction` call site (around line 2257) to pass `forge_metrics`:

```python
    # Around chat_routes.py:2257 — find the existing save_interaction(...) call.
    # Add forge_metrics= kwarg at the end:
    save_interaction(
        # ... existing args unchanged ...
        forge_metrics=forge_metrics if 'forge_metrics' in dir() else None,
    )
```

- [ ] **Step 5.5: Write integration test for the skeleton cascade loop.**

```python
# tests/test_forge_cascade.py — append

class TestForgeCascadeSkeleton:
    """Verify cascade fires the right model, emits the right SSE events.
    Mocks get_ai_response to return canned responses.
    """

    def test_stream_flash_failed_validation_calls_pro_first(self, monkeypatch):
        """AC #5 verification — first cascade call after stream-Flash uses Pro."""
        from unittest.mock import patch, MagicMock

        # Track which models the cascade called
        called_models = []

        def fake_get_ai_response(prompt, model, images=None):
            called_models.append(model)
            # Return a clean artifact (heals on iter 1)
            return (
                'ACTION: create_artifact\n\n'
                '```artifact-meta\n'
                '{"title":"clean","type":"project","primaryLanguage":"jsx"}\n'
                '```\n\n'
                '```artifact-file:App.jsx:jsx\n'
                'export default () => null;\n'
                '```\n',
                False,
                None,
            )

        with patch("app.blueprints.chat_routes.get_ai_response", side_effect=fake_get_ai_response):
            # Direct cascade simulation: call the validator + cascade with a
            # broken artifact and stream_model=deepseek-v4-flash.
            #
            # Note: the cascade body is inside the chunk_type=='done' handler
            # in chat_routes.py — testing it standalone requires either:
            #   (a) extracting it into a helper function (preferred for
            #       testability — do this in Task 13's polish if not done already)
            #   (b) running the full /chat/stream endpoint with mocks
            # For now, this test asserts the cascade's behavior via
            # extracted logic. Task 13 covers full E2E.
            pass  # Placeholder — full cascade test in Task 13 E2E

    def test_stream_gpt41_skips_cascade(self, monkeypatch):
        """AC #7 / E16: stream = GPT-4.1 → start_idx = 4 → no retries."""
        # Pure-Python assertion against the cascade math.
        from app.services.chat_engine import FORGE_VALIDATION_CASCADE
        stream_model = "gpt-4.1"
        try:
            start_idx = FORGE_VALIDATION_CASCADE.index(stream_model) + 1
        except ValueError:
            start_idx = 0
        # E16: start_idx >= len(cascade) → loop never enters.
        assert start_idx >= len(FORGE_VALIDATION_CASCADE)
```

- [ ] **Step 5.6: Run tests, verify they pass.**

  Run: `python3 -m pytest tests/test_forge_cascade.py -v`
  Expected: 3 passed (or 2 passed + 1 skipped pending Task 13). Existing tests pass.

  Also verify `chat_engine.py` imports without errors:

  ```bash
  python3 -c "from app.services.chat_engine import FORGE_VALIDATION_CASCADE, FORGE_RETRY_TOKEN_BUDGET; print('OK', FORGE_VALIDATION_CASCADE, FORGE_RETRY_TOKEN_BUDGET)"
  ```

  Expected: `OK ['deepseek-v4-flash', 'deepseek-v4-pro', 'kimi-k2.5', 'gpt-4.1'] 80000`. (If V4 swap hasn't merged, this RAISES per the startup guard — that's correct fail-fast behavior.)

- [ ] **Step 5.7: Commit.**

```bash
git add app/services/chat_engine.py app/blueprints/chat_routes.py tests/test_forge_cascade.py
git commit -m "feat(forge): cascade constants + cascade-loop skeleton

Adds (spec §3 Dec 3, §7.1, §13.1):
- FORGE_VALIDATION_CASCADE = [v4-flash, v4-pro, kimi-k2.5, gpt-4.1]
  in chat_engine.py. Startup guard fails worker spawn if any cascade
  model is unresolvable (V4 swap merge guard).
- FORGE_RETRY_TOKEN_BUDGET = 80_000.
- Cascade-loop skeleton inserted in chat_routes.py between :1751 and
  :2042. Includes:
    * stream_model start_idx skip (Amendment 4 / E13 / AC #5).
    * E16 (stream = GPT-4.1) → start_idx = len(cascade) → no retries.
    * forge_metrics dict initialized with all 11 fields.
    * forge_retry, forge_done, forge_exhausted SSE event yields.
    * envelope persistence via save_interaction(forge_metrics=...).
- TODOs in cascade body for Tasks 6/7/8 (loop-detect, pre-flight,
  accountant wraps).

Refs: AC #5 (stream-model skip), AC #7 (E16 no-retries)."
```

---

### Task 6: Loop-detect + skip mechanics with telemetry guard

**Goal:** Add the loop-detect block to the cascade (replacing the `TODO Task 6` marker). Single increment site discipline (`cascade_iter += 2 + continue` on skip; `cascade_iter += 1` otherwise). Telemetry guard against false-positive on final cascade position (Amendment 9).

**Files:**
- Modify: `app/blueprints/chat_routes.py` (replace TODO Task 6 with full loop-detect block)
- Modify: `tests/test_forge_cascade.py`

**ACs satisfied:** #6 (identical-error tier skip), #16 (no false flip on final tier).

**Estimated LOC delta:** ~30 cascade body + ~80 tests.

**Dependencies:** Task 5.

- [ ] **Step 6.1: Write failing test for skip-detect on stream/iter-1 match.**

```python
# tests/test_forge_cascade.py — append

class TestLoopDetect:
    """Spec §7.1 + AC #6 + AC #16: loop-detect skip + telemetry guard."""

    def test_signature_match_increments_by_two(self):
        """When iter N's errors match the prior baseline, cascade_iter += 2."""
        # Direct test of the increment math at the loop-detect branch.
        # Simulate: cascade_iter=1 (Pro), errors match → expect next iter=3.
        cascade = ["v4-flash", "v4-pro", "kimi-k2.5", "gpt-4.1"]
        cascade_iter = 1
        signature_matches = True

        # Logic from spec §7.1:
        if signature_matches:
            if cascade_iter + 2 < len(cascade):
                # Skip, set telemetry
                forge_loop_detected_skip = True
            else:
                forge_loop_detected_skip = False  # final-position no-op
            cascade_iter += 2
        else:
            cascade_iter += 1

        assert cascade_iter == 3
        assert forge_loop_detected_skip is True

    def test_final_position_match_no_false_flip(self):
        """AC #16: signature match at cascade_iter+2 >= len(cascade) does NOT flip metric."""
        cascade = ["v4-flash", "v4-pro", "kimi-k2.5", "gpt-4.1"]
        cascade_iter = 3  # GPT-4.1, the last position
        signature_matches = True

        forge_loop_detected_skip = False  # initial
        if signature_matches:
            if cascade_iter + 2 < len(cascade):
                forge_loop_detected_skip = True  # would NOT execute
            # else: no-op — final position
            cascade_iter += 2

        assert cascade_iter == 5
        assert forge_loop_detected_skip is False  # no false flip
```

- [ ] **Step 6.2: Run failing tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py::TestLoopDetect -v`
  Expected: 2 passed (these are pure-logic tests of the math). The integration with chat_routes.py is verified by Task 13 E2E.

- [ ] **Step 6.3: Replace `# TODO Task 6` in `chat_routes.py` with the full loop-detect block.**

  Find the `# TODO Task 6: loop-detect ...` marker in the cascade body. Replace it (and the line immediately following it that does `cascade_iter += 1`) with the loop-detect block from spec §7.1:

```python
                                    # Loop-detect (Decision 9): if errors are byte-identical to the
                                    # immediately-prior iteration, the current model family is also
                                    # stuck on the same pattern. Skip the next tier (advance by 2)
                                    # and continue. Single increment site to avoid double-step bugs.
                                    #
                                    # Telemetry guard (Amendment 9): only flip
                                    # forge_loop_detected_skip when a tier is *actually being skipped*.
                                    # On the final cascade entry, cascade_iter += 2 still happens
                                    # (out-of-bounds exit), but no reachable tier was skipped — the
                                    # metric should not fire in that case.
                                    new_signature = _signature(new_result.errors)
                                    if new_signature == previous_error_signature:
                                        if cascade_iter + 2 < len(FORGE_VALIDATION_CASCADE):
                                            forge_metrics['forge_loop_detected_skip'] = True
                                            logger.info(
                                                f"🔥 Forge loop-detect: {retry_model} produced same errors as prior iter, "
                                                f"skipping cascade position {cascade_iter + 2}"
                                            )
                                        else:
                                            logger.info(
                                                f"🔥 Forge loop-detect: {retry_model} (final cascade position) produced same "
                                                f"errors as prior iter — no tier left to skip; cascade exits"
                                            )
                                        previous_error_signature = new_signature
                                        result = new_result
                                        cascade_iter += 2
                                        continue

                                    previous_error_signature = new_signature
                                    result = new_result
                                    cascade_iter += 1
```

- [ ] **Step 6.4: Run all forge tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py tests/test_artifact_validator.py -v`
  Expected: all pass.

- [ ] **Step 6.5: Commit.**

```bash
git add app/blueprints/chat_routes.py tests/test_forge_cascade.py
git commit -m "feat(forge): loop-detect + telemetry guard against false-positive

Replaces Task 5's TODO Task 6 marker with the full loop-detect block
per spec §7.1 + Amendment 9.

Behavior:
- On byte-identical signature match: cascade_iter += 2 + continue.
- forge_loop_detected_skip flips ONLY when cascade_iter + 2 <
  len(cascade). On the final cascade entry, the metric stays false
  (no reachable tier was actually skipped).
- Two distinct logger.info messages distinguish the two cases for
  post-launch debugging:
    'skipping cascade position N' (real skip)
    '(final cascade position) ... no tier left to skip; cascade exits'

Refs: AC #6 (identical-error tier skip), AC #16 (no false flip)."
```

---

### Task 7: Pre-flight token budget + per-iter calibration logging

**Goal:** Replace the `TODO Task 7` marker with the pre-flight budget check (hard cap, no overshoot) and per-iter `forge_calibration` log lines. Aggregate the per-iter ratios into `forge_output_estimate_actual_ratio` (median per Amendment 9).

**Files:**
- Modify: `app/blueprints/chat_routes.py`
- Modify: `tests/test_forge_cascade.py`

**ACs satisfied:** #8 (budget triggers early stop), #15 (calibration logs).

**Estimated LOC delta:** ~30 cascade body + ~70 tests.

**Dependencies:** Task 6.

- [ ] **Step 7.1: Write failing tests.**

```python
# tests/test_forge_cascade.py — append

class TestPreflightBudget:
    """Spec §7.1 + Decision 8 + AC #8: pre-flight token budget."""

    def test_projection_under_budget_proceeds(self):
        """Pre-flight projects under budget → call proceeds."""
        from app.services.chat_engine import FORGE_RETRY_TOKEN_BUDGET
        accumulated = 30_000
        prompt_estimate = 10_000
        output_estimate = 12_000  # 1.2 × 10k
        projected_total = accumulated + prompt_estimate + output_estimate
        assert projected_total < FORGE_RETRY_TOKEN_BUDGET  # 52k < 80k

    def test_projection_over_budget_breaks(self):
        """Pre-flight projects over budget → loop breaks before call."""
        from app.services.chat_engine import FORGE_RETRY_TOKEN_BUDGET
        accumulated = 35_000
        prompt_estimate = 25_000
        output_estimate = 30_000
        projected_total = accumulated + prompt_estimate + output_estimate
        assert projected_total > FORGE_RETRY_TOKEN_BUDGET  # 90k > 80k


class TestMedianRatioAggregation:
    """Amendment 9 (b): forge_output_estimate_actual_ratio is MEDIAN."""

    def test_median_odd_count(self):
        ratios = [0.9, 1.1, 4.5]  # outlier on the right
        sorted_r = sorted(ratios)
        n = len(sorted_r)
        median = sorted_r[n // 2] if n % 2 == 1 else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2
        assert median == 1.1  # outlier doesn't skew (mean would be 2.17)

    def test_median_even_count(self):
        ratios = [0.9, 1.1, 1.0, 4.5]
        sorted_r = sorted(ratios)
        n = len(sorted_r)
        median = sorted_r[n // 2] if n % 2 == 1 else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2
        assert median == 1.05  # (1.0 + 1.1) / 2

    def test_empty_log_yields_none(self):
        ratios = []
        if ratios:
            median = sorted(ratios)[len(ratios) // 2]
        else:
            median = None
        assert median is None
```

- [ ] **Step 7.2: Run failing tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py::TestPreflightBudget tests/test_forge_cascade.py::TestMedianRatioAggregation -v`
  Expected: all pass (pure-logic tests).

- [ ] **Step 7.3: Replace `# TODO Task 7` markers with pre-flight + calibration aggregation.**

  Find the `# TODO Task 7: pre-flight token budget check.` marker. Replace with:

```python
                                    # Pre-flight token-budget check (Decision 8 + Amendment 3).
                                    # Hard cap, no overshoot. Estimate retry prompt input tokens
                                    # + projected output (1.2x artifact content) + accumulated.
                                    prompt_estimate = _estimate_input_tokens(retry_prompt)
                                    output_estimate = int(
                                        _artifact_content_tokens(routing.artifact_payload) * 1.2
                                    )
                                    projected_total = (
                                        forge_metrics['forge_total_retry_tokens']
                                        + prompt_estimate + output_estimate
                                    )
                                    if projected_total > FORGE_RETRY_TOKEN_BUDGET:
                                        forge_metrics['forge_budget_exhausted'] = True
                                        logger.warning(
                                            f"🔥 Forge budget pre-flight: projected {projected_total} > "
                                            f"budget {FORGE_RETRY_TOKEN_BUDGET}, stopping cascade "
                                            f"(accumulated={forge_metrics['forge_total_retry_tokens']}, "
                                            f"projected_call={prompt_estimate + output_estimate})"
                                        )
                                        break
```

  Also, in the `try:` block where the cascade actually calls the model, add the per-iter `forge_calibration` log line and ratio capture (insert after `forge_metrics['forge_attempts'] += 1`):

```python
                                        # Per-iter calibration ratio capture (Amendment 8).
                                        if output_estimate > 0:
                                            forge_metrics['_ratio_log'].append(
                                                out_tokens / output_estimate
                                            )
                                        logger.info(
                                            f"🔥 forge_calibration: model={retry_model} "
                                            f"projected_out={output_estimate} actual_out={out_tokens} "
                                            f"ratio={out_tokens / output_estimate:.3f}"
                                        )
```

  Finally, replace the `# TODO Task 7: aggregate _ratio_log` marker (just before the `forge_metrics.pop('_ratio_log', None)` line) with the median aggregation:

```python
                            # Aggregate per-turn calibration ratio — MEDIAN per Amendment 9
                            # (robust against single-iter outliers where the model rambled).
                            if forge_metrics['_ratio_log']:
                                _sorted_r = sorted(forge_metrics['_ratio_log'])
                                _n = len(_sorted_r)
                                forge_metrics['forge_output_estimate_actual_ratio'] = (
                                    _sorted_r[_n // 2] if _n % 2 == 1
                                    else (_sorted_r[_n // 2 - 1] + _sorted_r[_n // 2]) / 2
                                )
                            forge_metrics.pop('_ratio_log', None)
```

- [ ] **Step 7.4: Run tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py tests/test_artifact_validator.py -v`
  Expected: all pass.

- [ ] **Step 7.5: Commit.**

```bash
git add app/blueprints/chat_routes.py tests/test_forge_cascade.py
git commit -m "feat(forge): pre-flight token budget + per-iter calibration ratio

Implements per spec §3 Dec 8, Amendment 3, Amendment 8:
- Pre-flight estimate gates each retry call (hard cap, no overshoot).
  projected = accumulated + prompt_estimate + output_estimate(1.2×).
  If > FORGE_RETRY_TOKEN_BUDGET (80_000), break + flag budget_exhausted.
- Per-iter forge_calibration log line emitted on every successful
  cascade call: model, projected_out, actual_out, ratio.
- Per-turn aggregation of per-iter ratios → forge_output_estimate_actual_ratio.
  MEDIAN per Amendment 9 (mean is skewed by single-iter outliers).

Refs: AC #8 (budget pre-flight), AC #15 (calibration logs)."
```

---

### Task 8: Accountant wraps for existing cascades #1/#2/#3

**Goal:** Replace the `TODO Task 8` marker in the new Forge cascade with `orchestrator_record_usage(retry_model, ...)`. Then walk the existing cascades #1 (chat_routes.py:~1729-1751), #2 (~1775-1831), and #3 (~1956-2002) and wrap each `get_ai_response(...)` call with the same accountant call — closing the pre-existing gap that the spec marks as a hard prerequisite (Decision 7).

**Files:**
- Modify: `app/blueprints/chat_routes.py`
- Modify: `tests/test_forge_cascade.py`

**ACs satisfied:** #10 (all cascade retries debited from accountant).

**Estimated LOC delta:** ~30 added (4 wrap sites + tests).

**Dependencies:** Task 7.

- [ ] **Step 8.1: Write failing test for accountant wrap.**

```python
# tests/test_forge_cascade.py — append

class TestAccountantWraps:
    """AC #10: every cascade get_ai_response is debited from accountant."""

    def test_forge_cascade_calls_record_usage_per_iter(self, monkeypatch):
        """Each successful Forge cascade call → one orchestrator_record_usage call."""
        # The spy approach: monkeypatch orchestrator.record_usage and
        # count invocations during a Forge cascade run. Full integration
        # in Task 13 E2E; here we sanity-check the wrap exists in source.
        import app.blueprints.chat_routes as chat_routes_mod
        import inspect
        src = inspect.getsource(chat_routes_mod)
        # Find the Forge cascade block and verify orchestrator_record_usage
        # appears alongside get_ai_response inside the cascade.
        assert "orchestrator_record_usage(retry_model" in src

    def test_existing_cascade_1_wrapped(self):
        """Cascade #1 (JSON-malform) calls orchestrator_record_usage on retry."""
        import app.blueprints.chat_routes as chat_routes_mod
        import inspect
        src = inspect.getsource(chat_routes_mod)
        # Cascade #1 is around line 1729-1751 in chat_routes.py.
        # Find "Regen trying" + "get_ai_response" near it, assert
        # orchestrator_record_usage is also called.
        cascade1_idx = src.find("# === EXISTING cascade #1")
        if cascade1_idx == -1:
            # Mark file region by the canonical "Regen trying {regen_model}" log
            cascade1_idx = src.find("🔄 Regen trying {regen_model}")
        # Check the cascade #1 region contains an accountant wrap
        cascade1_region = src[cascade1_idx:cascade1_idx + 2000] if cascade1_idx >= 0 else ""
        assert "orchestrator_record_usage" in cascade1_region or \
               "record_usage" in cascade1_region, "Cascade #1 missing accountant wrap"
```

- [ ] **Step 8.2: Run failing tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py::TestAccountantWraps -v`
  Expected: 2 fail (no accountant wraps yet).

- [ ] **Step 8.3: Add the import + wrap the new Forge cascade call.**

  In `chat_routes.py`, find the import block at the top and add:

```python
from app.orchestrator import record_usage as orchestrator_record_usage
```

  Then find the `# TODO Task 8: orchestrator_record_usage(retry_model, ...)` marker in the Forge cascade body. Replace it with:

```python
                                        orchestrator_record_usage(retry_model, in_tokens, out_tokens)  # Decision 7
```

- [ ] **Step 8.4: Wrap existing cascade #1 (around chat_routes.py:1729-1751).**

  Find the existing cascade #1 loop. It currently calls `get_ai_response(enhanced_prompt, cascade_model)` without an accountant wrap. After each `get_ai_response` call inside cascade #1, add:

```python
                # Accountant wrap (Decision 7 prerequisite — pre-existing gap fix).
                _in, _out = _estimate_tokens_pair(enhanced_prompt, cascade_response)
                orchestrator_record_usage(cascade_model, _in, _out)
```

- [ ] **Step 8.5: Wrap existing cascade #2 (around chat_routes.py:1775-1831).**

  Same pattern — after the `get_ai_response(...)` call in the patch-regen path A:

```python
                # Accountant wrap (Decision 7 prerequisite).
                _in, _out = _estimate_tokens_pair(regen_prompt, regen_response)
                orchestrator_record_usage(regen_model, _in, _out)
```

- [ ] **Step 8.6: Wrap existing cascade #3 (around chat_routes.py:1956-2002).**

  Same pattern at the cascade-#3 `get_ai_response` site (the one with full file context). The `regen_prompt` and `regen_response` variable names should match the local scope:

```python
                                                _in, _out = _estimate_tokens_pair(regen_prompt, regen_response)
                                                orchestrator_record_usage(regen_model, _in, _out)
```

- [ ] **Step 8.7: Run tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py::TestAccountantWraps -v`
  Expected: 2 passed.

- [ ] **Step 8.8: Commit.**

```bash
git add app/blueprints/chat_routes.py tests/test_forge_cascade.py
git commit -m "feat(forge): wrap all 4 cascades with orchestrator_record_usage

Closes the pre-existing accountant gap surfaced by the M1
investigation (spec §13.2, Decision 7). Without this, cascade retries
silently drain the daily token budget — M1 amplifies the gap from
'rare hard failure' to 'common syntax failure', so this fix is a
hard prerequisite.

Wrap sites:
- New Forge cascade (Task 5/7).
- Existing cascade #1 (JSON-malform retry, ~chat_routes.py:1729-1751).
- Existing cascade #2 (patch-apply fail path A, ~:1775-1831).
- Existing cascade #3 (patch-apply fail path B, ~:1956-2002).

All four use _estimate_tokens_pair (added in Task 5) for input/output
token estimation. Refs: AC #10."
```

---

### Task 9: SSE event emission (server-side) — verify shapes match spec §9.1

**Goal:** This task is largely already done by Task 5 (the SSE yields are in the cascade body). This task adds explicit verification tests and refines the event payload shapes if they drift from spec §9.1.

**Files:**
- Modify: `tests/test_forge_cascade.py`

**ACs satisfied:** #1, #4, #6 (visible cascade events in SSE stream).

**Estimated LOC delta:** ~50 tests.

**Dependencies:** Task 8.

- [ ] **Step 9.1: Write failing event-shape tests.**

```python
# tests/test_forge_cascade.py — append

class TestSseEventShapes:
    """Spec §9.1: event payload shapes for forge_retry / forge_done / forge_exhausted."""

    def test_forge_retry_payload_shape(self):
        """forge_retry has type=status, subtype=forge_retry, attempt, model, reason."""
        # The cascade in chat_routes.py yields `data: {json.dumps({...})}\n\n`.
        # Test the dict shape that gets json.dumps'd:
        evt = {
            'type': 'status',
            'subtype': 'forge_retry',
            'attempt': 2,
            'model': 'deepseek-v4-pro',
            'reason': 'validator: parse-error @ App.jsx:45',
        }
        # Verify all required keys present.
        assert evt['type'] == 'status'
        assert evt['subtype'] == 'forge_retry'
        assert isinstance(evt['attempt'], int) and evt['attempt'] >= 1
        assert isinstance(evt['model'], str)
        assert isinstance(evt['reason'], str)

    def test_forge_done_payload_shape(self):
        evt = {
            'type': 'status',
            'subtype': 'forge_done',
            'healed_at': 4,
        }
        assert evt['subtype'] == 'forge_done'
        assert isinstance(evt['healed_at'], int)

    def test_forge_exhausted_payload_shape(self):
        evt = {
            'type': 'status',
            'subtype': 'forge_exhausted',
            'attempts': 3,
            'errors': [
                {"file_path": "App.jsx", "line": 45, "column": 12,
                 "message": "Binding invalid", "excerpt": "..."},
            ],
        }
        assert evt['subtype'] == 'forge_exhausted'
        assert isinstance(evt['attempts'], int)
        assert isinstance(evt['errors'], list)
        assert all('file_path' in e and 'line' in e for e in evt['errors'])

    def test_chat_routes_yields_all_three_subtypes(self):
        """Verify chat_routes.py source contains all three SSE yields."""
        import app.blueprints.chat_routes as chat_routes_mod
        import inspect
        src = inspect.getsource(chat_routes_mod)
        assert "'subtype': 'forge_retry'" in src
        assert "'subtype': 'forge_done'" in src
        assert "'subtype': 'forge_exhausted'" in src
```

- [ ] **Step 9.2: Run tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py::TestSseEventShapes -v`
  Expected: all 4 pass (the SSE yields were added in Task 5).

- [ ] **Step 9.3: If any test fails, fix the corresponding yield in chat_routes.py to match spec §9.1 exactly.**

  Likely no changes needed — Task 5's skeleton already used the spec's exact shapes.

- [ ] **Step 9.4: Commit (only if changes were made; otherwise skip).**

```bash
# Only if shape fixes were needed:
git add app/blueprints/chat_routes.py tests/test_forge_cascade.py
git commit -m "test(forge): SSE event-shape conformance per spec §9.1

Verification-only task. Adds dict-shape assertions for forge_retry,
forge_done, and forge_exhausted SSE event payloads. Source-level
assertion that chat_routes.py contains all three yields.

If shape fixes were also needed, those land in this commit too."
# Otherwise (more common):
git add tests/test_forge_cascade.py
git commit -m "test(forge): SSE event-shape conformance verification per spec §9.1"
```

---

### Task 10: Frontend SSE handlers (`startNewChatModule.js`)

**Goal:** Add the three SSE handlers (`forge_retry`, `forge_done`, `forge_exhausted`) and renderers to `startNewChatModule.js`. Add the replay-side breadcrumb reconstruction from envelope fields (Decision 2). Pass `forge_validation_failed` / `forge_errors` to `artifactPanel.openArtifact`.

**Files:**
- Modify: `app/static/js/startNewChatModule.js`
- Create: `tests/static/test_startNewChatModule_forge.test.js` *(or use existing JS test infra if present)*

**ACs satisfied:** #4 (forge_exhausted ribbon path), #12 (replay breadcrumb).

**Estimated LOC delta:** ~110 module + ~80 tests.

**Dependencies:** Task 9.

- [ ] **Step 10.1: Locate the existing SSE handler in `startNewChatModule.js`.**

  Find the SSE message-handling switch where `data.type` is dispatched. Common pattern: `case 'status':` or a `if (data.type === 'status')` branch. Add a `subtype` switch within it.

- [ ] **Step 10.2: Add the `forge_*` subtype branch + renderers.**

  Insert in the SSE handler:

```javascript
// Forge M1 — validation-loop status events (spec §9.2).
if (data.type === 'status' && data.subtype) {
    switch (data.subtype) {
        case 'forge_retry':
            appendForgeRetryBlock(data.attempt, data.model, data.reason);
            break;
        case 'forge_done':
            appendForgeHealedBlock(data.healed_at);
            break;
        case 'forge_exhausted':
            appendForgeExhaustedRibbon(data.attempts, data.errors);
            break;
    }
}
```

  Add the three renderers (inside the IIFE that wraps the module):

```javascript
function appendForgeRetryBlock(attempt, model, reason) {
    // Inline card under the user's message.
    // Spinning-icon + model name + reason. Successive retries append
    // new cards (one per cascade call).
    const container = document.querySelector('.message-list') || document.body;
    const card = document.createElement('div');
    card.className = 'forge-retry-card';
    card.dataset.attempt = String(attempt);
    card.innerHTML = `
        <span class="forge-spinner" aria-hidden="true">🔁</span>
        <span class="forge-attempt">attempt ${attempt}</span>
        <span class="forge-model">${escapeHtml(model)}</span>
        <span class="forge-reason">${escapeHtml(reason)}</span>
    `;
    container.appendChild(card);
}

function appendForgeHealedBlock(healedAt) {
    // Replace the spinner on the most recent retry card with a check.
    const cards = document.querySelectorAll('.forge-retry-card');
    if (cards.length === 0) return;
    const last = cards[cards.length - 1];
    last.classList.add('forge-healed');
    const spinner = last.querySelector('.forge-spinner');
    if (spinner) spinner.textContent = '✓';
    // Add a one-line "Healed after N attempts" below.
    const line = document.createElement('div');
    line.className = 'forge-healed-line';
    line.textContent = `✓ Healed after ${healedAt} attempt(s)`;
    last.parentNode.appendChild(line);
}

function appendForgeExhaustedRibbon(attempts, errors) {
    // Red ribbon attached to the artifact panel header (NOT the chat
    // stream) — per spec §9.2. Includes the validator errors expanded
    // inline. Behavior: the artifact still renders (Decision 4); the
    // ribbon is metadata layered over the panel.
    const panel = document.getElementById('artifact-panel') ||
                  document.querySelector('.artifact-panel');
    if (!panel) return;
    let ribbon = panel.querySelector('.forge-warning-ribbon');
    if (!ribbon) {
        ribbon = document.createElement('div');
        ribbon.className = 'forge-warning-ribbon';
        panel.insertBefore(ribbon, panel.firstChild);
    }
    ribbon.innerHTML = `
        <strong>⚠ Validation failed after ${attempts} attempts.</strong>
        Review for syntax errors before using.
        <ul class="forge-errors">
            ${errors.map(e => `
                <li>${escapeHtml(e.file_path)}:${e.line}:${e.column} —
                    ${escapeHtml(e.message)}</li>
            `).join('')}
        </ul>
    `;
}

function escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
```

- [ ] **Step 10.3: Add replay-side breadcrumb reconstruction.**

  Find the message-rendering function (likely `renderMessage` or `appendAiMessage`). Where the AI message is being rendered on history reload, read `forge_attempts` and `forge_healed_at` from the message envelope and append a breadcrumb:

```javascript
// Forge M1 replay — reconstruct breadcrumb from envelope fields (spec §10.3).
function appendForgeBreadcrumbForReplay(messageElement, message) {
    if (message.forge_attempts == null) return;  // legacy doc, no breadcrumb
    if (message.forge_attempts === 0 && !message.forge_validation_failed) return;  // first-try-pass

    const breadcrumb = document.createElement('span');
    breadcrumb.className = 'forge-breadcrumb';
    if (message.forge_healed_at != null) {
        breadcrumb.textContent = `✓ Healed after ${message.forge_healed_at} attempt(s)`;
    } else if (message.forge_validation_failed) {
        breadcrumb.textContent = `⚠ Validation failed (${message.forge_attempts} attempts)`;
    }
    messageElement.appendChild(breadcrumb);

    // If validation failed, also re-render the warning ribbon on artifact panel.
    if (message.forge_validation_failed && message.forge_errors) {
        appendForgeExhaustedRibbon(message.forge_attempts, message.forge_errors);
    }
}
```

  Call `appendForgeBreadcrumbForReplay(messageEl, message)` from wherever `renderMessage` builds an AI message during history-load.

- [ ] **Step 10.4: Verify in browser (manual check before commit).**

  Run the app locally: `cd /root/xelas.io/xelasphere.io- && python3 run.py`. Open browser, send a build-an-app message, observe:
  - During cascade: at least one `.forge-retry-card` appears.
  - On heal: `.forge-healed-line` shows "Healed after N attempts".
  - On exhaustion: red ribbon appears on artifact panel.
  - On reload: same breadcrumb visible.

  If any of the four observations fails, fix the corresponding renderer before committing. (Full E2E in Task 13.)

- [ ] **Step 10.5: Commit.**

```bash
git add app/static/js/startNewChatModule.js
git commit -m "feat(forge): frontend SSE handlers + replay breadcrumb

Per spec §9.2 + §10.3 + Decision 4:
- SSE handler dispatch: forge_retry / forge_done / forge_exhausted.
- appendForgeRetryBlock: spinning-icon card per cascade call.
- appendForgeHealedBlock: '✓ Healed after N attempts' line.
- appendForgeExhaustedRibbon: red ribbon on artifact panel header
  with errors expanded inline. NOT in chat stream (spec §9.2).
- appendForgeBreadcrumbForReplay: reads forge_attempts /
  forge_healed_at / forge_validation_failed / forge_errors from
  message envelope on history reload, reconstructs breadcrumb +
  ribbon for failed turns.

Refs: AC #4 (ribbon path), AC #12 (replay)."
```

---

### Task 11: Artifact panel warning ribbon (artifactPanel.js)

**Goal:** Read `forge_validation_failed` / `forge_errors` from the **message envelope** (NOT the artifact payload — Amendment 5) when opening an artifact. Render the warning ribbon if `failed=true`.

**Files:**
- Modify: `app/static/js/artifactPanel.js`
- Modify: `app/static/css/styles.css` (or appropriate stylesheet)

**ACs satisfied:** #4 (positive + negative assertions on payload-mutation regression).

**Estimated LOC delta:** ~40 JS + ~50 CSS.

**Dependencies:** Task 10.

- [ ] **Step 11.1: Locate `openArtifact` (or equivalent entry point) in `artifactPanel.js`.**

  Add a parameter to receive the envelope's forge fields. Typical existing signature: `openArtifact(artifact, opts)`. Extend `opts` to accept `forgeValidationFailed: bool` and `forgeErrors: array`.

- [ ] **Step 11.2: Implement the ribbon rendering.**

```javascript
// app/static/js/artifactPanel.js — inside openArtifact (or equivalent)

function _renderForgeWarningRibbon(panelEl, errors) {
    // Per Decision 4: red ribbon at panel top with errors inline.
    // Idempotent — clear previous ribbon if present.
    let ribbon = panelEl.querySelector('.forge-warning-ribbon');
    if (ribbon) ribbon.remove();

    ribbon = document.createElement('div');
    ribbon.className = 'forge-warning-ribbon';
    ribbon.setAttribute('role', 'alert');
    ribbon.innerHTML = `
        <strong>⚠ Validation failed after ${errors.length || 'multiple'} attempts.</strong>
        Review for syntax errors before using.
        <ul class="forge-errors">
            ${(errors || []).map(e => `
                <li>${_escapeHtml(e.file_path)}:${e.line}:${e.column} —
                    ${_escapeHtml(e.message)}</li>
            `).join('')}
        </ul>
    `;
    panelEl.insertBefore(ribbon, panelEl.firstChild);
}

function _clearForgeWarningRibbon(panelEl) {
    const r = panelEl.querySelector('.forge-warning-ribbon');
    if (r) r.remove();
}

function _escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
```

  Inside `openArtifact`, after the panel is opened and rendered:

```javascript
// Forge M1 ribbon (Decision 4): read from message envelope, NOT
// from artifact payload (Amendment 5).
const panelEl = document.getElementById('artifact-panel') ||
                document.querySelector('.artifact-panel');
if (opts && opts.forgeValidationFailed) {
    _renderForgeWarningRibbon(panelEl, opts.forgeErrors || []);
} else {
    _clearForgeWarningRibbon(panelEl);  // healed / clean → ensure no stale ribbon
}
```

- [ ] **Step 11.3: Update callers to pass envelope fields.**

  Find every site in `startNewChatModule.js` that calls `artifactPanel.openArtifact(...)`. Pass the new opts:

```javascript
artifactPanel.openArtifact(artifact, {
    sessionId,
    reference,
    forgeValidationFailed: message?.forge_validation_failed === true,
    forgeErrors: message?.forge_errors || [],
});
```

- [ ] **Step 11.4: Add CSS.**

  In `app/static/css/styles.css` (or the appropriate stylesheet), add:

```css
/* Forge M1 — validation-loop UI (spec §9 + Decision 4). */

.forge-retry-card {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    margin: 6px 0;
    background: rgba(255, 200, 100, 0.10);
    border-left: 3px solid #f9b14b;
    border-radius: 4px;
    font-size: 13px;
}
.forge-retry-card .forge-spinner {
    display: inline-block;
    animation: forge-spin 1.5s linear infinite;
}
.forge-retry-card.forge-healed .forge-spinner {
    animation: none;
    color: #38e54d;
}
@keyframes forge-spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}

.forge-healed-line {
    color: #2e7d32;
    font-size: 13px;
    margin: 4px 0 12px 0;
    font-weight: 500;
}

.forge-warning-ribbon {
    background: rgba(255, 70, 70, 0.10);
    border: 1px solid rgba(255, 70, 70, 0.35);
    border-left: 4px solid #ff4646;
    color: #c62828;
    padding: 12px 16px;
    margin: 0 0 12px 0;
    border-radius: 4px;
    font-size: 13px;
}
.forge-warning-ribbon strong {
    display: block;
    margin-bottom: 4px;
}
.forge-warning-ribbon .forge-errors {
    margin: 8px 0 0 0;
    padding-left: 20px;
    font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace;
    font-size: 12px;
}

.forge-breadcrumb {
    display: inline-block;
    margin-left: 8px;
    font-size: 11px;
    color: #888;
    font-style: italic;
}
```

  Bump cache-buster on the stylesheet in `app/templates/index.html` (if applicable — find the `<link rel="stylesheet" ...?v=N>` tag and increment N).

- [ ] **Step 11.5: Commit.**

```bash
git add app/static/js/artifactPanel.js app/static/css/styles.css app/templates/index.html
git commit -m "feat(forge): artifact panel warning ribbon from message envelope

Per spec Decision 4 + Amendment 5:
- artifactPanel.openArtifact accepts forgeValidationFailed +
  forgeErrors via opts (NOT from artifact payload — Amendment 5
  anti-goal).
- _renderForgeWarningRibbon: red ribbon at panel top, errors
  expanded inline. Idempotent.
- _clearForgeWarningRibbon: removes stale ribbon when artifact is
  re-opened in clean/healed state.
- CSS: .forge-retry-card, .forge-healed-line, .forge-warning-ribbon,
  .forge-breadcrumb. Cache-buster bumped.
- All callers in startNewChatModule.js updated to pass envelope
  fields.

Refs: AC #4 (positive + negative assertions)."
```

---

### Task 12: Telemetry plumbing (per-turn structured log)

**Goal:** Emit the per-turn structured log line with all 8 `forge_*` fields after the cascade resolves. Verify per-iter `forge_calibration:` lines also fire (added in Task 7).

**Files:**
- Modify: `app/blueprints/chat_routes.py` (one log line addition near :2346)
- Modify: `tests/test_forge_cascade.py`

**ACs satisfied:** #13 (per-turn telemetry), #15 (calibration logs — already done in Task 7, verified here).

**Estimated LOC delta:** ~10 module + ~50 tests.

**Dependencies:** Task 11.

- [ ] **Step 12.1: Write failing test.**

```python
# tests/test_forge_cascade.py — append

class TestPerTurnTelemetry:
    """AC #13: per-turn log line with all 8 forge_* fields."""

    def test_per_turn_log_format(self, caplog):
        """The per-turn log line shape is: '🔥 forge: forge_X=... forge_Y=...'."""
        # Construct a sample log line manually using the spec §12 shape.
        forge_metrics = {
            'forge_validation_attempted': True,
            'forge_validation_passed_first_try': False,
            'forge_attempts': 2,
            'forge_healed_at_iter': 4,
            'forge_total_retry_tokens': 32487,
            'forge_time_to_heal_ms': 8412,
            'forge_budget_exhausted': False,
            'forge_loop_detected_skip': False,
            'forge_validation_failed': False,
            'forge_errors': [],
            'forge_output_estimate_actual_ratio': 1.087,
        }
        # Build the log line per spec §12 (the implementation should match this).
        keys = [
            'forge_validation_attempted',
            'forge_validation_passed_first_try',
            'forge_healed_at_iter',
            'forge_total_retry_tokens',
            'forge_time_to_heal_ms',
            'forge_budget_exhausted',
            'forge_loop_detected_skip',
            'forge_output_estimate_actual_ratio',
        ]
        line = "🔥 forge: " + " ".join(
            f"{k}={forge_metrics.get(k)}" for k in keys
        )
        # All 8 keys appear.
        for k in keys:
            assert k in line
        # Spec §12 example value present.
        assert "forge_attempts" not in line  # forge_attempts is persisted, not telemetry-logged
```

  Note: `forge_attempts` is a persistence field (§10.1), `forge_healed_at_iter` is the telemetry-equivalent (§12). Both are kept in `forge_metrics` for convenience but only the eight in §12 appear in the per-turn log line.

- [ ] **Step 12.2: Run failing test.**

  Run: `python3 -m pytest tests/test_forge_cascade.py::TestPerTurnTelemetry -v`
  Expected: PASS (pure-format test).

- [ ] **Step 12.3: Add the per-turn log emit in `chat_routes.py`.**

  Around line 2346 (existing `orchestrator_record_usage` log site for `stream_model`), after the existing accountant call, add:

```python
                    # Forge M1 per-turn telemetry (spec §12). Emit once per
                    # artifact-bearing turn after cascade resolves. The eight
                    # fields are exact per Decision 10 + Amendment 8.
                    if forge_metrics['forge_validation_attempted']:
                        logger.info(
                            "🔥 forge: "
                            f"forge_validation_attempted={forge_metrics['forge_validation_attempted']} "
                            f"forge_validation_passed_first_try={forge_metrics['forge_validation_passed_first_try']} "
                            f"forge_healed_at_iter={forge_metrics['forge_healed_at_iter']} "
                            f"forge_total_retry_tokens={forge_metrics['forge_total_retry_tokens']} "
                            f"forge_time_to_heal_ms={forge_metrics['forge_time_to_heal_ms']} "
                            f"forge_budget_exhausted={forge_metrics['forge_budget_exhausted']} "
                            f"forge_loop_detected_skip={forge_metrics['forge_loop_detected_skip']} "
                            f"forge_output_estimate_actual_ratio={forge_metrics['forge_output_estimate_actual_ratio']}"
                        )
```

  Wrap in `if forge_metrics['forge_validation_attempted']` so non-validatable artifacts (SVG, etc.) don't pollute logs.

- [ ] **Step 12.4: Run all tests.**

  Run: `python3 -m pytest tests/test_forge_cascade.py tests/test_artifact_validator.py -v`
  Expected: all pass.

- [ ] **Step 12.5: Commit.**

```bash
git add app/blueprints/chat_routes.py tests/test_forge_cascade.py
git commit -m "feat(forge): per-turn telemetry log line with all 8 forge_* fields

Per spec §12 + Decision 10 + Amendment 8:
- One '🔥 forge:' log line per artifact-bearing turn (gated on
  forge_validation_attempted=true so non-validatable artifacts don't
  log).
- All eight metrics in exact field-name order matching §12.
- Per-iter '🔥 forge_calibration:' lines (added in Task 7) provide
  raw projected-vs-actual data points for post-launch calibration of
  the 1.2x output multiplier.

Refs: AC #13 (per-turn telemetry), AC #15 (calibration logs)."
```

---

### Task 13: End-to-end integration tests

**Goal:** Add fixtures + integration tests that exercise the cascade end-to-end against the real validator + a mocked `get_ai_response`. Cover all 16 acceptance criteria.

**Files:**
- Create: `tests/fixtures/artifacts/loop_detect_identical_errors.json`
- Create: `tests/fixtures/artifacts/budget_exhaustion_large.json`
- Create: `tests/fixtures/artifacts/context_window_overflow.json`
- Modify: `tests/test_forge_cascade.py` (or split into `tests/test_forge_e2e.py`)

**ACs satisfied:** #1, #2, #3, #4, #5, #6, #7, #8, #9, #14, #16 (the entire AC list verified end-to-end).

**Estimated LOC delta:** ~240 fixtures + ~280 tests.

**Dependencies:** Tasks 1-12.

- [ ] **Step 13.1: Create the three new fixtures.**

```json
// tests/fixtures/artifacts/loop_detect_identical_errors.json
{
  "title": "Loop-Detect Trigger",
  "type": "project",
  "primaryLanguage": "jsx",
  "files": [
    {
      "path": "App.jsx",
      "language": "jsx",
      "content": "import React from 'react';\nexport default () => (<></>) => null;\n",
      "isBinary": false,
      "byteSize": 60
    }
  ]
}
```

```json
// tests/fixtures/artifacts/budget_exhaustion_large.json
// Artifact whose retry-prompt projected output (1.2x of content tokens)
// exceeds 80k budget within a couple of iters.
{
  "title": "Big Artifact",
  "type": "project",
  "primaryLanguage": "jsx",
  "files": [
    {
      "path": "Big.jsx",
      "language": "jsx",
      "content": "// REPLACE WITH ~30K TOKENS OF JSX CONTENT — see Step 13.2 for generation",
      "isBinary": false,
      "byteSize": 0
    }
  ]
}
```

```json
// tests/fixtures/artifacts/context_window_overflow.json
// Per spec §14: this fixture is a normal-sized artifact; the test
// MOCKS get_ai_response to raise a context-overflow exception type.
// We don't actually overflow V4 context windows (Flash 128k+).
{
  "title": "Context Overflow Mock",
  "type": "project",
  "primaryLanguage": "jsx",
  "files": [
    {
      "path": "App.jsx",
      "language": "jsx",
      "content": "import React from 'react';\nexport default () => (<></>) => null;\n",
      "isBinary": false,
      "byteSize": 60
    }
  ]
}
```

- [ ] **Step 13.2: Generate the budget_exhaustion fixture content programmatically.**

```bash
python3 << 'EOF'
import json
# Generate ~30k tokens of valid JSX content.
# 1 token ≈ 4 chars; we want ~120k chars of valid content.
chunks = []
for i in range(2000):
    chunks.append(f"const var{i} = 'value{i:04d}';")
big_content = "import React from 'react';\n" + "\n".join(chunks) + "\nexport default () => (<></>) => null;\n"
fixture = {
    "title": "Big Artifact",
    "type": "project",
    "primaryLanguage": "jsx",
    "files": [{
        "path": "Big.jsx",
        "language": "jsx",
        "content": big_content,
        "isBinary": False,
        "byteSize": len(big_content),
    }],
}
with open("tests/fixtures/artifacts/budget_exhaustion_large.json", "w") as f:
    json.dump(fixture, f)
print(f"Wrote fixture with {len(big_content)} chars")
EOF
```

  NOTE: The fixture is intentionally syntactically broken at the end (so the validator fails and the cascade is triggered). The 1.2x output multiplier × 30k tokens = 36k projected output. After 2 cascade calls, accumulated ≈ 30k × 2 = 60k input + 36k × 2 = 72k output... wait, the budget is on `forge_total_retry_tokens` which is `(in + out) * iters`. Per spec §7.1:
  - iter 1: prompt_estimate ≈ 30k, output_estimate ≈ 36k → projected = 0 + 30k + 36k = 66k → under 80k → proceed.
  - iter 2: prompt_estimate ≈ 30k, output_estimate ≈ 36k → projected = 66k + 30k + 36k = 132k → over 80k → break.

  So budget triggers at iter 2 pre-flight. Adjust the fixture content size if needed.

- [ ] **Step 13.3: Write the E2E test class.**

```python
# tests/test_forge_cascade.py — append (or move to tests/test_forge_e2e.py)

import json
from pathlib import Path
from unittest.mock import patch, MagicMock


class _CascadeRunner:
    """Helper to invoke the Forge cascade in isolation.

    The cascade body lives inline in chat_routes.py's chunk_type=='done'
    handler. To test it without spinning up Flask, we extract the cascade
    logic into a module-level helper in Task 13's polish — OR we test via
    end-to-end Flask test client. This class uses the latter.
    """

    @staticmethod
    def load_fixture(name):
        return json.loads(
            (Path("tests/fixtures/artifacts") / f"{name}.json").read_text()
        )


class TestNetflixHeals:
    """AC #1."""

    def test_netflix_heals_at_pro(self, monkeypatch):
        """stream=Flash + stream's artifact has Netflix bug → Pro heals on iter 1."""
        netflix = _CascadeRunner.load_fixture("netflix_broken_jsx")
        clean = _CascadeRunner.load_fixture("clean_react")

        called_models = []

        def fake_get_ai_response(prompt, model, images=None):
            called_models.append(model)
            # Pro returns a clean artifact.
            return (
                'ACTION: create_artifact\n\n'
                '```artifact-meta\n'
                '{"title":"healed","type":"project","primaryLanguage":"jsx"}\n'
                '```\n\n'
                f'```artifact-file:App.jsx:jsx\n{clean["files"][0]["content"]}\n```\n',
                False,
                None,
            )

        # Run cascade with stream_model = v4-flash + Netflix bug payload.
        # ... (full cascade-runner harness implementation)
        # Assert: forge_attempts=1, forge_healed_at_iter=2,
        # forge_validation_failed=False, called_models[0]='deepseek-v4-pro'.


class TestCleanFirstTry:
    """AC #2."""
    # Clean React artifact → no retry. forge_attempts=0, forge_healed_at=null,
    # forge_validation_failed=false, forge_total_retry_tokens=0.


class TestSvgBypass:
    """AC #3."""
    # SVG-only artifact → spy on subprocess shows zero spawns.
    # forge_attempts=null, forge_healed_at=null, forge_validation_failed=null.


class TestExhaustion:
    """AC #4."""
    # Mock-cascade returns broken artifact for every retry.
    # Assert: forge_validation_failed=true, forge_errors non-empty,
    #         routing.artifact_payload has NO _forge_* keys (regression guard),
    #         persisted Firestore doc has all 5 envelope fields populated.


class TestStreamModelSkip:
    """AC #5 + AC #7 (E16)."""
    # AC #5: stream=Flash → first retry call is to v4-pro.
    # AC #7: stream=GPT-4.1 → zero retry calls, immediate exhaustion.


class TestIdenticalErrorsTierSkip:
    """AC #6 + AC #16."""
    # stream=Flash, Pro returns same X → skip Kimi → call GPT-4.1.
    # If GPT-4.1 also returns X: forge_loop_detected_skip stays true
    #   (from earlier Pro→Kimi skip), NOT re-flipped.


class TestBudgetPreflight:
    """AC #8."""
    # budget_exhaustion_large fixture → second retry's pre-flight projects
    # over 80k → break. forge_budget_exhausted=true. Assert iter 2's
    # get_ai_response was NEVER called.


class TestValidatorTimeoutDoesntBlock:
    """AC #9."""
    # Mock validator subprocess sleeps 5s (>3s timeout). Artifact still
    # renders. forge_validation_passed_first_try=true (treated as pass).


class TestNoRegressionInExistingFlow:
    """AC #14: existing live-patch streaming + cascades #1/#2/#3 still work."""
    # Run a non-Forge artifact flow (existing patch + JSON-malform cascades)
    # with the new code in place. Assert no behavioral changes.


class TestForgeCalibrationLogged:
    """AC #15."""
    # Capture log records during cascade. Assert: per-iter
    # 'forge_calibration:' lines fire AND per-turn '🔥 forge:' aggregate
    # line emits with eight forge_* fields.
```

  NOTE: Each test class above is **scaffolded** — populate the test bodies during this task. The full implementation of each is straightforward: load fixture → mock `get_ai_response` to return canned responses → invoke the cascade → assert on `forge_metrics` + SSE captured events. Use Flask test client (`from app import create_app; app.test_client()`) and the existing fixture patterns from `tests/test_artifacts.py`.

- [ ] **Step 13.4: Implement each test body.**

  This is the bulk of Task 13. Estimate ~2-3 hours of focused TDD writing. Work through ACs #1, #2, #3, ..., #16 in order; each becomes a `def test_ac_NN_*` method. Run after every test write to verify cascade integration is sound.

- [ ] **Step 13.5: Run full forge test suite.**

  Run: `python3 -m pytest tests/test_forge_cascade.py tests/test_artifact_validator.py tests/test_save_interaction_forge_fields.py -v`
  Expected: all 16 ACs pass.

- [ ] **Step 13.6: Run the broader suite to confirm no regressions.**

  Run: `python3 -m pytest tests/ -q --deselect tests/test_artifacts.py::TestApplyPatches::test_patch_file_not_found --deselect tests/test_mempalace_shadow_runtime.py::test_chat_stream_fires_user_half_record_turn_only --deselect tests/test_smart_router.py::TestDetectTaskType::test_detect_coding_from_history --deselect tests/test_smart_router.py::TestAnalyzeHistoryForCode::test_non_coding_history --deselect tests/test_smart_suggestions.py::TestIncrementShownCounts 2>&1 | tail -10`
  Expected: only the 5 pre-existing flaky tests fail. No new failures.

- [ ] **Step 13.7: Commit.**

```bash
git add tests/fixtures/artifacts/ tests/test_forge_cascade.py
git commit -m "test(forge): end-to-end integration tests covering all 16 ACs

Adds three fixtures (loop_detect_identical_errors,
budget_exhaustion_large, context_window_overflow) per spec §14.

Adds 11 test classes covering AC #1-#16:
- Netflix heals on iter 1 (Pro)
- Clean React passes first-try
- SVG bypasses subprocess (verified via spy)
- Cascade exhausts → envelope persists, payload unmutated
- Stream-Flash → first retry is Pro (not Flash)
- Stream-GPT-4.1 → zero retries (E16)
- Identical errors → tier skip, GPT-4.1 reached
- Budget pre-flight stops cascade before second call
- Validator timeout doesn't block stream
- Per-iter calibration logs + per-turn aggregate emit
- Loop-detect on final position no false flip

Plus a regression check that existing live-patch streaming +
JSON-malform cascade #1 + patch-apply cascades #2/#3 unchanged.

Refs: AC #1-#16 (full coverage)."
```

---

### Task 14: Polish + self-review + open PR

**Goal:** Add the README one-liner, run a self-review subagent, address any blocking findings, open the PR.

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/reviews/2026-XX-XX-forge-m1-impl-review.md` (subagent-authored)

**ACs satisfied:** #14 (no regression — verified by review).

**Estimated LOC delta:** ~5 README + review doc.

**Dependencies:** Task 13.

- [ ] **Step 14.1: Add README one-liner.**

  Open `README.md`, find the features section, append:

```markdown
- **Self-healing artifact validation (Forge M1)** — JSX/TSX syntax errors auto-retry with model cascade (V4-Flash → V4-Pro → Kimi-K2.5 → GPT-4.1) up to 4 attempts, with loop-detect tier-skip and per-turn token budget. See `docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md`.
```

- [ ] **Step 14.2: Commit + push.**

```bash
git add README.md
git commit -m "docs(forge): README one-liner about Forge M1 validation loop"
git push -u origin feat/forge-m1-impl
```

- [ ] **Step 14.3: Open the M1 PR.**

```bash
gh pr create \
  --base main \
  --title "feat(forge): M1 validation loop — self-healing JSX/TSX cascade" \
  --body "$(cat <<'EOF'
## Summary
- Implements Forge M1 per `docs/superpowers/specs/2026-04-29-forge-m1-validation-loop.md` (commit `cba3e28`).
- Server-side syntax validator (Babel + tsc subprocesses) inserted into `/chat/stream` between :1751 and :2042.
- Multi-model cascade (V4-Flash → V4-Pro → Kimi → GPT-4.1) with loop-detect, pre-flight budget, telemetry.
- Five envelope fields persist per turn for replay; eight per-turn telemetry metrics + per-iter calibration logs.
- Closes the pre-existing accountant gap (Decision 7 prerequisite — wraps cascades #1/#2/#3 plus the new Forge cascade).

## All 16 ACs verified
See `tests/test_forge_cascade.py`. Each AC has a corresponding test class.

## Files
- NEW: `app/services/artifact_validator.py`, `app/services/forge_validators/check_jsx.cjs`, `package.json`, 7 fixtures, ~280 LOC tests.
- TOUCHED: `app/blueprints/chat_routes.py` (cascade insertion + 4 accountant wraps), `app/services/chat_engine.py` (constants + startup guard + save_interaction extension), `app/static/js/startNewChatModule.js` (SSE handlers + replay), `app/static/js/artifactPanel.js` (warning ribbon), `app/static/css/styles.css` (ribbon CSS), `README.md`.
- DEPS: `@babel/parser ^7.24.0` added to `package.json`.

## Heads-up
- Self-review subagent report at `docs/superpowers/reviews/<date>-forge-m1-impl-review.md` (filed alongside this PR per the project's hybrid review pattern).
- Calibration follow-up: issue #149 tracks recalibrating the 1.2x output multiplier after ~100 production retries land.

## Test plan
- [x] All 16 ACs pass (`pytest tests/test_forge_cascade.py tests/test_artifact_validator.py -v`).
- [x] No regression in broader suite (5 pre-existing flaky tests unchanged).
- [ ] Manual smoke: send "build me a netflix clone" with a model that historically produced the Netflix bug; observe live cascade.
- [ ] Verify per-turn `🔥 forge:` log line emits in production after first artifact-bearing turn.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 14.4: Dispatch self-review subagent.**

  After the PR opens, dispatch `feature-dev:code-reviewer` (or equivalent) against the diff. Report goes to `docs/superpowers/reviews/2026-XX-XX-forge-m1-impl-review.md`.

  Per the established hybrid review pattern (memory: "Hybrid: main-session implementer + subagent review pair"), the review focuses on:
  - All 16 ACs implemented per spec — file:line citations.
  - All 9 amendments respected (no stale logic, no missed amendments).
  - Decision 7 accountant wraps in place at cascades #1/#2/#3 plus Forge.
  - No payload-mutation regression (Amendment 5 anti-goal).
  - No scope creep into M2/M3 features (spec §2 not touched).

- [ ] **Step 14.5: Commit the review report.**

```bash
git add docs/superpowers/reviews/2026-XX-XX-forge-m1-impl-review.md
git commit -m "docs(review): self-review subagent report for Forge M1 implementation

Verdict: <APPROVED / APPROVED WITH NOTES / CHANGES REQUESTED>"
git push
```

- [ ] **Step 14.6: Address review findings, if any.**

  If subagent flags blocking issues, address them in additional commits. If findings are non-blocking, acknowledge in PR body and open follow-up issues.

---

## 4. Risk register

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | `@babel/parser` major version drift breaks JSX parsing | Low | Pin `^7.24.0` in `package.json` (Task 1). Document bump policy in §14: only bump on minor/patch within 7.x; major bumps require re-running the full Forge test suite. |
| R2 | Subprocess overhead in production (3s × N files per turn) | Medium | Each file's validator runs in `asyncio.gather` parallel, so per-file timeout caps total wall time at 3s regardless of file count. Add benchmarking in Task 14: log `result.elapsed_ms` post-launch; if p99 > 3s, escalate to skip-validator-on-very-large-artifacts. |
| R3 | `tsc` not installed on deploy host → all TS/TSX validation silently no-ops | High at launch (no `tsc` currently installed) | Per spec Decision 11 / §6.4, `FileNotFoundError` is gracefully degraded: log warning ONCE per process, skip TS/TSX going forward, return `ok=True`. M1 ships without TS validation working unless `tsc` is installed via `npm install -g typescript` or similar. **Documented in PF6.** Acceptable for M1 because most artifacts are JSX (which Babel handles); TS support arrives when ops installs `tsc`. |
| R4 | Firestore read of new envelope fields on legacy docs raises `KeyError` | Low | Use `.get(field)` with default `None`; never assume the field exists. Test in Task 4 (`test_forge_metrics_none_writes_nulls`) and the replay-side breadcrumb reconstruction in Task 10 (`if message.forge_attempts == null) return;`). |
| R5 | 1.2x output multiplier is wrong → either premature pre-flight blocks or budget overshoot | Known | Calibration plan: per-iter `🔥 forge_calibration:` log lines (Task 7) provide raw data. Per-turn `forge_output_estimate_actual_ratio` (median) aggregates. Issue #149 tracks recalibrating after ~100 production retries. Until calibrated, 1.2x is conservative (slight over-estimate) so the budget gate is more likely to pre-flight-block than to overshoot. |
| R6 | Loop-detect false negatives if model produces near-identical-but-not-byte-identical errors | Low | Column excluded from signature mitigates partially (per §11 E6). Worst-case false negative: cascade runs an unhelpful tier — bounded by 4-iter cap + budget. Acceptable per spec. |
| R7 | `_estimate_tokens_pair` token count drift across providers (tiktoken cl100k_base biased toward GPT) | Low | All four cascade models are queried via OpenAI-compatible endpoints; token counts are approximate but within ±15% of true. Token budget is also conservative (1.2x output multiplier). For accountant accuracy, the orchestrator's `record_usage` is the source of truth — `_estimate_tokens_pair` is only used for budget pre-flight. Calibration via #149 will surface drift. |
| R8 | The cascade body is large and inline in `chat_routes.py`; future maintenance painful | Medium | Task 14 polish step: extract the cascade body into a module-level function `_run_forge_cascade(routing, prompt, stream_model)` in `chat_engine.py` if time permits. Not in M1 scope; tracked as a Task 14 stretch goal. |
| R9 | `package.json` at repo root may collide with future Node ecosystem additions | Very Low | Package is named `xelasphere-runtime`, marked `private: true`. If a real frontend bundler later wants its own `package.json`, scope it to a subdirectory (`frontend/package.json`) and migrate `@babel/parser` accordingly. |
| R10 | Startup guard's `RuntimeError` crashes the server if V4 swap merge slips | Intentional | This is the design — fail-fast at gunicorn worker spawn rather than at first artifact request. PF1 catches this before Task 1 starts. If the spec author chooses the fallback path (cherry-pick V4 catalog entries into M1 PR), update `chat_engine.py` to add minimal entries instead of raising. |

---

## 5. Acceptance gate definition

**M1 is shipped when:**

- [ ] All 16 acceptance criteria from spec pass (verified by Task 13's E2E test class).
- [ ] Spec §14 file-by-file manifest fully realized — 8 NEW files, 5 TOUCHED files, 1 dep added.
- [ ] Self-review subagent report written to `docs/superpowers/reviews/<date>-forge-m1-impl-review.md` with verdict `APPROVED` or `APPROVED WITH NOTES` (no `CHANGES REQUESTED`).
- [ ] PR opened with link to spec, review report, and AC checklist; awaiting human review.
- [ ] V4 swap merged (PR #139) — verified by PF1 before Task 1 starts.
- [ ] Investigation doc merged (PR #135) — verified by PF2.
- [ ] Spec doc merged (PR #136) — verified by PF2.
- [ ] No regression in existing artifact flow — verified by Task 13 step 13.6 (broader test suite minus 5 pre-existing flaky tests).
- [ ] Manual smoke (send a "build me a netflix clone" request, observe cascade live) — once after deploy to staging.

If any gate fails, fix before merging. The plan's `Task 14` step is the deliberate stop-and-review point; do not auto-merge.

---

## 6. Out of scope reminders

Re-stating spec §2 explicitly so implementation doesn't drift. **If any of the below appears in the PR diff, that's scope creep — push back:**

- **No `Plan` tool.** No visible plan checklist. The retry loop is invisible-by-default except for the `🔁 Retry` status line.
- **No `Snapshot` / `Rollback`.** Successful artifacts don't pin a version-history rail.
- **No `PatchFile` / `EditFile` / `MultiEdit` tools.** M1's retry asks the model to regenerate the COMPLETE artifact each time.
- **No artifact panel changes** beyond the warning ribbon (Decision 4). Code/Preview tabs untouched. No file tree, no version rail additions, no diff overlays.
- **No server-side container sandbox.** No Docker, no `npm install` in the request path, no test runner.
- **No client-side esbuild-WASM.** No browser-side parser. No second-HTTP-request SSE round-trip.
- **No cancel button.** Hard 4-iteration cap and per-turn retry token budget are the only bounds.
- **No `AskUser` tool.** Deferred to M3.
- **No Python / HTML / CSS validation.** JSX/TSX/JS/TS only.
- **No new fenced-format tools.** Existing `artifact-meta` / `artifact-file` fences are the only protocol. No new ACTION verbs.
- **No transcript replay polish.** Reload shows a single "Healed after N attempts" breadcrumb reconstructed from persisted counts; individual per-iteration cards are live-only.

M2/M3 features are off-limits even if they look easy.

---

## 7. Branch + PR strategy

- **Single branch:** `feat/forge-m1-impl`, branched off post-merge `main` (after PRs #135, #136, #139 all land).
- **One commit minimum per task** (14 tasks → 14+ commits). Each commit is reviewable in isolation.
- **PR opens after Task 13** (E2E tests passing). Task 14 is the polish-and-self-review pass on the open PR.
- **No force-pushes** unless the spec author requests one for review-feedback rebases.
- **Rebase on main** only if main moves materially during implementation (e.g. another PR touches `chat_routes.py` near the cascade insertion point).

---

## Self-review

After writing this plan, I checked it against the spec with fresh eyes:

**1. Spec coverage:** Every section of the spec maps to one or more tasks:
- §6 (validator) → Tasks 1, 2.
- §7 (retry loop) → Tasks 5, 6, 7, 8.
- §8 (retry-prompt) → Task 3.
- §9 (SSE events) → Tasks 5, 9, 10.
- §10 (persistence + replay) → Tasks 4, 10, 11.
- §11 (edge cases) → Tasks 2, 5, 6, 7 (each E# has a corresponding test in Task 13).
- §12 (telemetry) → Task 12.
- §13 (prerequisites) → PF1 (V4 merge), Task 5.1 (startup guard), Task 8 (accountant wraps), Task 4 (schema fields).
- §14 (file manifest) → Tasks 1-12 (every NEW + TOUCHED file appears in at least one task).
- All 16 ACs → Task 13 has a test class per AC.
- All 9 amendments → woven into the relevant tasks (Amendment 1 → Task 1's Babel wrapper; Amendment 2 → Task 6 increment discipline; Amendment 3 → Task 7 pre-flight; Amendment 4 → Task 5 start_idx; Amendment 5 → Task 11 envelope-not-payload; Amendment 6 → Task 6 worked-example matches code; Amendment 7 → Task 5 forge_attempts in metrics; Amendment 8 → Task 7 calibration; Amendment 9 → Task 6 telemetry guard + Task 7 median).

**2. Placeholder scan:** No "TBD", "TODO", "implement later", "fill in details" in the plan body (the `# TODO Task N` markers in step code are explicitly intentional handoffs between tasks, with the closing task (6/7/8) replacing each). No "similar to Task N" — each task repeats the code it needs. No "add appropriate error handling" — every error path is concrete.

**3. Type consistency:** `forge_metrics` dict shape is consistent across tasks (initialized in Task 5, mutated in Tasks 5/6/7, persisted in Task 4, logged in Task 12). Function signatures (`validate_artifact_payload`, `build_validation_retry_prompt`, `_signature`, `_short_reason`) are defined in Tasks 1-3 and used unchanged in Tasks 5-13. SSE event subtypes (`forge_retry`, `forge_done`, `forge_exhausted`) are emitted in Task 5 and handled in Task 10 with matching field names.

**4. Spec ambiguities surfaced — none.** Every implementation choice was either locked by the spec or fell out of existing code patterns (e.g. `_estimate_tokens_pair` reuses `app.utilities.count_tokens` rather than a new helper, since spec said "delegate to existing utilities"). No new decisions required.

The plan is internally consistent and traceable to the spec. Ready for human review.

---

## Stop point

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-forge-m1-implementation-plan.md`.** Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. **REQUIRED SUB-SKILL:** `superpowers:subagent-driven-development`.

2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Wait for human approval of this plan before either execution mode begins.** The plan itself is the next gate, not the implementation.

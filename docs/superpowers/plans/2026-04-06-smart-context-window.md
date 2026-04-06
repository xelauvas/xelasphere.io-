# Smart Context Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed 20-message cap + full artifact dump with a smart context assembler, artifact state tracker, and ACTION-based decision protocol.

**Architecture:** Three new modules (`artifact_state_tracker.py`, `context_builder.py`, `action_parser.py`) that replace the current prompt assembly in `chat_routes.py` and decision logic in `artifacts.py`. The existing `ArtifactStore`, Firestore schema, and Memory V2/V3 stay unchanged. New modules are integrated at the same call sites where `build_prompt()`, `detect_artifact_update_intent()`, `parse_structured_response()`, and `ArtifactRouter.route()` are currently invoked.

**Tech Stack:** Python 3.11, Flask, Google Cloud Firestore, hashlib (SHA-256), existing test infrastructure (pytest)

**Design Spec:** `docs/SMART_CONTEXT_WINDOW_SPEC.md`

**API constraint:** The entire API layer is string-based. `send_prompt(prompt, model)`, `get_ai_response(full_prompt, model)`, and `stream_deepseek_response(prompt, model)` all accept a single string prompt. Internally, `_execute_prompt_request()` wraps this string in `[{"role": "system", ...}, {"role": "user", "content": prompt}]`. The `ContextBuilder` must therefore output a **flat assembled string** (like the existing `build_prompt()` returns), not a messages list. The `BuiltContext.messages` field exists for future messages-API migration but is not used in the current integration.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/context/artifact_state_tracker.py` | Per-file SHA-256 hashing, dirty-file detection, version-transition diffing, LRU cache, Firestore persistence |
| Create | `app/context/context_builder.py` | 4-zone prompt assembly, 3-tier history, microcompact, token estimation, metadata extraction |
| Create | `app/context/action_parser.py` | ACTION: line protocol parsing, streaming buffer, fallback heuristics |
| Create | `app/context/__init__.py` | Package init, public API exports |
| Create | `tests/test_artifact_state_tracker.py` | Unit tests for state tracker |
| Create | `tests/test_context_builder.py` | Unit tests for context builder |
| Create | `tests/test_action_parser.py` | Unit tests for action parser |
| Modify | `app/services/chat_engine.py:152-664` | Replace META/CHAT/ARTIFACT instructions with ACTION format rules |
| Modify | `app/services/chat_engine.py:1402-1490` | Add `turn_metadata` extraction to `save_interaction()` |
| Modify | `app/blueprints/chat_routes.py:394-977` | Integrate State Tracker + Context Builder + ActionParser into `/chat_stream` |
| Modify | `app/blueprints/chat_routes.py:980-2020` | Integrate State Tracker + Context Builder + ActionParser into `/chat` |
| Modify | `app/artifacts.py:699-790` | Add State Tracker `mark_mutated()` call after `apply_patches()` |
| Modify | `app/artifacts.py:1822-1858` | Add State Tracker `sync_from_firestore()` call after `_append_version()` |

---

## Task 1: Artifact State Tracker — Core Data Model

**Files:**
- Create: `app/context/__init__.py`
- Create: `app/context/artifact_state_tracker.py`
- Test: `tests/test_artifact_state_tracker.py`

- [ ] **Step 1: Create package init**

```python
# app/context/__init__.py
"""Smart Context Window — context management for XelaSphere chat."""
```

- [ ] **Step 2: Write failing tests for FileState and ArtifactStateTracker data model**

```python
# tests/test_artifact_state_tracker.py
"""Tests for ArtifactStateTracker."""

import hashlib
import pytest


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


class TestFileState:
    def test_is_dirty_when_never_sent(self):
        from app.context.artifact_state_tracker import FileState
        fs = FileState(
            content_hash=_sha256("print('hello')"),
            last_sent_hash=None,
            byte_size=15,
            language="python",
        )
        assert fs.is_dirty()

    def test_is_dirty_when_content_changed(self):
        from app.context.artifact_state_tracker import FileState
        old_hash = _sha256("print('hello')")
        new_hash = _sha256("print('world')")
        fs = FileState(
            content_hash=new_hash,
            last_sent_hash=old_hash,
            byte_size=15,
            language="python",
        )
        assert fs.is_dirty()

    def test_is_clean_when_hashes_match(self):
        from app.context.artifact_state_tracker import FileState
        h = _sha256("print('hello')")
        fs = FileState(
            content_hash=h,
            last_sent_hash=h,
            byte_size=15,
            language="python",
        )
        assert not fs.is_dirty()


class TestArtifactStateTrackerInit:
    def test_sync_from_files(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
            {"path": "style.css", "language": "css", "content": "body { margin: 0; }"},
        ]
        tracker.sync_from_files(files)
        assert len(tracker.files) == 2
        assert tracker.files["main.py"].content_hash == _sha256("print('hello')")
        assert tracker.files["main.py"].last_sent_hash is None  # Never sent
        assert tracker.files["main.py"].is_dirty()

    def test_get_dirty_files_returns_all_when_never_sent(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
        ]
        tracker.sync_from_files(files)
        dirty = tracker.get_dirty_files()
        assert len(dirty) == 1
        assert dirty[0][0] == "main.py"
        assert dirty[0][1] == "print('hello')"

    def test_mark_sent_clears_dirty(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
        ]
        tracker.sync_from_files(files)
        tracker.mark_sent(["main.py"])
        assert len(tracker.get_dirty_files()) == 0

    def test_mark_mutated_makes_dirty_again(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
        ]
        tracker.sync_from_files(files)
        tracker.mark_sent(["main.py"])
        assert not tracker.files["main.py"].is_dirty()
        tracker.mark_mutated("main.py", "print('world')")
        assert tracker.files["main.py"].is_dirty()
        assert tracker.files["main.py"].content_hash == _sha256("print('world')")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifact_state_tracker.py -v 2>&1 | head -40`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.context'`

- [ ] **Step 4: Implement FileState and ArtifactStateTracker core**

```python
# app/context/artifact_state_tracker.py
"""Artifact State Tracker — knows what the model has seen vs what exists.

Tracks per-file SHA-256 hashes to detect dirty files that need re-injection
into the prompt. Supports version-transition diffing so unchanged files
aren't re-sent across version boundaries.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _sha256(content: str) -> str:
    """Compute SHA-256 hash of string content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class FileState:
    """Tracks hash state for a single file in an artifact."""

    content_hash: str
    last_sent_hash: Optional[str]
    byte_size: int
    language: str

    def is_dirty(self) -> bool:
        """True if the model hasn't seen the current content."""
        return self.content_hash != self.last_sent_hash


class ArtifactStateTracker:
    """Per-session tracker for artifact file state.

    One instance per chat session. Tracks which files the model has
    seen (via last_sent_hash) vs what actually exists (content_hash).
    """

    def __init__(self, artifact_id: str, version_id: str) -> None:
        self.artifact_id = artifact_id
        self.version_id = version_id
        self.files: Dict[str, FileState] = {}
        self.last_access = time.time()
        self._file_contents: Dict[str, str] = {}  # path -> content cache

    def sync_from_files(
        self,
        files: List[Dict[str, Any]],
        preserve_sent_hashes: bool = False,
    ) -> None:
        """Load file state from a list of artifact file dicts.

        Args:
            files: List of {"path", "language", "content", ...} dicts.
            preserve_sent_hashes: If True, keep last_sent_hash for files
                whose content_hash hasn't changed (version-transition diffing).
        """
        old_files = self.files.copy() if preserve_sent_hashes else {}
        self.files.clear()
        self._file_contents.clear()

        for f in files:
            path = f.get("path", "")
            content = f.get("content", "")
            language = f.get("language", "text")
            byte_size = f.get("byteSize") or len(content.encode("utf-8"))
            content_hash = _sha256(content)

            # Version-transition diffing: if file existed before with same
            # content hash, keep its last_sent_hash so we don't re-inject.
            last_sent = None
            if preserve_sent_hashes and path in old_files:
                old = old_files[path]
                if old.content_hash == content_hash:
                    last_sent = old.last_sent_hash

            self.files[path] = FileState(
                content_hash=content_hash,
                last_sent_hash=last_sent,
                byte_size=byte_size,
                language=language,
            )
            self._file_contents[path] = content

        self.last_access = time.time()

    def get_dirty_files(self) -> List[Tuple[str, str]]:
        """Return list of (path, content) for files the model needs to see."""
        self.last_access = time.time()
        result = []
        for path, state in self.files.items():
            if state.is_dirty():
                content = self._file_contents.get(path, "")
                result.append((path, content))
        return result

    def mark_sent(self, file_paths: List[str]) -> None:
        """Mark files as seen by the model after prompt injection."""
        self.last_access = time.time()
        for path in file_paths:
            if path in self.files:
                self.files[path].last_sent_hash = self.files[path].content_hash

    def mark_mutated(self, file_path: str, new_content: str) -> None:
        """Update content hash after a patch or new version."""
        self.last_access = time.time()
        new_hash = _sha256(new_content)
        byte_size = len(new_content.encode("utf-8"))

        if file_path in self.files:
            self.files[file_path].content_hash = new_hash
            self.files[file_path].byte_size = byte_size
        else:
            self.files[file_path] = FileState(
                content_hash=new_hash,
                last_sent_hash=None,
                byte_size=byte_size,
                language="text",
            )
        self._file_contents[file_path] = new_content

    def get_context_injection(
        self,
        max_bytes: int,
        user_message: str = "",
    ) -> List[Dict[str, Any]]:
        """Return minimal set of file contents for prompt injection.

        Priority: dirty files first, scored by keyword overlap with
        user message tokens and file paths/languages. Recency as tiebreaker.
        Respects byte budget.

        Returns list of {"path", "language", "content"} dicts.
        """
        self.last_access = time.time()
        dirty = self.get_dirty_files()
        if not dirty:
            return []

        # Score by keyword overlap with user message
        user_tokens = set(user_message.lower().split()) if user_message else set()

        def _score(path: str) -> float:
            path_tokens = set(path.lower().replace("/", " ").replace(".", " ").split())
            lang = self.files[path].language.lower() if path in self.files else ""
            path_tokens.add(lang)
            overlap = len(user_tokens & path_tokens)
            return overlap

        dirty.sort(key=lambda x: _score(x[0]), reverse=True)

        result = []
        total_bytes = 0
        for path, content in dirty:
            content_bytes = len(content.encode("utf-8"))
            if total_bytes + content_bytes > max_bytes:
                continue
            total_bytes += content_bytes
            lang = self.files[path].language if path in self.files else "text"
            result.append({"path": path, "language": lang, "content": content})

        return result

    def to_persistence_dict(self) -> Dict[str, Any]:
        """Serialize hash state for Firestore persistence (no content)."""
        file_hashes = {}
        for path, state in self.files.items():
            file_hashes[path] = {
                "content_hash": state.content_hash,
                "last_sent_hash": state.last_sent_hash,
                "byte_size": state.byte_size,
                "language": state.language,
            }
        return {
            "artifact_id": self.artifact_id,
            "version_id": self.version_id,
            "file_hashes": file_hashes,
        }

    @classmethod
    def from_persistence_dict(
        cls, data: Dict[str, Any], file_contents: Dict[str, str]
    ) -> "ArtifactStateTracker":
        """Restore from Firestore-persisted hash state + current file contents."""
        tracker = cls(
            artifact_id=data["artifact_id"],
            version_id=data["version_id"],
        )
        for path, hashes in data.get("file_hashes", {}).items():
            content = file_contents.get(path, "")
            tracker.files[path] = FileState(
                content_hash=hashes["content_hash"],
                last_sent_hash=hashes.get("last_sent_hash"),
                byte_size=hashes.get("byte_size", 0),
                language=hashes.get("language", "text"),
            )
            tracker._file_contents[path] = content
        return tracker
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifact_state_tracker.py -v 2>&1 | tail -20`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/context/__init__.py app/context/artifact_state_tracker.py tests/test_artifact_state_tracker.py
git commit -m "feat(context): add ArtifactStateTracker with per-file SHA-256 dirty checking"
```

---

## Task 2: Artifact State Tracker — Version Transition + LRU Cache + Context Injection

**Files:**
- Modify: `app/context/artifact_state_tracker.py`
- Test: `tests/test_artifact_state_tracker.py`

- [ ] **Step 1: Write failing tests for version transition, LRU cache, and context injection**

```python
# Append to tests/test_artifact_state_tracker.py

class TestVersionTransition:
    def test_unchanged_files_keep_sent_hash(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files_v1 = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
            {"path": "style.css", "language": "css", "content": "body {}"},
        ]
        tracker.sync_from_files(files_v1)
        tracker.mark_sent(["main.py", "style.css"])

        # Version transition: main.py unchanged, style.css updated
        files_v2 = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
            {"path": "style.css", "language": "css", "content": "body { color: red; }"},
        ]
        tracker.version_id = "ver_2"
        tracker.sync_from_files(files_v2, preserve_sent_hashes=True)

        # main.py should NOT be dirty (content unchanged, last_sent preserved)
        assert not tracker.files["main.py"].is_dirty()
        # style.css SHOULD be dirty (content changed)
        assert tracker.files["style.css"].is_dirty()

    def test_new_file_in_version_is_dirty(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files_v1 = [{"path": "main.py", "language": "python", "content": "x = 1"}]
        tracker.sync_from_files(files_v1)
        tracker.mark_sent(["main.py"])

        files_v2 = [
            {"path": "main.py", "language": "python", "content": "x = 1"},
            {"path": "utils.py", "language": "python", "content": "def helper(): pass"},
        ]
        tracker.version_id = "ver_2"
        tracker.sync_from_files(files_v2, preserve_sent_hashes=True)

        assert not tracker.files["main.py"].is_dirty()
        assert tracker.files["utils.py"].is_dirty()


class TestContextInjection:
    def test_respects_byte_budget(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "big.py", "language": "python", "content": "x" * 1000},
            {"path": "small.py", "language": "python", "content": "y = 1"},
        ]
        tracker.sync_from_files(files)
        # Budget only fits the small file
        result = tracker.get_context_injection(max_bytes=100)
        assert len(result) == 1
        assert result[0]["path"] == "small.py"

    def test_prioritizes_by_keyword_overlap(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "style.css", "language": "css", "content": "body {}"},
            {"path": "main.py", "language": "python", "content": "print('hi')"},
        ]
        tracker.sync_from_files(files)
        # User mentions "python" — main.py should come first
        result = tracker.get_context_injection(max_bytes=100000, user_message="fix the python code")
        assert result[0]["path"] == "main.py"

    def test_returns_empty_when_no_dirty_files(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [{"path": "main.py", "language": "python", "content": "x = 1"}]
        tracker.sync_from_files(files)
        tracker.mark_sent(["main.py"])
        result = tracker.get_context_injection(max_bytes=100000)
        assert result == []


class TestSessionCache:
    def test_get_or_create_returns_same_instance(self):
        from app.context.artifact_state_tracker import get_tracker, clear_tracker_cache
        clear_tracker_cache()
        t1 = get_tracker("session_1", "art_1", "ver_1")
        t2 = get_tracker("session_1", "art_1", "ver_1")
        assert t1 is t2

    def test_different_sessions_get_different_trackers(self):
        from app.context.artifact_state_tracker import get_tracker, clear_tracker_cache
        clear_tracker_cache()
        t1 = get_tracker("session_1", "art_1", "ver_1")
        t2 = get_tracker("session_2", "art_1", "ver_1")
        assert t1 is not t2

    def test_eviction_after_max_size(self):
        from app.context.artifact_state_tracker import (
            get_tracker, clear_tracker_cache, _SESSION_STATES
        )
        clear_tracker_cache()
        # Fill cache past limit (default 100)
        for i in range(105):
            get_tracker(f"session_{i}", "art_1", "ver_1")
        assert len(_SESSION_STATES) <= 100


class TestPersistence:
    def test_round_trip_persistence(self):
        from app.context.artifact_state_tracker import ArtifactStateTracker
        tracker = ArtifactStateTracker(artifact_id="art_1", version_id="ver_1")
        files = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
        ]
        tracker.sync_from_files(files)
        tracker.mark_sent(["main.py"])

        # Serialize
        data = tracker.to_persistence_dict()

        # Restore
        contents = {"main.py": "print('hello')"}
        restored = ArtifactStateTracker.from_persistence_dict(data, contents)
        assert restored.artifact_id == "art_1"
        assert not restored.files["main.py"].is_dirty()
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifact_state_tracker.py -v 2>&1 | tail -30`
Expected: New tests for `TestSessionCache` FAIL (functions don't exist yet), others PASS

- [ ] **Step 3: Add session cache functions to artifact_state_tracker.py**

Add at the bottom of `app/context/artifact_state_tracker.py`:

```python
# ---- Session-level LRU cache ----

_SESSION_STATES: Dict[str, ArtifactStateTracker] = {}
_MAX_CACHE_SIZE = 100
_EVICTION_TIMEOUT_SECS = 1800  # 30 minutes


def get_tracker(
    session_id: str,
    artifact_id: str,
    version_id: str,
) -> ArtifactStateTracker:
    """Get or create a tracker for a session. LRU eviction at 100 entries."""
    _evict_stale()
    if session_id in _SESSION_STATES:
        tracker = _SESSION_STATES[session_id]
        tracker.last_access = time.time()
        return tracker
    tracker = ArtifactStateTracker(artifact_id=artifact_id, version_id=version_id)
    _SESSION_STATES[session_id] = tracker
    if len(_SESSION_STATES) > _MAX_CACHE_SIZE:
        _evict_oldest()
    return tracker


def clear_tracker_cache() -> None:
    """Clear the session cache (for testing)."""
    _SESSION_STATES.clear()


def _evict_stale() -> None:
    """Remove trackers not accessed within the timeout."""
    now = time.time()
    stale = [
        sid for sid, t in _SESSION_STATES.items()
        if now - t.last_access > _EVICTION_TIMEOUT_SECS
    ]
    for sid in stale:
        logger.info(f"Evicting stale tracker for session {sid}")
        del _SESSION_STATES[sid]


def _evict_oldest() -> None:
    """Evict the least recently accessed tracker."""
    if not _SESSION_STATES:
        return
    oldest_sid = min(_SESSION_STATES, key=lambda sid: _SESSION_STATES[sid].last_access)
    logger.info(f"LRU evicting tracker for session {oldest_sid}")
    del _SESSION_STATES[oldest_sid]
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifact_state_tracker.py -v 2>&1 | tail -30`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/context/artifact_state_tracker.py tests/test_artifact_state_tracker.py
git commit -m "feat(context): add version-transition diffing, LRU cache, and context injection to state tracker"
```

---

## Task 3: Action Parser

**Files:**
- Create: `app/context/action_parser.py`
- Test: `tests/test_action_parser.py`

- [ ] **Step 1: Write failing tests for ActionParser**

```python
# tests/test_action_parser.py
"""Tests for ActionParser — replaces META tag parsing."""

import pytest


class TestActionParserChat:
    def test_parse_chat_action(self):
        from app.context.action_parser import ActionParser
        text = "ACTION: chat\n\nHere is my response about Python."
        result = ActionParser.parse(text)
        assert result.action == "chat"
        assert "Here is my response about Python." in result.chat_text
        assert result.artifact_payload is None
        assert result.patch_payload is None

    def test_strips_action_line_from_chat_text(self):
        from app.context.action_parser import ActionParser
        text = "ACTION: chat\n\nHello world"
        result = ActionParser.parse(text)
        assert "ACTION:" not in result.chat_text
        assert "Hello world" in result.chat_text


class TestActionParserCreateArtifact:
    def test_parse_create_artifact(self):
        from app.context.action_parser import ActionParser
        text = (
            'ACTION: create_artifact\n\n'
            'Building a counter app.\n\n'
            '```artifact\n'
            '{"title": "Counter", "entrypoint": "main.py", '
            '"files": [{"path": "main.py", "language": "python", "content": "x=1"}], '
            '"meta": {}}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None
        assert result.artifact_payload["title"] == "Counter"
        assert "Building a counter app" in result.chat_text
        assert "```artifact" not in result.chat_text

    def test_parse_create_artifact_without_action_line(self):
        """Fallback: artifact fence present but no ACTION line."""
        from app.context.action_parser import ActionParser
        text = (
            'Here is your app.\n\n'
            '```artifact\n'
            '{"title": "App", "entrypoint": "main.py", '
            '"files": [{"path": "main.py", "language": "python", "content": "x=1"}], '
            '"meta": {}}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "create_artifact"
        assert result.artifact_payload is not None


class TestActionParserPatchArtifact:
    def test_parse_patch_artifact(self):
        from app.context.action_parser import ActionParser
        text = (
            'ACTION: patch_artifact\n\n'
            'Fixing the bug.\n\n'
            '```artifact_patch\n'
            '{"patches": [{"file": "main.py", "old_string": "x=1", "new_string": "x=2"}]}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "patch_artifact"
        assert result.patch_payload is not None
        assert len(result.patch_payload["patches"]) == 1
        assert "Fixing the bug" in result.chat_text
        assert "```artifact_patch" not in result.chat_text

    def test_parse_patch_without_action_line(self):
        """Fallback: artifact_patch fence but no ACTION line."""
        from app.context.action_parser import ActionParser
        text = (
            'Fixed it.\n\n'
            '```artifact_patch\n'
            '{"patches": [{"file": "main.py", "old_string": "x=1", "new_string": "x=2"}]}\n'
            '```'
        )
        result = ActionParser.parse(text)
        assert result.action == "patch_artifact"
        assert result.patch_payload is not None


class TestActionParserFallback:
    def test_no_action_no_fence_defaults_to_chat(self):
        from app.context.action_parser import ActionParser
        text = "Just a normal response with some text."
        result = ActionParser.parse(text)
        assert result.action == "chat"
        assert result.chat_text == text

    def test_empty_response(self):
        from app.context.action_parser import ActionParser
        result = ActionParser.parse("")
        assert result.action == "chat"
        assert result.chat_text == ""

    def test_preserves_raw_response(self):
        from app.context.action_parser import ActionParser
        text = "ACTION: chat\n\nHello"
        result = ActionParser.parse(text)
        assert result.raw_response == text


class TestStreamingBuffer:
    def test_buffer_accumulates_until_newline(self):
        from app.context.action_parser import StreamingActionBuffer
        buf = StreamingActionBuffer()
        assert buf.feed("ACT") is None
        assert buf.feed("ION: patch_") is None
        result = buf.feed("artifact\nHere is")
        assert result == "patch_artifact"

    def test_buffer_returns_action_on_first_newline(self):
        from app.context.action_parser import StreamingActionBuffer
        buf = StreamingActionBuffer()
        result = buf.feed("ACTION: chat\nHello")
        assert result == "chat"
        assert buf.remainder == "Hello"

    def test_buffer_fallback_when_no_action(self):
        from app.context.action_parser import StreamingActionBuffer
        buf = StreamingActionBuffer()
        result = buf.feed("Just some text\nMore text")
        assert result == "chat"  # Default fallback
        assert "Just some text" in buf.remainder
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_action_parser.py -v 2>&1 | head -30`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement ActionParser**

```python
# app/context/action_parser.py
"""ActionParser — replaces META tag parsing with ACTION: first-line protocol.

Every model response should begin with:
    ACTION: chat
    ACTION: create_artifact
    ACTION: patch_artifact

Falls back to fence-detection heuristics when the ACTION line is missing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ACTION_LINE_RE = re.compile(r"^\s*ACTION:\s*(\w+)", re.IGNORECASE)
_ARTIFACT_FENCE_RE = re.compile(r"```artifact\s*([\s\S]*?)\s*```", re.IGNORECASE)
_ARTIFACT_UNCLOSED_RE = re.compile(r"```artifact\s*([\s\S]+)", re.IGNORECASE)
_PATCH_FENCE_RE = re.compile(r"```artifact_patch\s*([\s\S]*?)\s*```", re.IGNORECASE)

_VALID_ACTIONS = {"chat", "create_artifact", "patch_artifact"}


@dataclass
class ParsedAction:
    """Result of parsing a model response."""

    action: str  # 'chat' | 'create_artifact' | 'patch_artifact'
    chat_text: str  # Everything except artifact/patch fence
    artifact_payload: Optional[Dict[str, Any]]  # Parsed artifact JSON (if create)
    patch_payload: Optional[Dict[str, Any]]  # Parsed patch JSON (if patch)
    raw_response: str  # Full unmodified response
    used_fallback: bool = False  # True if ACTION line was missing


class ActionParser:
    """Parse ACTION: protocol from model responses."""

    @staticmethod
    def parse(response_text: str) -> ParsedAction:
        """Parse a complete model response into a ParsedAction."""
        if not response_text:
            return ParsedAction(
                action="chat",
                chat_text="",
                artifact_payload=None,
                patch_payload=None,
                raw_response="",
            )

        raw = response_text
        action = None
        used_fallback = False

        # Step 1: Try to extract ACTION line
        lines = response_text.split("\n", 1)
        first_line = lines[0].strip()
        match = _ACTION_LINE_RE.match(first_line)

        if match:
            keyword = match.group(1).lower()
            if keyword in _VALID_ACTIONS:
                action = keyword
                # Strip the ACTION line from the response
                response_text = lines[1] if len(lines) > 1 else ""
                response_text = response_text.lstrip("\n")

        # Step 2: If no ACTION line, use fence-detection fallback
        if action is None:
            used_fallback = True
            if _PATCH_FENCE_RE.search(response_text):
                action = "patch_artifact"
            elif _ARTIFACT_FENCE_RE.search(response_text) or _ARTIFACT_UNCLOSED_RE.search(response_text):
                action = "create_artifact"
            else:
                action = "chat"

            if used_fallback and action != "chat":
                logger.info(
                    f"ActionParser fallback triggered: action={action}, "
                    f"first_100={raw[:100]!r}"
                )

        # Step 3: Extract payloads based on action
        artifact_payload = None
        patch_payload = None
        chat_text = response_text

        if action == "create_artifact":
            artifact_payload, chat_text = _extract_artifact(response_text)
        elif action == "patch_artifact":
            patch_payload, chat_text = _extract_patch(response_text)

        return ParsedAction(
            action=action,
            chat_text=chat_text.strip(),
            artifact_payload=artifact_payload,
            patch_payload=patch_payload,
            raw_response=raw,
            used_fallback=used_fallback,
        )


def _extract_artifact(text: str):
    """Extract artifact JSON from fenced block. Returns (payload, chat_text)."""
    from app.artifacts import extract_artifact_block, ARTIFACT_FENCE_PATTERN

    try:
        payload = extract_artifact_block(text)
    except Exception as exc:
        logger.warning(f"ActionParser: artifact extraction failed: {exc}")
        payload = None

    # Strip the artifact fence from chat text
    chat_text = ARTIFACT_FENCE_PATTERN.sub("", text).strip()
    # Also try unclosed pattern
    if not chat_text or chat_text == text:
        chat_text = _ARTIFACT_UNCLOSED_RE.sub("", text).strip()

    return payload, chat_text


def _extract_patch(text: str):
    """Extract patch JSON from fenced block. Returns (payload, chat_text)."""
    from app.artifacts import extract_artifact_patch_block

    payload = extract_artifact_patch_block(text)
    chat_text = _PATCH_FENCE_RE.sub("", text).strip()
    return payload, chat_text


class StreamingActionBuffer:
    """Buffer streaming chunks to parse the ACTION line.

    Streaming chunks don't respect line boundaries. This buffer
    accumulates chunks until the first newline, then parses the
    ACTION line. After parsing, remainder holds any text after
    the newline that should be forwarded to the client.
    """

    def __init__(self) -> None:
        self.buffer = ""
        self.action: Optional[str] = None
        self.remainder: str = ""
        self._resolved = False

    def feed(self, chunk: str) -> Optional[str]:
        """Feed a chunk. Returns action string when resolved, else None.

        After resolution, self.remainder contains any text after the
        ACTION line that should be forwarded to the client.
        """
        if self._resolved:
            return self.action

        self.buffer += chunk

        # Wait for first newline
        newline_idx = self.buffer.find("\n")
        if newline_idx == -1:
            return None

        # We have a complete first line
        first_line = self.buffer[:newline_idx].strip()
        rest = self.buffer[newline_idx + 1:]

        match = _ACTION_LINE_RE.match(first_line)
        if match:
            keyword = match.group(1).lower()
            if keyword in _VALID_ACTIONS:
                self.action = keyword
                self.remainder = rest
                self._resolved = True
                return self.action

        # No valid ACTION line — default to chat, pass everything through
        self.action = "chat"
        self.remainder = self.buffer
        self._resolved = True
        logger.info(
            f"StreamingActionBuffer: no ACTION line found, defaulting to chat. "
            f"First line: {first_line[:100]!r}"
        )
        return self.action
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_action_parser.py -v 2>&1 | tail -30`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/context/action_parser.py tests/test_action_parser.py
git commit -m "feat(context): add ActionParser with ACTION: protocol and streaming buffer"
```

---

## Task 4: Context Builder — Token Estimation + MicroCompact

**Files:**
- Create: `app/context/context_builder.py`
- Test: `tests/test_context_builder.py`

- [ ] **Step 1: Write failing tests for token estimation and microcompact**

```python
# tests/test_context_builder.py
"""Tests for ContextBuilder — smart prompt assembly."""

import pytest


class TestTokenEstimation:
    def test_english_text_estimate(self):
        from app.context.context_builder import estimate_tokens
        text = "Hello world, this is a test of token estimation."
        tokens = estimate_tokens(text)
        # ~50 chars / 3.5 ~ 14 tokens
        assert 10 <= tokens <= 20

    def test_code_text_estimate(self):
        from app.context.context_builder import estimate_tokens
        code = "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n-1)"
        tokens = estimate_tokens(code, is_code=True)
        # ~80 chars / 3 ~ 27 tokens
        assert 20 <= tokens <= 35

    def test_empty_string(self):
        from app.context.context_builder import estimate_tokens
        assert estimate_tokens("") == 0


class TestMicroCompact:
    def test_replaces_long_code_blocks(self):
        from app.context.context_builder import microcompact_message
        # Build a response with a 60-line code block
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_context_builder.py -v 2>&1 | head -20`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement token estimation and microcompact**

```python
# app/context/context_builder.py
"""Context Builder — smart prompt assembly for XelaSphere chat.

Replaces the fixed 20-message cap with 4-zone prompt assembly:
  Zone 1: System prompt + instructions (fixed)
  Zone 2: Artifact context — dirty files only (dynamic budget)
  Zone 3: Conversation history — 3 tiers (fills remaining)
  Zone 4: Current user message (fixed)

API constraint: The XelaSphere API layer is string-based. send_prompt(),
get_ai_response(), and stream_deepseek_response() all accept a single
string prompt. ContextBuilder.build() therefore outputs a flat assembled
string via BuiltContext.prompt. The BuiltContext.messages field exists
for future messages-API migration but is not used in the current integration.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Note: Dense minified JS or generated JSON can hit len/2.5.
# The 10% headroom should absorb this, but if context overruns
# appear in production, check code-density ratio before blaming the model.
_ENGLISH_CHARS_PER_TOKEN = 3.5
_CODE_CHARS_PER_TOKEN = 3.0
_HEADROOM_FACTOR = 0.90  # 10% safety margin

_CODE_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def estimate_tokens(text: str, is_code: bool = False) -> int:
    """Estimate token count using character-based heuristic.

    Provider-agnostic — no tokenizer dependency.
    """
    if not text:
        return 0
    divisor = _CODE_CHARS_PER_TOKEN if is_code else _ENGLISH_CHARS_PER_TOKEN
    return int(len(text) / divisor)


def _has_code_indicators(text: str) -> bool:
    """Check if text is predominantly code."""
    if not text:
        return False
    return bool(_CODE_FENCE_RE.search(text)) or text.count("\n    ") > 5


def estimate_tokens_auto(text: str) -> int:
    """Estimate tokens, auto-detecting code vs English."""
    return estimate_tokens(text, is_code=_has_code_indicators(text))


def microcompact_message(
    message: Dict[str, Any],
    artifact_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Strip heavyweight content from an older message.

    Replaces:
    - Code blocks >50 lines with placeholder
    - Preserves prompts unchanged
    - Returns a shallow copy with modified response
    """
    result = message.copy()
    response = result.get("response", "")
    if not response:
        return result

    def _replace_long_code(match: re.Match) -> str:
        lang = match.group(1) or "code"
        code = match.group(2)
        line_count = code.count("\n") + 1
        if line_count <= 50:
            return match.group(0)  # Keep short blocks
        version_hint = f" -- see artifact v{artifact_version}" if artifact_version else ""
        return f"[Code block: {lang}, {line_count} lines{version_hint}]"

    result["response"] = _CODE_FENCE_RE.sub(_replace_long_code, response)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_context_builder.py -v 2>&1 | tail -20`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/context/context_builder.py tests/test_context_builder.py
git commit -m "feat(context): add token estimation and microcompact to ContextBuilder"
```

---

## Task 5: Context Builder — Metadata Extraction

**Files:**
- Modify: `app/context/context_builder.py`
- Test: `tests/test_context_builder.py`

- [ ] **Step 1: Write failing tests for metadata extraction**

Append to `tests/test_context_builder.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_context_builder.py::TestMetadataExtraction -v 2>&1 | tail -15`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement metadata extraction and tier 2 summary**

Add to `app/context/context_builder.py`:

```python
def extract_turn_metadata(
    turn_number: int,
    user_message: str,
    action_type: str,
    had_error: bool,
    artifact_mutation: Optional[Dict[str, Any]] = None,
    error_detail: Optional[str] = None,
    model: str = "",
) -> Dict[str, Any]:
    """Extract structured metadata from a turn for Tier 2 summaries.

    Called at save time (in save_interaction), not at build time.
    user_intent_summary: strip code blocks first, then take first 80 chars.
    """
    # Strip code blocks, then take first 80 chars
    stripped = _CODE_FENCE_RE.sub("", user_message).strip()
    intent = stripped[:80] if stripped else user_message[:80]

    meta: Dict[str, Any] = {
        "turn_number": turn_number,
        "user_intent_summary": intent,
        "action_type": action_type,
        "had_error": had_error,
        "model": model,
    }
    if error_detail:
        meta["error_detail"] = error_detail
    if artifact_mutation:
        meta["artifact_mutation"] = artifact_mutation
    return meta


def build_tier2_summary(metadata_list: List[Dict[str, Any]]) -> str:
    """Build a structured summary from turn metadata for Tier 2 injection.

    Each line captures: turn number, user intent, action taken, errors.
    """
    if not metadata_list:
        return ""

    first_turn = metadata_list[0].get("turn_number", 1)
    last_turn = metadata_list[-1].get("turn_number", len(metadata_list))
    lines = [f"## Conversation Summary (turns {first_turn}-{last_turn})"]

    for meta in metadata_list:
        turn = meta.get("turn_number", "?")
        intent = meta.get("user_intent_summary", "")
        action = meta.get("action_type", "chat")
        had_error = meta.get("had_error", False)
        error_detail = meta.get("error_detail", "")
        mutation = meta.get("artifact_mutation")

        parts = [f"- Turn {turn}: {intent}"]

        if action == "create_artifact" and mutation:
            files = mutation.get("files_changed", [])
            to_ver = mutation.get("to_version", "")
            parts.append(f" -> Created artifact ({to_ver}, {len(files)} files)")
        elif action == "patch_artifact" and mutation:
            files = mutation.get("files_changed", [])
            to_ver = mutation.get("to_version", "")
            parts.append(f" -> Patched {', '.join(files)} -> {to_ver}")

        if had_error:
            err = f": {error_detail}" if error_detail else ""
            parts.append(f" [ERROR{err}]")

        lines.append("".join(parts))

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_context_builder.py -v 2>&1 | tail -20`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/context/context_builder.py tests/test_context_builder.py
git commit -m "feat(context): add metadata extraction and Tier 2 summary building"
```

---

## Task 6: Context Builder — Full build() Method

**Files:**
- Modify: `app/context/context_builder.py`
- Test: `tests/test_context_builder.py`

- [ ] **Step 1: Write failing tests for ContextBuilder.build()**

Append to `tests/test_context_builder.py`:

```python
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
        # Attach turn_metadata inline for testing
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
        # Last 5 messages should be in verbatim form
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
        # Tier 2 summary should be present for older turns
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
        # Create messages where older ones have big code blocks
        messages = []
        for i in range(8):
            if i == 2:
                # Big code block that should get microcompacted (turn [len-7..len-3])
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
        # The big code block from turn 2 should be compacted
        assert "line_0" not in result.prompt
        assert "[Code block:" in result.prompt

    def test_proper_role_interleaving(self):
        """Verify that user and assistant messages produce separate entries."""
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
        # The prompt should contain User: and Assistant: as separate blocks
        assert "User: hello" in result.prompt
        assert "Assistant: world" in result.prompt
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_context_builder.py::TestContextBuilderBuild -v 2>&1 | tail -20`
Expected: FAIL — `ContextBuilder` class doesn't exist yet

- [ ] **Step 3: Implement ContextBuilder.build()**

Add to `app/context/context_builder.py`:

```python
@dataclass
class BuiltContext:
    """Output of ContextBuilder.build().

    prompt: The flat assembled string to pass to the API (send_prompt/get_ai_response).
    messages: Structured message list for future messages-API migration (not used currently).
    """

    prompt: str  # Flat string for the API (primary output)
    messages: List[Dict[str, str]]  # Structured for future use
    artifact_files: List[Dict[str, Any]]
    token_estimate: int
    compression_level: str  # 'none' | 'microcompact' | 'summarized'


class ContextBuilder:
    """Smart prompt assembler with 4-zone budget allocation.

    Outputs a flat prompt string matching the existing API contract:
    send_prompt(prompt, model) and get_ai_response(full_prompt, model)
    both accept a single string.
    """

    TIER1_SIZE = 5  # Last N turns included verbatim

    def __init__(self, token_budget: int = 16384) -> None:
        self.token_budget = token_budget

    def build(
        self,
        system_prompt: str,
        full_history: List[Dict[str, Any]],
        artifact_state: Optional[Any] = None,  # ArtifactStateTracker or None
        user_message: str = "",
        memory_context: Optional[str] = None,
    ) -> BuiltContext:
        """Assemble the optimal prompt within the token budget.

        Zones:
          1. System prompt (fixed)
          2. Artifact context — dirty files only (dynamic budget)
          3. Conversation history — 3 tiers (fills remaining)
          4. User's current message (fixed)

        Returns BuiltContext with .prompt as the flat assembled string.
        """
        prompt_parts: List[str] = []
        messages: List[Dict[str, str]] = []
        compression_level = "none"

        # --- Zone 1: System prompt (fixed cost) ---
        zone1_text = system_prompt
        if memory_context:
            zone1_text += f"\n\nUser memory:\n{memory_context}"
        zone1_tokens = estimate_tokens_auto(zone1_text)
        prompt_parts.append(zone1_text)
        messages.append({"role": "system", "content": zone1_text})

        # --- Zone 4: User message (fixed cost, added last) ---
        zone4_text = f"User: {user_message}\nAssistant:"
        zone4_tokens = estimate_tokens_auto(zone4_text)

        # --- Zone 3 minimum: last TIER1_SIZE turns verbatim ---
        tier1_messages = full_history[-self.TIER1_SIZE:]
        tier1_text_estimate = self._estimate_history_tokens(tier1_messages)

        # --- Zone 2: Artifact context (dynamic budget) ---
        available = int(self.token_budget * _HEADROOM_FACTOR)
        zone2_budget_tokens = max(0, available - zone1_tokens - zone4_tokens - tier1_text_estimate)
        zone2_budget_bytes = int(zone2_budget_tokens * _ENGLISH_CHARS_PER_TOKEN)

        artifact_files: List[Dict[str, Any]] = []
        zone2_text = ""
        if artifact_state is not None:
            artifact_files = artifact_state.get_context_injection(
                max_bytes=zone2_budget_bytes,
                user_message=user_message,
            )
            if artifact_files:
                zone2_text = self._format_artifact_context(artifact_files)
                artifact_state.mark_sent([f["path"] for f in artifact_files])

        zone2_tokens = estimate_tokens_auto(zone2_text) if zone2_text else 0
        if zone2_text:
            prompt_parts.append(zone2_text)
            messages.append({"role": "system", "content": zone2_text})

        # --- Zone 3: Conversation history (fills remaining budget) ---
        remaining_tokens = available - zone1_tokens - zone2_tokens - zone4_tokens
        zone3_parts, zone3_messages, compression_level = self._build_zone3(
            full_history, remaining_tokens, artifact_state
        )
        prompt_parts.extend(zone3_parts)
        messages.extend(zone3_messages)

        # --- Zone 4: User message (always last) ---
        prompt_parts.append(zone4_text)
        messages.append({"role": "user", "content": user_message})

        # Assemble final flat prompt
        prompt = "\n".join(prompt_parts)
        total_tokens = estimate_tokens_auto(prompt)

        return BuiltContext(
            prompt=prompt,
            messages=messages,
            artifact_files=artifact_files,
            token_estimate=total_tokens,
            compression_level=compression_level,
        )

    def _build_zone3(
        self,
        full_history: List[Dict[str, Any]],
        budget_tokens: int,
        artifact_state: Optional[Any],
    ) -> tuple:
        """Build Zone 3 conversation history with 3 tiers.

        Returns (prompt_parts, messages_list, compression_level).
        """
        if not full_history:
            return [], [], "none"

        tier1_start = max(0, len(full_history) - self.TIER1_SIZE)
        older_messages = full_history[:tier1_start]
        tier1_messages = full_history[tier1_start:]

        # Get artifact version for microcompact hints
        artifact_version = None
        if artifact_state is not None:
            vid = getattr(artifact_state, "version_id", "")
            if vid:
                artifact_version = vid

        # Tier 1: Recent messages (verbatim, with microcompact on older ones)
        # MicroCompact applies to turns [len-7..len-3], never [len-2..len]
        compression_level = "none"
        never_compact_count = 2  # Last 2 turns never compacted
        tier1_prompt_parts = []
        tier1_msg_list = []

        for idx, msg in enumerate(tier1_messages):
            is_recent = idx >= len(tier1_messages) - never_compact_count
            if is_recent:
                source_msg = msg
            else:
                source_msg = microcompact_message(msg, artifact_version=artifact_version)
                if source_msg.get("response") != msg.get("response"):
                    compression_level = "microcompact"

            # Produce separate User/Assistant entries
            user_part, assistant_part, user_msg, asst_msg = self._format_single_message(source_msg)
            tier1_prompt_parts.append(user_part)
            tier1_msg_list.append(user_msg)
            if assistant_part:
                tier1_prompt_parts.append(assistant_part)
                tier1_msg_list.append(asst_msg)

        tier1_tokens = sum(estimate_tokens_auto(p) for p in tier1_prompt_parts)

        # Tier 2: Metadata summary of older messages
        tier2_text = ""
        if older_messages:
            metadata_list = []
            for i, msg in enumerate(older_messages):
                meta = msg.get("turn_metadata")
                if meta:
                    metadata_list.append(meta)
                else:
                    metadata_list.append({
                        "turn_number": i + 1,
                        "user_intent_summary": (msg.get("prompt", "") or "")[:80],
                        "action_type": "chat",
                        "had_error": False,
                        "artifact_mutation": None,
                        "model": msg.get("model_id", ""),
                    })
            tier2_text = build_tier2_summary(metadata_list)
            compression_level = "summarized"

        tier2_tokens = estimate_tokens_auto(tier2_text) if tier2_text else 0

        # Assemble within budget
        total_zone3 = tier1_tokens + tier2_tokens
        result_parts = []
        result_msgs = []

        if total_zone3 <= budget_tokens:
            if tier2_text:
                result_parts.append(tier2_text)
                result_msgs.append({"role": "system", "content": tier2_text})
            result_parts.extend(tier1_prompt_parts)
            result_msgs.extend(tier1_msg_list)
        elif tier1_tokens <= budget_tokens:
            # Tier 1 fits, truncate Tier 2 from oldest lines
            remaining = budget_tokens - tier1_tokens
            if tier2_text and remaining > 0:
                tier2_lines = tier2_text.split("\n")
                truncated = []
                tokens_used = 0
                for line in tier2_lines:
                    line_tokens = estimate_tokens_auto(line)
                    if tokens_used + line_tokens > remaining:
                        break
                    truncated.append(line)
                    tokens_used += line_tokens
                if truncated:
                    trunc_text = "\n".join(truncated)
                    result_parts.append(trunc_text)
                    result_msgs.append({"role": "system", "content": trunc_text})
            result_parts.extend(tier1_prompt_parts)
            result_msgs.extend(tier1_msg_list)
        else:
            # Even Tier 1 doesn't fit — trim oldest Tier 1 messages
            tokens_used = 0
            trimmed_parts = []
            trimmed_msgs = []
            for part, msg in zip(reversed(tier1_prompt_parts), reversed(tier1_msg_list)):
                part_tokens = estimate_tokens_auto(part)
                if tokens_used + part_tokens > budget_tokens:
                    break
                trimmed_parts.append(part)
                trimmed_msgs.append(msg)
                tokens_used += part_tokens
            result_parts = list(reversed(trimmed_parts))
            result_msgs = list(reversed(trimmed_msgs))

        return result_parts, result_msgs, compression_level

    def _estimate_history_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate total tokens for a list of messages."""
        total = 0
        for msg in messages:
            if msg.get("prompt"):
                total += estimate_tokens_auto(f"User: {msg['prompt']}")
            if msg.get("response"):
                total += estimate_tokens_auto(f"Assistant: {msg['response']}")
        return total

    def _format_single_message(self, msg: Dict[str, Any]):
        """Format a single message into separate User/Assistant parts.

        Returns (user_text, assistant_text, user_msg_dict, assistant_msg_dict).
        assistant_text may be empty string if no response.
        """
        prompt = msg.get("prompt", "")
        response = msg.get("response", "")

        user_text = f"User: {prompt}" if prompt else ""
        assistant_text = f"Assistant: {response}" if response else ""

        user_msg = {"role": "user", "content": prompt} if prompt else {"role": "user", "content": ""}
        asst_msg = {"role": "assistant", "content": response} if response else None

        return user_text, assistant_text, user_msg, asst_msg

    def _format_artifact_context(self, files: List[Dict[str, Any]]) -> str:
        """Format artifact files for prompt injection."""
        parts = ["CURRENT ARTIFACT FILES (for reference when fixing/updating):"]
        for f in files:
            path = f.get("path", "unknown")
            lang = f.get("language", "text")
            content = f.get("content", "")
            parts.append(f"\n{path} ({lang}):")
            parts.append(f"```{lang}")
            parts.append(content)
            parts.append("```")
        return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_context_builder.py -v 2>&1 | tail -30`
Expected: All 17 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/context/context_builder.py tests/test_context_builder.py
git commit -m "feat(context): add ContextBuilder.build() with 4-zone assembly and flat prompt output"
```

---

## Task 7: Update System Prompt — Replace META with ACTION Rules

**Files:**
- Modify: `app/services/chat_engine.py:152-664`

- [ ] **Step 1: Read the current DEVELOPER_INSTRUCTIONS block**

Read: `app/services/chat_engine.py` lines 566-664 to see the full current META instructions.

- [ ] **Step 2: Replace the output mode decision block and META format instructions**

Replace the section in `DEVELOPER_INSTRUCTIONS` (lines 566-664 in `chat_engine.py`) that contains the META instructions with ACTION format rules.

Find this block starting at line 566:
```python
    "⚠️ GOLDEN RULE: Default to CHAT unless there is a strong reason to escalate.\n\n"

    "OUTPUT MODE DECISION:\n"
    ...everything through...
    "Response: <META>{\"output_mode\":\"artifact_patch\",...}</META> + ```artifact_patch block\n"
)
```

Replace with:
```python
    "⚠️ GOLDEN RULE: Default to CHAT unless there is a strong reason to escalate.\n\n"

    "===============================================================\n"
    "RESPONSE FORMAT - ACTION PROTOCOL\n"
    "===============================================================\n\n"

    "Your FIRST LINE must be an ACTION declaration. Choose exactly one:\n\n"

    "ACTION: chat\n"
    "  Use when: answering questions, explaining concepts, discussing approaches,\n"
    "  giving feedback, or when the user's request does not produce a runnable\n"
    "  deliverable. Short code snippets (under 12 lines) that illustrate a point\n"
    "  belong in chat, not an artifact.\n\n"

    "ACTION: create_artifact\n"
    "  Use when: the user asks you to BUILD, CREATE, or MAKE something that is a\n"
    "  complete, runnable deliverable (app, game, tool, page, script). The result\n"
    "  must be self-contained code that a user would want to run, preview, or\n"
    "  download. Do NOT create an artifact for partial code, pseudocode, or\n"
    "  explanations with code examples.\n\n"

    "ACTION: patch_artifact\n"
    "  Use when: the user asks you to FIX, UPDATE, CHANGE, MODIFY, or IMPROVE\n"
    "  an existing artifact. You will receive the current file contents below.\n"
    "  Respond with only the patches needed - do not regenerate unchanged files.\n\n"

    "After the ACTION line, write your response.\n\n"

    "-- ACTION: chat --\n"
    "ACTION: chat\n\n"
    "Your normal markdown response here. Code blocks for illustration are fine.\n\n"

    "-- ACTION: create_artifact --\n"
    "ACTION: create_artifact\n\n"
    "Brief explanation of what you're building.\n\n"
    "```artifact\n"
    "{\"title\":\"...\",\"type\":\"project\",\"primaryLanguage\":\"...\","
    "\"files\":[{\"path\":\"...\",\"language\":\"...\",\"content\":\"...\"}]}\n"
    "```\n\n"

    "For React apps: Use App.jsx as entrypoint + style.css. DO NOT create index.html.\n"
    "  The preview system auto-loads React 18 + Babel. Just write JSX directly.\n"
    "  IMPORTANT: return() MUST have a SINGLE root element.\n"
    "  CRITICAL: Inside artifact code, NEVER use markdown like **bold**, *italic*.\n"
    "  NEVER mix index.html with .jsx files.\n"
    "For plain HTML apps: Create index.html, style.css, script.js.\n"
    "React artifacts MUST have AT LEAST 2 files: App.jsx + style.css.\n\n"

    "-- ACTION: patch_artifact --\n"
    "ACTION: patch_artifact\n\n"
    "Brief explanation of what you're changing.\n\n"
    "```artifact_patch\n"
    "{\"patches\":[{\"file\":\"App.jsx\",\"old_string\":\"exact text to find\",\"new_string\":\"replacement\"}]}\n"
    "```\n\n"
    "Rules for patches:\n"
    "- old_string must be EXACT text from the current code (include 2-4 surrounding lines)\n"
    "- new_string is the replacement (empty string for deletions)\n"
    "- Multiple patches allowed in one block\n"
    "- Write a brief summary of what you changed before the patch block\n\n"

    "Triggers for patch: 'fix', 'update', 'correct', 'change', 'modify', 'edit', 'improve'\n"
)
```

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py -v 2>&1 | tail -20`
Expected: All existing tests still PASS

- [ ] **Step 4: Commit**

```bash
git add app/services/chat_engine.py
git commit -m "refactor(prompts): replace META tag instructions with ACTION protocol"
```

---

## Task 8: Integrate save_interaction() with Metadata Extraction

**Files:**
- Modify: `app/services/chat_engine.py:1402-1490`

- [ ] **Step 1: Add turn_metadata to save_interaction()**

Add one new parameter to `save_interaction()` at `chat_engine.py:1402`:

```python
def save_interaction(
    user_id: str,
    session_id: str,
    user_prompt: str,
    response_text: str,
    sentiment: str,
    is_code_response: bool,
    search_results: List[SearchResult],
    user_attachments: Optional[List[Dict[str, Any]]] = None,
    persist: bool = True,
    response_id: Optional[str] = None,
    render_mode: str = "inline",
    artifact_reference: Optional[Dict[str, Any]] = None,
    reasoning: Optional[str] = None,
    crypto_chart: Optional[Dict[str, Any]] = None,
    display_prompt: Optional[str] = None,
    model_id: Optional[str] = None,
    task_type: Optional[str] = None,
    auto_selected: bool = False,
    turn_metadata: Optional[Dict[str, Any]] = None,  # Smart Context metadata
) -> Tuple[Dict[str, Any], bool]:
```

Then after the `chat_data` dict is built (around line 1441), add:

```python
    if turn_metadata:
        chat_data['turn_metadata'] = turn_metadata
```

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/ -v --timeout=30 2>&1 | tail -20`
Expected: All existing tests PASS (new param is optional with default None)

- [ ] **Step 3: Commit**

```bash
git add app/services/chat_engine.py
git commit -m "feat(context): add turn_metadata to save_interaction()"
```

---

## Task 9: Integrate into /chat_stream Endpoint

**Files:**
- Modify: `app/blueprints/chat_routes.py:394-977`

This is the largest integration task. It replaces the current prompt assembly, artifact decision logic, and patch handling in the streaming endpoint.

**Important:** The API layer is string-based. `stream_deepseek_response(prompt, model)` accepts a single string. `ContextBuilder.build()` returns `BuiltContext` with `.prompt` as the flat assembled string. Use `built_context.prompt` directly as `enhanced_prompt`.

- [ ] **Step 1: Add imports at top of chat_routes.py**

Add after the existing artifact imports (around line 20-40 of chat_routes.py):

```python
from app.context.action_parser import ActionParser, StreamingActionBuffer
from app.context.context_builder import ContextBuilder, extract_turn_metadata
from app.context.artifact_state_tracker import get_tracker, ArtifactStateTracker
```

- [ ] **Step 2: Replace history cap with full history loading**

In `chat_stream()`, replace the 20-message cap (lines 484-487):

```python
                # Cap messages in memory — only recent ones are needed for context
                if len(previous_messages) > 20:
                    logger.debug(f"[Stream] Trimming {len(previous_messages)} messages to last 20")
                    previous_messages = previous_messages[-20:]
```

With:

```python
                # Smart Context: load full history (Context Builder handles compression)
                logger.debug(f"[Stream] Loaded {len(previous_messages)} messages (full history for Context Builder)")
```

- [ ] **Step 3: Replace manual context injection with ContextBuilder**

Replace the prompt assembly block (lines 568-610) that manually injects memory, history, and artifact context:

```python
    # Inject memory context into streaming prompt (was missing — memories retrieved but never used)
    enhanced_prompt = prompt
    if long_term_memory_stream:
        enhanced_prompt = f"User memory (from previous sessions):\n{long_term_memory_stream}\n\n{enhanced_prompt}"
        ...
    # Inject recent chat history for conversation continuity
    if previous_messages:
        recent = previous_messages[-6:]
        ...
    # Claude-inspired: Inject current artifact context when user asks to fix/update
    if session_id and user_id and detect_artifact_update_intent(prompt):
        ...
```

With:

```python
    # --- Smart Context Window: build prompt via ContextBuilder ---
    artifact_tracker = None
    if session_id and user_id:
        try:
            from app.artifacts import ArtifactStore
            from app.config import db as _db
            store = ArtifactStore(_db)
            artifacts_data = store.list_artifacts(user_id, session_id)
            artifacts = artifacts_data.get('artifacts', [])
            active = next((a for a in artifacts if a.get('isActive')), artifacts[0] if artifacts else None)
            if active:
                full_art = store.get_artifact(user_id, session_id, active['artifactId'])
                if full_art and full_art.get('activeVersion'):
                    version = full_art['activeVersion']
                    files = version.get('files', [])
                    artifact_tracker = get_tracker(
                        session_id,
                        active['artifactId'],
                        version.get('versionId', ''),
                    )
                    artifact_tracker.sync_from_files(files, preserve_sent_hashes=True)
        except Exception as e:
            logger.warning(f"[Stream] Failed to load artifact state: {e}")

    # Determine if we should inject artifact context
    # Inject when dirty files exist OR last action was patch_artifact
    should_inject_artifact = False
    if artifact_tracker:
        has_dirty = len(artifact_tracker.get_dirty_files()) > 0
        last_action_was_patch = False
        if previous_messages:
            last_meta = previous_messages[-1].get('turn_metadata', {})
            last_action_was_patch = last_meta.get('action_type') == 'patch_artifact'
        should_inject_artifact = has_dirty or last_action_was_patch

    from app.model_catalog import get_model_context_limit
    model_context = get_model_context_limit(stream_model)
    context_builder = ContextBuilder(token_budget=model_context)
    built_context = context_builder.build(
        system_prompt=SYSTEM_INSTRUCTIONS + "\n" + DEVELOPER_INSTRUCTIONS,
        full_history=previous_messages,
        artifact_state=artifact_tracker if should_inject_artifact else None,
        user_message=prompt,
        memory_context=long_term_memory_stream or None,
    )
    # Use the flat prompt string — API layer is string-based
    enhanced_prompt = built_context.prompt
    logger.info(f"[Stream] Context built: {built_context.token_estimate} tokens, "
                f"level={built_context.compression_level}, "
                f"artifact_files={len(built_context.artifact_files)}")
```

- [ ] **Step 4: Replace ArtifactRouter with ActionParser in stream done handler**

First, initialize `parsed_action = None` at the top of `generate()` (around line 676, right after `content_buffer = []`):

```python
        reasoning_buffer = []
        content_buffer = []
        parsed_action = None  # Will be set when response is complete
```

Then replace the artifact processing block (lines 707-714):

```python
                    try:
                        structured = parse_structured_response(full_content)
                        routing = artifact_router.route(structured, prompt, provider_flagged_code=False)
```

With:

```python
                    try:
                        parsed_action = ActionParser.parse(full_content)
                        logger.info(f"Stream ActionParser: action={parsed_action.action}, "
                                    f"has_artifact={parsed_action.artifact_payload is not None}, "
                                    f"has_patch={parsed_action.patch_payload is not None}, "
                                    f"fallback={parsed_action.used_fallback}")

                        # Map ParsedAction to ArtifactDecision for compatibility
                        if parsed_action.action == "create_artifact" and parsed_action.artifact_payload:
                            routing = ArtifactDecision(
                                render_mode="artifact",
                                chat_payload=parsed_action.chat_text,
                                artifact_payload=parsed_action.artifact_payload,
                                contains_code=True,
                                desired_action=ArtifactPreference.UPDATE,
                            )
                        elif parsed_action.action == "patch_artifact" and parsed_action.patch_payload:
                            routing = ArtifactDecision(
                                render_mode="artifact_patch",
                                chat_payload=parsed_action.chat_text,
                                artifact_payload=parsed_action.patch_payload,
                                contains_code=True,
                                desired_action=ArtifactPreference.UPDATE,
                            )
                        else:
                            routing = ArtifactDecision(
                                render_mode="inline",
                                chat_payload=parsed_action.chat_text,
                                artifact_payload=None,
                                contains_code=False,
                                desired_action=ArtifactPreference.NONE,
                            )
```

- [ ] **Step 5: Add State Tracker mutation tracking after patch apply**

After the existing patch apply success block (around line 734, after `if applied_count > 0:`), add state tracker updates:

```python
                                    if applied_count > 0:
                                        # Update state tracker with mutated files
                                        if artifact_tracker:
                                            for pf in patched_files:
                                                artifact_tracker.mark_mutated(
                                                    pf['path'], pf.get('content', '')
                                                )
```

- [ ] **Step 6: Add turn_metadata to save_interaction call**

In the `save_interaction()` call inside `generate()` (around line 906), build turn_metadata using the already-initialized `parsed_action`:

```python
                            # Build turn metadata for Smart Context
                            action_type = parsed_action.action if parsed_action is not None else "chat"
                            had_error = bool(artifact_error)
                            art_mutation = None
                            if artifact_ref:
                                art_mutation = {
                                    "artifact_id": artifact_ref.get('artifactId'),
                                    "from_version": None,
                                    "to_version": artifact_ref.get('versionId'),
                                    "files_changed": [f.get('path', '') for f in
                                                      (routing.artifact_payload or {}).get('files', [])],
                                }
                            stream_turn_metadata = extract_turn_metadata(
                                turn_number=len(previous_messages) + 1,
                                user_message=prompt,
                                action_type=action_type,
                                had_error=had_error,
                                artifact_mutation=art_mutation,
                                error_detail=artifact_error,
                                model=stream_model,
                            )

                            save_interaction(
                                user_id=user_id,
                                session_id=session_id,
                                user_prompt=prompt,
                                response_text=chat_summary,
                                sentiment='neutral',
                                is_code_response=(render_mode in ['artifact', 'code_window']),
                                search_results=[],
                                persist=True,
                                response_id=response_msg_id,
                                render_mode=render_mode,
                                artifact_reference=artifact_ref,
                                reasoning=full_reasoning if full_reasoning else None,
                                crypto_chart=crypto_chart,
                                turn_metadata=stream_turn_metadata,
                            )
```

- [ ] **Step 7: Test imports work end-to-end**

Run: `cd /root/xelas.io/xelasphere.io- && python -c "from app.context.action_parser import ActionParser; from app.context.context_builder import ContextBuilder; from app.context.artifact_state_tracker import get_tracker; print('All imports OK')" 2>&1`
Expected: "All imports OK"

- [ ] **Step 8: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(context): integrate Smart Context Window into /chat_stream endpoint"
```

---

## Task 10: Integrate into /chat Endpoint

**Files:**
- Modify: `app/blueprints/chat_routes.py:980-2020`

- [ ] **Step 1: Replace 20-message cap in /chat**

Replace lines 1260-1263:

```python
            if len(previous_messages) > 20:
                logger.debug(f"Trimming {len(previous_messages)} messages to last 20")
                previous_messages = previous_messages[-20:]
```

With:

```python
            # Smart Context: load full history (Context Builder handles compression)
            logger.debug(f"Loaded {len(previous_messages)} messages (full history for Context Builder)")
```

- [ ] **Step 2: Replace build_prompt() call with ContextBuilder**

Replace the prompt building block (lines 1505-1562) that calls `detect_artifact_update_intent()`, loads artifact context, and calls `build_prompt()`.

Replace:
```python
            # Claude-inspired: Inject current artifact context when user asks to fix/update
            artifact_context = None
            if detect_artifact_update_intent(user_prompt):
                ...
            full_prompt = build_prompt(
                user_prompt=user_prompt,
                ...
            )
```

With:
```python
            # --- Smart Context Window: build prompt via ContextBuilder ---
            artifact_tracker = None
            try:
                store = artifact_store
                artifacts_data = store.list_artifacts(user_id, session_id)
                artifacts = artifacts_data.get('artifacts', [])
                active = next((a for a in artifacts if a.get('isActive')), artifacts[0] if artifacts else None)
                if active:
                    full_art = store.get_artifact(user_id, session_id, active['artifactId'])
                    if full_art and full_art.get('activeVersion'):
                        version = full_art['activeVersion']
                        files = version.get('files', [])
                        artifact_tracker = get_tracker(
                            session_id,
                            active['artifactId'],
                            version.get('versionId', ''),
                        )
                        artifact_tracker.sync_from_files(files, preserve_sent_hashes=True)
            except Exception as e:
                logger.warning(f"Failed to load artifact state: {e}")

            should_inject_artifact = False
            if artifact_tracker:
                has_dirty = len(artifact_tracker.get_dirty_files()) > 0
                last_action_was_patch = False
                if prev_msgs_for_prompt:
                    last_meta = prev_msgs_for_prompt[-1].get('turn_metadata', {})
                    last_action_was_patch = last_meta.get('action_type') == 'patch_artifact'
                should_inject_artifact = has_dirty or last_action_was_patch

            from app.model_catalog import get_model_context_limit
            model_context = get_model_context_limit(model)
            context_builder = ContextBuilder(token_budget=model_context)

            # Include extracted text, search results in user message
            enriched_prompt = user_prompt
            if extracted_text:
                max_extract = min(len(extracted_text), 30000)
                enriched_prompt = f"Uploaded file content:\n{extracted_text[:max_extract]}\n\nUser request: {enriched_prompt}"
            if search_results:
                search_parts = []
                for r in search_results[:5]:
                    search_parts.append(f"[{r.title}]({r.link})\n{r.snippet}")
                search_text = "Web search results:\n" + "\n\n".join(search_parts)
                enriched_prompt = f"{search_text}\n\n{enriched_prompt}"

            built_context = context_builder.build(
                system_prompt=SYSTEM_INSTRUCTIONS + "\n" + DEVELOPER_INSTRUCTIONS,
                full_history=prev_msgs_for_prompt,
                artifact_state=artifact_tracker if should_inject_artifact else None,
                user_message=enriched_prompt,
                memory_context=long_term_memory or None,
            )
            # Use the flat prompt string — API layer is string-based
            full_prompt = built_context.prompt
            prompt_max_tokens = model_context
            logger.info(f"Context built: {built_context.token_estimate} tokens, "
                        f"level={built_context.compression_level}, "
                        f"artifact_files={len(built_context.artifact_files)}")
```

- [ ] **Step 3: Replace ArtifactRouter with ActionParser in /chat response handling**

First, initialize `parsed_action = None` near the top of the `/chat` handler (after getting `raw_model_response`):

```python
        raw_model_response = strip_html_from_response(raw_model_response)
        parsed_action = None  # Will be set below
```

Then replace lines 1769-1774:
```python
        structured_response = parse_structured_response(raw_model_response)
        routing_decision = artifact_router.route(
            structured_response,
            user_prompt,
            provider_flagged_code=provider_flagged_code
        )
```

With:
```python
        parsed_action = ActionParser.parse(raw_model_response)
        logger.info(f"ActionParser: action={parsed_action.action}, "
                     f"has_artifact={parsed_action.artifact_payload is not None}, "
                     f"has_patch={parsed_action.patch_payload is not None}, "
                     f"fallback={parsed_action.used_fallback}")

        if parsed_action.action == "create_artifact" and parsed_action.artifact_payload:
            routing_decision = ArtifactDecision(
                render_mode="artifact",
                chat_payload=parsed_action.chat_text,
                artifact_payload=parsed_action.artifact_payload,
                contains_code=True,
                desired_action=ArtifactPreference.UPDATE,
            )
        elif parsed_action.action == "patch_artifact" and parsed_action.patch_payload:
            routing_decision = ArtifactDecision(
                render_mode="artifact_patch",
                chat_payload=parsed_action.chat_text,
                artifact_payload=parsed_action.patch_payload,
                contains_code=True,
                desired_action=ArtifactPreference.UPDATE,
            )
        else:
            # Fall back to existing router for non-ACTION responses
            structured_response = parse_structured_response(raw_model_response)
            routing_decision = artifact_router.route(
                structured_response,
                user_prompt,
                provider_flagged_code=provider_flagged_code,
            )
```

- [ ] **Step 4: Add state tracker mutation tracking after /chat patch apply**

After the patch apply in /chat (around line 1862), add:

```python
                    if applied_count > 0 and artifact_tracker:
                        for pf in patched_files:
                            artifact_tracker.mark_mutated(pf['path'], pf.get('content', ''))
```

- [ ] **Step 5: Add turn_metadata to /chat save_interaction call**

Before the `save_interaction()` call (around line 1977), build turn_metadata using the already-initialized `parsed_action`:

```python
        # Build turn metadata for Smart Context
        chat_action_type = parsed_action.action if parsed_action is not None else "chat"
        chat_had_error = getattr(routing_decision, 'error_message', None) is not None
        chat_art_mutation = None
        if artifact_reference:
            chat_art_mutation = {
                "artifact_id": artifact_reference.get('artifactId'),
                "from_version": None,
                "to_version": artifact_reference.get('versionId'),
                "files_changed": [f.get('path', '') for f in
                                  (routing_decision.artifact_payload or {}).get('files', [])],
            }
        chat_turn_metadata = extract_turn_metadata(
            turn_number=len(previous_messages) + 1,
            user_message=user_prompt,
            action_type=chat_action_type,
            had_error=chat_had_error,
            artifact_mutation=chat_art_mutation,
            error_detail=getattr(routing_decision, 'error_message', None),
            model=selected_model_id or model,
        )
```

Then add `turn_metadata=chat_turn_metadata` to the `save_interaction()` call.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(context): integrate Smart Context Window into /chat endpoint"
```

---

## Task 11: Patch Retry Mechanism

**Files:**
- Modify: `app/blueprints/chat_routes.py` (both endpoints)

**Important:** The retry must use the same provider-agnostic call as the primary response. For `/chat`, use `get_ai_response(retry_prompt, model)`. For `/chat_stream`, use `get_ai_response(retry_prompt, stream_model)` synchronously (not streaming) — the retry is bounded to 1 call and the "Verifying patch..." indicator already breaks the stream naturally. Do NOT hardcode `stream_deepseek_response` for the retry.

- [ ] **Step 1: Add patch retry to /chat_stream**

After the existing patch apply in the stream handler (around line 729), add retry logic when all patches fail:

```python
                            if routing.render_mode == 'artifact_patch' and routing.artifact_payload:
                                patch_data = routing.artifact_payload
                                current_files, full_artifact = _load_current_artifact_files(user_id, session_id)
                                if current_files:
                                    patched_files, patch_results = apply_patches(current_files, patch_data.get('patches', []))
                                    applied_count = sum(1 for r in patch_results if r['status'] == 'applied')
                                    failed_count = sum(1 for r in patch_results if r['status'] == 'failed')

                                    # Patch retry: if all patches failed, inject actual content and retry once
                                    if failed_count > 0 and applied_count == 0:
                                        logger.info(f"🔄 Patch retry: {failed_count} patches failed, attempting retry")
                                        yield f"data: {json.dumps({'type': 'status', 'text': 'Verifying patch...'})}\n\n"

                                        # Build retry prompt with actual file contents
                                        failed_files = set(r['file'] for r in patch_results if r['status'] == 'failed')
                                        retry_parts = []
                                        for ff in failed_files:
                                            file_entry = next((f for f in current_files if f['path'] == ff), None)
                                            if file_entry:
                                                lang = file_entry.get('language', 'text')
                                                retry_parts.append(
                                                    f"Your patch for {ff} failed: the old_string does not "
                                                    f"exist in the current file. Here is the actual content:\n\n"
                                                    f"```{lang}\n{file_entry.get('content', '')}\n```\n\n"
                                                    f"Please provide a corrected artifact_patch."
                                                )
                                        retry_prompt = "\n".join(retry_parts)

                                        # Synchronous retry via provider-agnostic get_ai_response
                                        try:
                                            retry_response, _, _ = get_ai_response(retry_prompt, stream_model)
                                            retry_parsed = ActionParser.parse(retry_response)
                                            if retry_parsed.patch_payload:
                                                patched_files, patch_results = apply_patches(
                                                    current_files, retry_parsed.patch_payload.get('patches', [])
                                                )
                                                applied_count = sum(1 for r in patch_results if r['status'] == 'applied')
                                                failed_count = sum(1 for r in patch_results if r['status'] == 'failed')
                                                if applied_count > 0:
                                                    chat_summary = retry_parsed.chat_text or parsed_action.chat_text
                                                    logger.info(f"🔄 Patch retry succeeded: {applied_count} applied")
                                        except Exception as retry_err:
                                            logger.warning(f"🔄 Patch retry failed: {retry_err}")
```

- [ ] **Step 2: Add patch retry to /chat endpoint**

Apply same retry pattern after the patch apply block in `/chat` (around line 1862):

```python
                if failed_count > 0 and applied_count == 0:
                    logger.info(f"🔄 [NonStream] Patch retry: {failed_count} patches failed")
                    failed_files = set(r['file'] for r in patch_results if r['status'] == 'failed')
                    retry_parts = []
                    for ff in failed_files:
                        file_entry = next((f for f in current_files if f['path'] == ff), None)
                        if file_entry:
                            lang = file_entry.get('language', 'text')
                            retry_parts.append(
                                f"Your patch for {ff} failed: the old_string does not "
                                f"exist in the current file. Here is the actual content:\n\n"
                                f"```{lang}\n{file_entry.get('content', '')}\n```\n\n"
                                f"Please provide a corrected artifact_patch."
                            )
                    retry_prompt = "\n".join(retry_parts)
                    try:
                        retry_response, _, _ = get_ai_response(retry_prompt, model)
                        retry_parsed = ActionParser.parse(retry_response)
                        if retry_parsed.patch_payload:
                            patched_files, patch_results = apply_patches(
                                current_files, retry_parsed.patch_payload.get('patches', [])
                            )
                            applied_count = sum(1 for r in patch_results if r['status'] == 'applied')
                            failed_count = sum(1 for r in patch_results if r['status'] == 'failed')
                            if applied_count > 0:
                                response_text = retry_parsed.chat_text or routing_decision.chat_payload
                                logger.info(f"🔄 [NonStream] Patch retry succeeded: {applied_count} applied")
                    except Exception as retry_err:
                        logger.warning(f"🔄 [NonStream] Patch retry failed: {retry_err}")
```

- [ ] **Step 3: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(context): add bounded 1-retry patch correction via get_ai_response"
```

---

## Task 12: Update Package Exports and Final Verification

**Files:**
- Modify: `app/context/__init__.py`

- [ ] **Step 1: Update package init with public API**

```python
# app/context/__init__.py
"""Smart Context Window — context management for XelaSphere chat.

Public API:
  - ArtifactStateTracker, get_tracker: Track what the model has seen
  - ContextBuilder, BuiltContext: Smart prompt assembly
  - ActionParser, ParsedAction, StreamingActionBuffer: Response parsing
  - extract_turn_metadata, build_tier2_summary: Metadata extraction
"""

from app.context.artifact_state_tracker import (
    ArtifactStateTracker,
    FileState,
    get_tracker,
    clear_tracker_cache,
)
from app.context.context_builder import (
    ContextBuilder,
    BuiltContext,
    extract_turn_metadata,
    build_tier2_summary,
    estimate_tokens,
    microcompact_message,
)
from app.context.action_parser import (
    ActionParser,
    ParsedAction,
    StreamingActionBuffer,
)
```

- [ ] **Step 2: Run full test suite**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifact_state_tracker.py tests/test_context_builder.py tests/test_action_parser.py -v 2>&1 | tail -40`
Expected: All tests PASS

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_artifacts.py tests/test_smart_router.py -v --timeout=30 2>&1 | tail -20`
Expected: All existing tests still PASS

- [ ] **Step 4: Verify imports work end-to-end**

Run: `cd /root/xelas.io/xelasphere.io- && python -c "from app.context import ArtifactStateTracker, ContextBuilder, ActionParser, get_tracker, extract_turn_metadata; print('All Smart Context Window imports OK')" 2>&1`
Expected: "All Smart Context Window imports OK"

- [ ] **Step 5: Commit**

```bash
git add app/context/__init__.py
git commit -m "feat(context): finalize Smart Context Window package exports"
```

---

## Task 13: Remove Deprecated Code

**Files:**
- Modify: `app/blueprints/chat_routes.py`
- Modify: `app/services/chat_engine.py`

Note: `ArtifactDecisionEngine`, `detect_artifact_update_intent()`, and `parse_structured_response()` are NOT deleted from `artifacts.py` yet — they're kept as fallback and for any code paths that still reference them. They can be removed in a follow-up cleanup once the new system is validated in production.

- [ ] **Step 1: Remove detect_artifact_update_intent() calls from chat_routes.py**

Search for and remove any remaining direct calls to `detect_artifact_update_intent()` that weren't replaced in Tasks 9-10:

Run: `cd /root/xelas.io/xelasphere.io- && grep -n "detect_artifact_update_intent" app/blueprints/chat_routes.py`
Expected: No matches (all replaced in Tasks 9-10). If any remain, remove them.

- [ ] **Step 2: Remove old build_prompt() call from /chat if still present**

Verify the old `build_prompt()` call in `/chat` (line ~1545) has been replaced:

Run: `cd /root/xelas.io/xelasphere.io- && grep -n "build_prompt(" app/blueprints/chat_routes.py`
Expected: No matches (replaced in Task 10). If any remain, remove them.

- [ ] **Step 3: Commit**

```bash
git add app/blueprints/chat_routes.py app/services/chat_engine.py
git commit -m "refactor(context): remove deprecated detect_artifact_update_intent and build_prompt calls"
```

---

## Summary

| Task | What | Files | Tests |
|------|------|-------|-------|
| 1 | State Tracker core | `artifact_state_tracker.py` | 6 |
| 2 | Version transition + LRU + injection | `artifact_state_tracker.py` | 8 |
| 3 | ActionParser | `action_parser.py` | 11 |
| 4 | Token estimation + microcompact | `context_builder.py` | 6 |
| 5 | Metadata extraction | `context_builder.py` | 4+1 |
| 6 | ContextBuilder.build() | `context_builder.py` | 7 |
| 7 | System prompt update | `chat_engine.py` | 0 (existing) |
| 8 | save_interaction metadata | `chat_engine.py` | 0 (existing) |
| 9 | /chat_stream integration | `chat_routes.py` | 0 (manual) |
| 10 | /chat integration | `chat_routes.py` | 0 (manual) |
| 11 | Patch retry mechanism | `chat_routes.py` | 0 (manual) |
| 12 | Package exports + verification | `__init__.py` | 0 (verify) |
| 13 | Remove deprecated code | cleanup | 0 (verify) |

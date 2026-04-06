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
from dataclasses import dataclass
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
    """Per-session tracker for artifact file state."""

    def __init__(self, artifact_id: str, version_id: str) -> None:
        self.artifact_id = artifact_id
        self.version_id = version_id
        self.files: Dict[str, FileState] = {}
        self.last_access = time.time()
        self._file_contents: Dict[str, str] = {}

    def sync_from_files(
        self,
        files: List[Dict[str, Any]],
        preserve_sent_hashes: bool = False,
    ) -> None:
        """Load file state from artifact file dicts.

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

    def get_all_files(self) -> List[Dict[str, Any]]:
        """Return all tracked files with their content."""
        self.last_access = time.time()
        result = []
        for path, state in self.files.items():
            content = self._file_contents.get(path, "")
            result.append({"path": path, "language": state.language, "content": content})
        return result

    def get_context_injection(
        self,
        max_bytes: int,
        user_message: str = "",
        include_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return file contents for prompt injection.

        When include_all=False (default): only dirty files (for minimal injection).
        When include_all=True: all files (needed when model will patch any file).

        Priority: dirty files first, then clean files. Scored by keyword
        overlap with user message. Respects byte budget.
        """
        self.last_access = time.time()
        if include_all:
            candidates = [(p, self._file_contents.get(p, "")) for p in self.files]
        else:
            candidates = self.get_dirty_files()
        if not candidates:
            return []

        user_tokens = set(user_message.lower().split()) if user_message else set()

        def _score(path: str) -> float:
            path_tokens = set(path.lower().replace("/", " ").replace(".", " ").split())
            lang = self.files[path].language.lower() if path in self.files else ""
            path_tokens.add(lang)
            return len(user_tokens & path_tokens)

        candidates.sort(key=lambda x: _score(x[0]), reverse=True)

        result = []
        total_bytes = 0
        for path, content in candidates:
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

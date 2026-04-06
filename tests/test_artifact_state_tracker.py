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
        assert tracker.files["main.py"].last_sent_hash is None
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

        files_v2 = [
            {"path": "main.py", "language": "python", "content": "print('hello')"},
            {"path": "style.css", "language": "css", "content": "body { color: red; }"},
        ]
        tracker.version_id = "ver_2"
        tracker.sync_from_files(files_v2, preserve_sent_hashes=True)

        assert not tracker.files["main.py"].is_dirty()
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

        data = tracker.to_persistence_dict()

        contents = {"main.py": "print('hello')"}
        restored = ArtifactStateTracker.from_persistence_dict(data, contents)
        assert restored.artifact_id == "art_1"
        assert not restored.files["main.py"].is_dirty()

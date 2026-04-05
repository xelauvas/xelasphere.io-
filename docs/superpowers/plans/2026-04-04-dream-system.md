# Dream System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add background memory consolidation that periodically merges duplicates, prunes stale entries, resolves conflicts, and synthesizes higher-level insights from a user's Memory V2 collection.

**Architecture:** Two-phase hybrid — Phase 1 (algorithmic) handles pruning/merging/flagging without LLM, Phase 2 (Kimi) resolves ambiguous conflicts and synthesizes patterns. Triggered per-user after chat responses via a three-gate system (time, session count, lock). All mutations go through existing V2 store/judge. User sees toast notifications and a detailed activity log with undo.

**Tech Stack:** Python/Flask, Firestore, Moonshot/Kimi API, existing Memory V2 modules, vanilla JS frontend

**Spec:** `docs/superpowers/specs/2026-04-04-dream-system-design.md`

---

## File Structure

```
app/memory_v2/dream/
  __init__.py           — Public API: maybe_consolidate(), DreamConfig
  config.py             — Constants, defaults, user setting loader
  gates.py              — Time gate, session count gate, lock gate
  phase1.py             — Algorithmic: prune, merge, detect conflicts, flag
  phase2.py             — Kimi LLM: prompt building, response parsing, decisions
  executor.py           — Batch execution, snapshots, dream log persistence
  report.py             — ConsolidationReport and DreamAction dataclasses

tests/
  test_dream_config.py
  test_dream_gates.py
  test_dream_phase1.py
  test_dream_phase2.py
  test_dream_executor.py
  test_dream_orchestrator.py
```

**Existing files modified:**
- `app/blueprints/chat_routes.py` — add `maybe_consolidate()` call after response
- `app/blueprints/memory_routes.py` — add activity log + undo routes
- `app/static/js/script.js` — add dream toast display on chat response
- `app/templates/index.html` — no changes needed (toast already supported)

---

### Task 1: Dream Config Module

**Files:**
- Create: `app/memory_v2/dream/__init__.py`
- Create: `app/memory_v2/dream/config.py`
- Test: `tests/test_dream_config.py`

- [ ] **Step 1: Create the dream package init**

```python
# app/memory_v2/dream/__init__.py
"""Dream System: Background memory consolidation."""
from app.memory_v2.dream.config import DreamConfig, get_dream_config
```

- [ ] **Step 2: Write failing test for DreamConfig**

```python
# tests/test_dream_config.py
"""Tests for dream system configuration."""
import pytest
from unittest.mock import MagicMock, patch


class TestDreamConfig:
    """Test DreamConfig defaults and user overrides."""

    def test_default_config(self):
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig()
        assert cfg.enabled is True
        assert cfg.frequency_hours == 24
        assert cfg.min_sessions == 5
        assert cfg.min_active_memories == 10
        assert cfg.max_mutations_per_cycle == 50
        assert cfg.max_syntheses_per_cycle == 3
        assert cfg.kimi_token_budget == 4000
        assert cfg.hard_rate_floor_hours == 6
        assert cfg.stale_lock_hours == 1
        assert cfg.circuit_breaker_max_failures == 3
        assert cfg.circuit_breaker_cooldown_hours == 1

    def test_get_dream_config_no_prefs(self):
        from app.memory_v2.dream.config import get_dream_config
        cfg = get_dream_config(user_prefs=None)
        assert cfg.enabled is True
        assert cfg.frequency_hours == 24

    def test_get_dream_config_user_overrides(self):
        from app.memory_v2.dream.config import get_dream_config
        prefs = {
            'dream_enabled': False,
            'dream_frequency_hours': 12,
            'dream_min_sessions': 8,
        }
        cfg = get_dream_config(user_prefs=prefs)
        assert cfg.enabled is False
        assert cfg.frequency_hours == 12
        assert cfg.min_sessions == 8

    def test_get_dream_config_clamps_values(self):
        from app.memory_v2.dream.config import get_dream_config
        prefs = {
            'dream_frequency_hours': 2,   # Below min of 6
            'dream_min_sessions': 100,    # Above max of 20
        }
        cfg = get_dream_config(user_prefs=prefs)
        assert cfg.frequency_hours == 6   # Clamped to min
        assert cfg.min_sessions == 20     # Clamped to max
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_config.py -v`
Expected: ImportError — module does not exist yet

- [ ] **Step 4: Write config implementation**

```python
# app/memory_v2/dream/config.py
"""Dream system configuration and defaults."""
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class DreamConfig:
    """Configuration for the dream consolidation system."""
    # User-configurable
    enabled: bool = True
    frequency_hours: int = 24
    min_sessions: int = 5

    # System safeguards (not user-configurable)
    min_active_memories: int = 10
    max_mutations_per_cycle: int = 50
    max_syntheses_per_cycle: int = 3
    kimi_token_budget: int = 4000
    hard_rate_floor_hours: int = 6
    stale_lock_hours: int = 1
    circuit_breaker_max_failures: int = 3
    circuit_breaker_cooldown_hours: int = 1

    # Phase 1 thresholds
    embedding_similarity_threshold: float = 0.92
    low_value_unused_days: int = 60
    low_value_confidence_floor: float = 0.5

    # Phase 2 trigger
    pattern_synthesis_threshold: int = 15


def get_dream_config(user_prefs: Optional[Dict[str, Any]] = None) -> DreamConfig:
    """Build DreamConfig from defaults + user preference overrides."""
    cfg = DreamConfig()

    if not user_prefs:
        return cfg

    if 'dream_enabled' in user_prefs:
        cfg.enabled = bool(user_prefs['dream_enabled'])

    if 'dream_frequency_hours' in user_prefs:
        val = int(user_prefs['dream_frequency_hours'])
        cfg.frequency_hours = max(6, min(168, val))

    if 'dream_min_sessions' in user_prefs:
        val = int(user_prefs['dream_min_sessions'])
        cfg.min_sessions = max(3, min(20, val))

    return cfg
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_config.py -v`
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/memory_v2/dream/__init__.py app/memory_v2/dream/config.py tests/test_dream_config.py
git commit -m "feat(dream): add config module with defaults and user overrides"
```

---

### Task 2: Report Dataclasses

**Files:**
- Create: `app/memory_v2/dream/report.py`
- Test: `tests/test_dream_config.py` (append)

- [ ] **Step 1: Write failing test for report dataclasses**

Append to `tests/test_dream_config.py`:

```python
class TestDreamReport:
    """Test dream report and action dataclasses."""

    def test_consolidation_report_defaults(self):
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport()
        assert report.pruned == []
        assert report.merged == []
        assert report.flagged_conflicts == []
        assert report.flagged_low_value == []
        assert report.all_active == []

    def test_dream_action_creation(self):
        from app.memory_v2.dream.report import DreamAction
        action = DreamAction(
            action_type='prune',
            memory_id='mem_abc123',
            reason='Expired TTL',
            phase=1,
        )
        assert action.action_type == 'prune'
        assert action.memory_id == 'mem_abc123'
        assert action.before_snapshot is None

    def test_dream_log_creation(self):
        from app.memory_v2.dream.report import DreamLog
        from datetime import datetime
        log = DreamLog(
            user_id='user_123',
            started_at=datetime(2026, 4, 4, 3, 0),
            completed_at=datetime(2026, 4, 4, 3, 1),
        )
        assert log.user_id == 'user_123'
        assert log.phase1_actions == []
        assert log.phase2_actions == []
        assert log.errors == []
        assert log.kimi_raw_response is None

    def test_dream_log_summary(self):
        from app.memory_v2.dream.report import DreamLog, DreamAction
        from datetime import datetime
        log = DreamLog(
            user_id='user_123',
            started_at=datetime(2026, 4, 4, 3, 0),
            completed_at=datetime(2026, 4, 4, 3, 1),
            phase1_actions=[
                DreamAction(action_type='prune', memory_id='m1', reason='expired', phase=1),
                DreamAction(action_type='prune', memory_id='m2', reason='expired', phase=1),
                DreamAction(action_type='merge', memory_id='m3', reason='duplicate', phase=1),
            ],
            phase2_actions=[
                DreamAction(action_type='synthesize', memory_id='m4', reason='pattern', phase=2),
            ],
        )
        summary = log.summary()
        assert summary['pruned'] == 2
        assert summary['merged'] == 1
        assert summary['synthesized'] == 1
        assert summary['total_actions'] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_config.py::TestDreamReport -v`
Expected: ImportError

- [ ] **Step 3: Write report implementation**

```python
# app/memory_v2/dream/report.py
"""Dataclasses for dream consolidation reports and logs."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class ConsolidationReport:
    """Output of Phase 1 algorithmic consolidation."""
    pruned: List[str] = field(default_factory=list)
    merged: List[Tuple[str, str]] = field(default_factory=list)
    flagged_conflicts: List[List[str]] = field(default_factory=list)
    flagged_low_value: List[str] = field(default_factory=list)
    all_active: List[Any] = field(default_factory=list)


@dataclass
class DreamAction:
    """A single action taken during consolidation."""
    action_type: str  # 'prune', 'merge', 'supersede', 'promote', 'synthesize'
    memory_id: str
    reason: str
    phase: int  # 1 or 2
    before_snapshot: Optional[Dict[str, Any]] = None
    related_ids: List[str] = field(default_factory=list)


@dataclass
class DreamLog:
    """Full log of a dream consolidation cycle."""
    user_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    phase1_actions: List[DreamAction] = field(default_factory=list)
    phase2_actions: List[DreamAction] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    kimi_raw_response: Optional[str] = None

    def summary(self) -> Dict[str, int]:
        """Return action counts by type."""
        all_actions = self.phase1_actions + self.phase2_actions
        counts: Dict[str, int] = {}
        for action in all_actions:
            key = action.action_type
            # Normalize to past tense for display
            display_key = {
                'prune': 'pruned',
                'merge': 'merged',
                'supersede': 'superseded',
                'promote': 'promoted',
                'synthesize': 'synthesized',
            }.get(key, key)
            counts[display_key] = counts.get(display_key, 0) + 1
        counts['total_actions'] = len(all_actions)
        return counts

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for Firestore storage."""
        return {
            'user_id': self.user_id,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'phase1_actions': [
                {
                    'action_type': a.action_type,
                    'memory_id': a.memory_id,
                    'reason': a.reason,
                    'phase': a.phase,
                    'before_snapshot': a.before_snapshot,
                    'related_ids': a.related_ids,
                }
                for a in self.phase1_actions
            ],
            'phase2_actions': [
                {
                    'action_type': a.action_type,
                    'memory_id': a.memory_id,
                    'reason': a.reason,
                    'phase': a.phase,
                    'before_snapshot': a.before_snapshot,
                    'related_ids': a.related_ids,
                }
                for a in self.phase2_actions
            ],
            'errors': self.errors,
            'kimi_raw_response': self.kimi_raw_response,
            'summary': self.summary(),
        }
```

- [ ] **Step 4: Update dream __init__.py**

```python
# app/memory_v2/dream/__init__.py
"""Dream System: Background memory consolidation."""
from app.memory_v2.dream.config import DreamConfig, get_dream_config
from app.memory_v2.dream.report import ConsolidationReport, DreamAction, DreamLog
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_config.py -v`
Expected: 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/memory_v2/dream/report.py app/memory_v2/dream/__init__.py tests/test_dream_config.py
git commit -m "feat(dream): add report dataclasses for consolidation logging"
```

---

### Task 3: Gate System

**Files:**
- Create: `app/memory_v2/dream/gates.py`
- Create: `tests/test_dream_gates.py`

- [ ] **Step 1: Write failing tests for gates**

```python
# tests/test_dream_gates.py
"""Tests for dream gate system."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class TestTimeGate:
    """Test the time gate (Gate 1)."""

    def test_passes_when_no_prior_consolidation(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=24)
        result = check_time_gate(last_consolidated_at=None, config=cfg)
        assert result is True

    def test_passes_when_enough_time_elapsed(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=24)
        old_time = datetime.utcnow() - timedelta(hours=25)
        result = check_time_gate(last_consolidated_at=old_time, config=cfg)
        assert result is True

    def test_fails_when_too_recent(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=24)
        recent = datetime.utcnow() - timedelta(hours=2)
        result = check_time_gate(last_consolidated_at=recent, config=cfg)
        assert result is False

    def test_respects_hard_rate_floor(self):
        from app.memory_v2.dream.gates import check_time_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(frequency_hours=6, hard_rate_floor_hours=6)
        five_hours_ago = datetime.utcnow() - timedelta(hours=5)
        result = check_time_gate(last_consolidated_at=five_hours_ago, config=cfg)
        assert result is False


class TestSessionCountGate:
    """Test the session count gate (Gate 2)."""

    def test_passes_with_enough_sessions(self):
        from app.memory_v2.dream.gates import check_session_count_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(min_sessions=5)
        mock_db = MagicMock()
        # Mock query that returns 6 session docs
        mock_docs = [MagicMock() for _ in range(6)]
        mock_query = MagicMock()
        mock_query.stream.return_value = mock_docs
        mock_db.collection.return_value.document.return_value.collection.return_value.where.return_value = mock_query
        last = datetime.utcnow() - timedelta(hours=25)
        result = check_session_count_gate(
            db=mock_db, user_id='user_123',
            last_consolidated_at=last, config=cfg
        )
        assert result is True

    def test_fails_with_few_sessions(self):
        from app.memory_v2.dream.gates import check_session_count_gate
        from app.memory_v2.dream.config import DreamConfig
        cfg = DreamConfig(min_sessions=5)
        mock_db = MagicMock()
        mock_docs = [MagicMock() for _ in range(2)]
        mock_query = MagicMock()
        mock_query.stream.return_value = mock_docs
        mock_db.collection.return_value.document.return_value.collection.return_value.where.return_value = mock_query
        last = datetime.utcnow() - timedelta(hours=25)
        result = check_session_count_gate(
            db=mock_db, user_id='user_123',
            last_consolidated_at=last, config=cfg
        )
        assert result is False


class TestLockGate:
    """Test the lock gate (Gate 3)."""

    def test_acquires_lock_when_free(self):
        from app.memory_v2.dream.gates import try_acquire_lock
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc

        result = try_acquire_lock(db=mock_db, user_id='user_123', worker_id='w1')
        assert result is True
        mock_doc.set.assert_called_once()

    def test_fails_when_locked_by_another(self):
        from app.memory_v2.dream.gates import try_acquire_lock
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            'worker_id': 'other_worker',
            'locked_at': datetime.utcnow() - timedelta(minutes=5),
        }
        mock_doc.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc

        result = try_acquire_lock(db=mock_db, user_id='user_123', worker_id='w1')
        assert result is False

    def test_reclaims_stale_lock(self):
        from app.memory_v2.dream.gates import try_acquire_lock
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = {
            'worker_id': 'dead_worker',
            'locked_at': datetime.utcnow() - timedelta(hours=2),
        }
        mock_doc.get.return_value = mock_snapshot
        mock_db.collection.return_value.document.return_value = mock_doc

        cfg = DreamConfig(stale_lock_hours=1)
        result = try_acquire_lock(
            db=mock_db, user_id='user_123', worker_id='w1', config=cfg
        )
        assert result is True
        mock_doc.set.assert_called_once()

    def test_release_lock(self):
        from app.memory_v2.dream.gates import release_lock
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc
        release_lock(db=mock_db, user_id='user_123')
        mock_doc.delete.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_gates.py -v`
Expected: ImportError

- [ ] **Step 3: Write gates implementation**

```python
# app/memory_v2/dream/gates.py
"""Dream system gate checks: time, session count, lock."""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from app.memory_v2.dream.config import DreamConfig

logger = logging.getLogger(__name__)


def check_time_gate(
    last_consolidated_at: Optional[datetime],
    config: DreamConfig,
) -> bool:
    """Gate 1: Has enough time elapsed since last consolidation?"""
    if last_consolidated_at is None:
        return True

    now = datetime.utcnow()
    hours_since = (now - last_consolidated_at).total_seconds() / 3600

    # Enforce hard rate floor regardless of user setting
    if hours_since < config.hard_rate_floor_hours:
        return False

    return hours_since >= config.frequency_hours


def check_session_count_gate(
    db,
    user_id: str,
    last_consolidated_at: Optional[datetime],
    config: DreamConfig,
) -> bool:
    """Gate 2: Have enough sessions been touched since last consolidation?"""
    since = last_consolidated_at or datetime(2000, 1, 1)

    try:
        sessions_ref = (
            db.collection('users')
            .document(user_id)
            .collection('chat_sessions')
            .where('last_updated', '>', since)
        )
        docs = list(sessions_ref.stream())
        count = len(docs)
        logger.debug(f"[dream] Session count gate: {count} sessions since {since}")
        return count >= config.min_sessions
    except Exception as e:
        logger.warning(f"[dream] Session count gate failed: {e}")
        return False


def try_acquire_lock(
    db,
    user_id: str,
    worker_id: Optional[str] = None,
    config: Optional[DreamConfig] = None,
) -> bool:
    """Gate 3: Try to acquire the consolidation lock. Returns True if acquired."""
    if config is None:
        config = DreamConfig()
    if worker_id is None:
        worker_id = f"worker_{uuid.uuid4().hex[:8]}"

    lock_ref = (
        db.collection('users')
        .document(user_id)
        .document('dream_lock')
    )

    try:
        snapshot = lock_ref.get()

        if snapshot.exists:
            lock_data = snapshot.to_dict()
            locked_at = lock_data.get('locked_at')

            # Check if lock is stale
            if locked_at:
                age_hours = (datetime.utcnow() - locked_at).total_seconds() / 3600
                if age_hours < config.stale_lock_hours:
                    logger.debug(f"[dream] Lock held by {lock_data.get('worker_id')}, age {age_hours:.1f}h")
                    return False
                logger.info(f"[dream] Reclaiming stale lock (age {age_hours:.1f}h)")
            else:
                return False

        lock_ref.set({
            'worker_id': worker_id,
            'locked_at': datetime.utcnow(),
        })
        return True

    except Exception as e:
        logger.warning(f"[dream] Lock acquisition failed: {e}")
        return False


def release_lock(db, user_id: str) -> None:
    """Release the consolidation lock."""
    try:
        lock_ref = (
            db.collection('users')
            .document(user_id)
            .document('dream_lock')
        )
        lock_ref.delete()
    except Exception as e:
        logger.warning(f"[dream] Lock release failed: {e}")


def get_dream_state(db, user_id: str) -> dict:
    """Read the dream state document for a user."""
    try:
        doc_ref = (
            db.collection('users')
            .document(user_id)
            .document('dream_state')
        )
        snapshot = doc_ref.get()
        if snapshot.exists:
            return snapshot.to_dict()
        return {}
    except Exception as e:
        logger.warning(f"[dream] Failed to read dream state: {e}")
        return {}


def update_dream_state(db, user_id: str, updates: dict) -> None:
    """Update the dream state document for a user."""
    try:
        doc_ref = (
            db.collection('users')
            .document(user_id)
            .document('dream_state')
        )
        doc_ref.set(updates, merge=True)
    except Exception as e:
        logger.warning(f"[dream] Failed to update dream state: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_gates.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/gates.py tests/test_dream_gates.py
git commit -m "feat(dream): add three-gate trigger system"
```

---

### Task 4: Phase 1 — Algorithmic Consolidation

**Files:**
- Create: `app/memory_v2/dream/phase1.py`
- Create: `tests/test_dream_phase1.py`

- [ ] **Step 1: Write failing tests for Phase 1**

```python
# tests/test_dream_phase1.py
"""Tests for Phase 1 algorithmic consolidation."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import numpy as np


def _make_memory(id, key, value, mem_type='preference', status='active',
                 confidence=0.8, use_count=5, last_used_days_ago=10,
                 expires_at=None, embedding=None, repeated_by_user=0):
    """Helper to build a mock MemoryItem-like dict for testing."""
    from app.memory_v2.schema import (
        MemoryItem, MemoryType, MemoryStatus, MemoryLifecycle, MemoryEvidence
    )
    type_map = {
        'profile': MemoryType.PROFILE,
        'preference': MemoryType.PREFERENCE,
        'project': MemoryType.PROJECT,
        'constraint': MemoryType.CONSTRAINT,
        'temporary': MemoryType.TEMPORARY,
    }
    status_map = {
        'active': MemoryStatus.ACTIVE,
        'provisional': MemoryStatus.PROVISIONAL,
        'expired': MemoryStatus.EXPIRED,
    }
    last_used = datetime.utcnow() - timedelta(days=last_used_days_ago) if last_used_days_ago else None
    return MemoryItem(
        id=id,
        user_id='user_123',
        type=type_map[mem_type],
        key=key,
        value=value,
        status=status_map[status],
        confidence=confidence,
        lifecycle=MemoryLifecycle(
            created_at=datetime.utcnow() - timedelta(days=90),
            updated_at=datetime.utcnow() - timedelta(days=last_used_days_ago or 0),
            last_used_at=last_used,
            expires_at=expires_at,
            use_count=use_count,
            repeated_by_user=repeated_by_user,
        ),
        embedding=embedding,
    )


class TestPruneExpired:
    """Test Step 1: prune expired memories."""

    def test_prunes_expired_ttl(self):
        from app.memory_v2.dream.phase1 import prune_expired
        expired_time = datetime.utcnow() - timedelta(hours=1)
        memories = [
            _make_memory('m1', 'k1', 'v1', expires_at=expired_time),
            _make_memory('m2', 'k2', 'v2'),  # No expiry
        ]
        pruned = prune_expired(memories)
        assert pruned == ['m1']

    def test_prunes_old_provisional(self):
        from app.memory_v2.dream.phase1 import prune_expired
        memories = [
            _make_memory('m1', 'k1', 'v1', status='provisional',
                         last_used_days_ago=20, repeated_by_user=0),
        ]
        # Provisional older than 14 days with no confirmation
        memories[0].lifecycle.created_at = datetime.utcnow() - timedelta(days=20)
        pruned = prune_expired(memories)
        assert pruned == ['m1']

    def test_keeps_confirmed_provisional(self):
        from app.memory_v2.dream.phase1 import prune_expired
        memories = [
            _make_memory('m1', 'k1', 'v1', status='provisional',
                         repeated_by_user=2),
        ]
        memories[0].lifecycle.created_at = datetime.utcnow() - timedelta(days=20)
        pruned = prune_expired(memories)
        assert pruned == []


class TestMergeNearDuplicates:
    """Test Step 2: merge near-duplicate memories."""

    def test_merges_identical_key_high_similarity(self):
        from app.memory_v2.dream.phase1 import merge_near_duplicates
        emb = np.random.rand(1024).astype(np.float32)
        emb_similar = emb + np.random.normal(0, 0.01, 1024).astype(np.float32)
        memories = [
            _make_memory('m1', 'response_style', 'concise', confidence=0.9,
                         embedding=emb.tolist()),
            _make_memory('m2', 'response_style', 'brief and concise', confidence=0.7,
                         embedding=emb_similar.tolist()),
        ]
        merged = merge_near_duplicates(memories, threshold=0.92)
        assert len(merged) == 1
        winner_id, loser_id = merged[0]
        assert winner_id == 'm1'  # Higher confidence wins
        assert loser_id == 'm2'

    def test_no_merge_different_keys(self):
        from app.memory_v2.dream.phase1 import merge_near_duplicates
        emb = np.random.rand(1024).astype(np.float32)
        memories = [
            _make_memory('m1', 'timezone', 'GMT', embedding=emb.tolist()),
            _make_memory('m2', 'location', 'London', embedding=emb.tolist()),
        ]
        merged = merge_near_duplicates(memories, threshold=0.92)
        assert merged == []

    def test_no_merge_low_similarity(self):
        from app.memory_v2.dream.phase1 import merge_near_duplicates
        emb1 = np.random.rand(1024).astype(np.float32)
        emb2 = np.random.rand(1024).astype(np.float32)  # Random = low similarity
        memories = [
            _make_memory('m1', 'response_style', 'concise', embedding=emb1.tolist()),
            _make_memory('m2', 'response_style', 'verbose', embedding=emb2.tolist()),
        ]
        merged = merge_near_duplicates(memories, threshold=0.92)
        assert merged == []


class TestDetectConflicts:
    """Test Step 3: detect conflicting memories."""

    def test_detects_same_key_different_value(self):
        from app.memory_v2.dream.phase1 import detect_conflicts
        memories = [
            _make_memory('m1', 'timezone', 'PST', mem_type='profile'),
            _make_memory('m2', 'timezone', 'GMT', mem_type='profile'),
        ]
        conflicts = detect_conflicts(memories)
        assert len(conflicts) == 1
        assert set(conflicts[0]) == {'m1', 'm2'}

    def test_no_conflict_for_accumulate_types(self):
        from app.memory_v2.dream.phase1 import detect_conflicts
        memories = [
            _make_memory('m1', 'no_python', 'true', mem_type='constraint'),
            _make_memory('m2', 'no_java', 'true', mem_type='constraint'),
        ]
        conflicts = detect_conflicts(memories)
        assert conflicts == []


class TestFlagLowValue:
    """Test Step 4: flag low-value memories."""

    def test_flags_unused_old_memory(self):
        from app.memory_v2.dream.phase1 import flag_low_value
        memories = [
            _make_memory('m1', 'k1', 'v1', use_count=0, last_used_days_ago=90),
        ]
        flagged = flag_low_value(memories, unused_days=60, confidence_floor=0.5)
        assert 'm1' in flagged

    def test_flags_low_confidence_never_promoted(self):
        from app.memory_v2.dream.phase1 import flag_low_value
        memories = [
            _make_memory('m1', 'k1', 'v1', confidence=0.3, status='provisional'),
        ]
        flagged = flag_low_value(memories, unused_days=60, confidence_floor=0.5)
        assert 'm1' in flagged

    def test_keeps_well_used_memory(self):
        from app.memory_v2.dream.phase1 import flag_low_value
        memories = [
            _make_memory('m1', 'k1', 'v1', use_count=10, last_used_days_ago=5),
        ]
        flagged = flag_low_value(memories, unused_days=60, confidence_floor=0.5)
        assert flagged == []


class TestRunPhase1:
    """Test the full Phase 1 pipeline."""

    def test_returns_consolidation_report(self):
        from app.memory_v2.dream.phase1 import run_phase1
        from app.memory_v2.dream.config import DreamConfig
        expired_time = datetime.utcnow() - timedelta(hours=1)
        memories = [
            _make_memory('m1', 'k1', 'v1', expires_at=expired_time),
            _make_memory('m2', 'k2', 'v2', use_count=0, last_used_days_ago=90),
            _make_memory('m3', 'k3', 'v3'),
        ]
        cfg = DreamConfig()
        report = run_phase1(memories, cfg)
        assert 'm1' in report.pruned
        assert 'm2' in report.flagged_low_value
        assert len(report.all_active) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_phase1.py -v`
Expected: ImportError

- [ ] **Step 3: Write Phase 1 implementation**

```python
# app/memory_v2/dream/phase1.py
"""Phase 1: Algorithmic memory consolidation — no LLM needed."""
import logging
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

import numpy as np

from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
from app.memory_v2.dream.config import DreamConfig
from app.memory_v2.dream.report import ConsolidationReport

logger = logging.getLogger(__name__)

# Profile type uses overwrite strategy — conflicts are real
# Accumulate types (constraint, preference with different keys) aren't conflicts
OVERWRITE_TYPES = {MemoryType.PROFILE, MemoryType.TEMPORARY}


def prune_expired(memories: List[MemoryItem]) -> List[str]:
    """Step 1: Find memories that should be expired."""
    now = datetime.utcnow()
    pruned_ids = []

    for mem in memories:
        # Check explicit TTL expiry
        if mem.lifecycle.expires_at and mem.lifecycle.expires_at < now:
            pruned_ids.append(mem.id)
            continue

        # Check old provisionals with no confirmation
        if mem.status == MemoryStatus.PROVISIONAL:
            age_days = (now - mem.lifecycle.created_at).days
            if age_days > 14 and mem.lifecycle.repeated_by_user < 2 and not mem.lifecycle.explicit_confirmed:
                pruned_ids.append(mem.id)
                continue

    return pruned_ids


def merge_near_duplicates(
    memories: List[MemoryItem],
    threshold: float = 0.92,
) -> List[Tuple[str, str]]:
    """Step 2: Find near-duplicate memory pairs to merge.
    Returns list of (winner_id, loser_id) tuples.
    """
    merged = []
    consumed = set()

    # Group by type for pairwise comparison
    by_type = {}
    for mem in memories:
        if mem.id in consumed or not mem.embedding:
            continue
        by_type.setdefault(mem.type, []).append(mem)

    for mem_type, group in by_type.items():
        for i, a in enumerate(group):
            if a.id in consumed:
                continue
            for b in group[i + 1:]:
                if b.id in consumed:
                    continue
                # Must share the same normalized key
                if a.key != b.key:
                    continue
                # Compute cosine similarity
                emb_a = np.array(a.embedding, dtype=np.float32)
                emb_b = np.array(b.embedding, dtype=np.float32)
                norm_a = np.linalg.norm(emb_a)
                norm_b = np.linalg.norm(emb_b)
                if norm_a == 0 or norm_b == 0:
                    continue
                similarity = float(np.dot(emb_a, emb_b) / (norm_a * norm_b))
                if similarity >= threshold:
                    # Higher confidence wins
                    if a.confidence >= b.confidence:
                        winner, loser = a, b
                    else:
                        winner, loser = b, a
                    merged.append((winner.id, loser.id))
                    consumed.add(loser.id)

    return merged


def detect_conflicts(memories: List[MemoryItem]) -> List[List[str]]:
    """Step 3: Find groups of memories with conflicting values.
    Only flags overwrite-type memories (profile, temporary) with same key but different value.
    Accumulate types (constraints, preferences) are not conflicts.
    """
    conflicts = []
    by_key = {}

    for mem in memories:
        if mem.status != MemoryStatus.ACTIVE:
            continue
        by_key.setdefault(mem.key, []).append(mem)

    for key, group in by_key.items():
        if len(group) < 2:
            continue
        # Check if any of these are overwrite types
        overwrite_mems = [m for m in group if m.type in OVERWRITE_TYPES]
        if len(overwrite_mems) >= 2:
            # All same key, different values = conflict
            values = {str(m.value) for m in overwrite_mems}
            if len(values) > 1:
                conflicts.append([m.id for m in overwrite_mems])
            continue

        # For non-overwrite types, only flag if values are contradictory
        # (e.g., preference "concise" vs "verbose" for same key)
        if group[0].type == MemoryType.PREFERENCE:
            values = {str(m.value) for m in group}
            if len(values) > 1:
                conflicts.append([m.id for m in group])

    return conflicts


def flag_low_value(
    memories: List[MemoryItem],
    unused_days: int = 60,
    confidence_floor: float = 0.5,
) -> List[str]:
    """Step 4: Flag low-value memories for Phase 2 review."""
    now = datetime.utcnow()
    flagged = []

    for mem in memories:
        # Unused for a long time
        if mem.lifecycle.use_count == 0:
            last_touched = mem.lifecycle.last_used_at or mem.lifecycle.created_at
            days_unused = (now - last_touched).days
            if days_unused > unused_days:
                flagged.append(mem.id)
                continue

        # Low confidence, never promoted
        if mem.confidence < confidence_floor and mem.status == MemoryStatus.PROVISIONAL:
            flagged.append(mem.id)

    return flagged


def run_phase1(memories: List[MemoryItem], config: DreamConfig) -> ConsolidationReport:
    """Run the full Phase 1 algorithmic consolidation pipeline."""
    report = ConsolidationReport()

    # Step 1: Prune expired
    report.pruned = prune_expired(memories)
    pruned_set = set(report.pruned)

    # Filter out pruned for remaining steps
    remaining = [m for m in memories if m.id not in pruned_set]

    # Step 2: Merge near-duplicates
    report.merged = merge_near_duplicates(
        remaining, threshold=config.embedding_similarity_threshold
    )
    merged_losers = {loser for _, loser in report.merged}

    # Filter out merged losers for remaining steps
    remaining = [m for m in remaining if m.id not in merged_losers]

    # Step 3: Detect conflicts
    report.flagged_conflicts = detect_conflicts(remaining)

    # Step 4: Flag low-value
    report.flagged_low_value = flag_low_value(
        remaining,
        unused_days=config.low_value_unused_days,
        confidence_floor=config.low_value_confidence_floor,
    )

    # All remaining active memories for Phase 2 context
    report.all_active = [
        m for m in remaining
        if m.status in (MemoryStatus.ACTIVE, MemoryStatus.PROVISIONAL)
    ]

    logger.info(
        f"[dream] Phase 1: pruned={len(report.pruned)}, merged={len(report.merged)}, "
        f"conflicts={len(report.flagged_conflicts)}, low_value={len(report.flagged_low_value)}, "
        f"remaining={len(report.all_active)}"
    )

    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_phase1.py -v`
Expected: 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/phase1.py tests/test_dream_phase1.py
git commit -m "feat(dream): add Phase 1 algorithmic consolidation"
```

---

### Task 5: Phase 2 — Kimi LLM Consolidation

**Files:**
- Create: `app/memory_v2/dream/phase2.py`
- Create: `tests/test_dream_phase2.py`

- [ ] **Step 1: Write failing tests for Phase 2**

```python
# tests/test_dream_phase2.py
"""Tests for Phase 2 Kimi LLM consolidation."""
import pytest
import json
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class TestBuildPrompt:
    """Test Phase 2 prompt construction."""

    def test_builds_prompt_with_conflicts(self):
        from app.memory_v2.dream.phase2 import build_consolidation_prompt
        from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
        memories = [
            MemoryItem(id='m1', key='timezone', value='PST', type=MemoryType.PROFILE,
                       confidence=0.8, status=MemoryStatus.ACTIVE),
            MemoryItem(id='m2', key='timezone', value='GMT', type=MemoryType.PROFILE,
                       confidence=0.9, status=MemoryStatus.ACTIVE),
        ]
        prompt = build_consolidation_prompt(
            flagged_conflicts=[['m1', 'm2']],
            flagged_low_value=[],
            all_active=memories,
            memories_by_id={'m1': memories[0], 'm2': memories[1]},
        )
        assert 'timezone' in prompt
        assert 'PST' in prompt
        assert 'GMT' in prompt
        assert 'resolve_conflicts' in prompt

    def test_builds_prompt_with_low_value(self):
        from app.memory_v2.dream.phase2 import build_consolidation_prompt
        from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
        mem = MemoryItem(id='m1', key='old_project', value='legacy app',
                         type=MemoryType.PROJECT, confidence=0.6,
                         status=MemoryStatus.ACTIVE)
        prompt = build_consolidation_prompt(
            flagged_conflicts=[],
            flagged_low_value=['m1'],
            all_active=[mem],
            memories_by_id={'m1': mem},
        )
        assert 'old_project' in prompt
        assert 'prune' in prompt

    def test_prompt_within_token_budget(self):
        from app.memory_v2.dream.phase2 import build_consolidation_prompt
        from app.memory_v2.schema import MemoryItem, MemoryType, MemoryStatus
        # Create 50 memories to test budget capping
        memories = []
        by_id = {}
        for i in range(50):
            mem = MemoryItem(id=f'm{i}', key=f'key_{i}', value=f'value_{i}' * 20,
                             type=MemoryType.PREFERENCE, confidence=0.7,
                             status=MemoryStatus.ACTIVE)
            memories.append(mem)
            by_id[f'm{i}'] = mem
        prompt = build_consolidation_prompt(
            flagged_conflicts=[],
            flagged_low_value=[],
            all_active=memories,
            memories_by_id=by_id,
            token_budget=4000,
        )
        # Rough estimate: 1 token ~4 chars. 4000 tokens ~16000 chars
        assert len(prompt) < 20000


class TestParseKimiResponse:
    """Test parsing Kimi's JSON response."""

    def test_parses_valid_response(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        raw = json.dumps({
            "resolve_conflicts": [
                {"keep_id": "m1", "supersede_ids": ["m2"], "reason": "newer"}
            ],
            "prune": [
                {"memory_id": "m3", "reason": "stale"}
            ],
            "promote": [
                {"memory_id": "m4", "reason": "corroborated"}
            ],
            "synthesize": [
                {"type": "preference", "key": "style", "value": "iterative",
                 "evidence_ids": ["m5", "m6"], "confidence": 0.75}
            ],
        })
        result = parse_kimi_response(raw)
        assert len(result['resolve_conflicts']) == 1
        assert len(result['prune']) == 1
        assert len(result['promote']) == 1
        assert len(result['synthesize']) == 1

    def test_handles_malformed_json(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        result = parse_kimi_response("not json at all")
        assert result['resolve_conflicts'] == []
        assert result['prune'] == []
        assert result['promote'] == []
        assert result['synthesize'] == []

    def test_handles_partial_response(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        raw = json.dumps({"resolve_conflicts": [], "prune": []})
        result = parse_kimi_response(raw)
        assert result['promote'] == []
        assert result['synthesize'] == []

    def test_caps_syntheses(self):
        from app.memory_v2.dream.phase2 import parse_kimi_response
        raw = json.dumps({
            "resolve_conflicts": [],
            "prune": [],
            "promote": [],
            "synthesize": [
                {"type": "preference", "key": f"k{i}", "value": f"v{i}",
                 "evidence_ids": [], "confidence": 0.7}
                for i in range(10)
            ],
        })
        result = parse_kimi_response(raw, max_syntheses=3)
        assert len(result['synthesize']) == 3


class TestCallKimi:
    """Test Kimi API call."""

    @patch('app.memory_v2.dream.phase2.requests.post')
    def test_call_kimi_success(self, mock_post):
        from app.memory_v2.dream.phase2 import call_kimi
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '{"resolve_conflicts":[],"prune":[],"promote":[],"synthesize":[]}'}}]
        }
        mock_post.return_value = mock_response

        result = call_kimi("test prompt")
        assert result is not None
        assert 'resolve_conflicts' in result

    @patch('app.memory_v2.dream.phase2.requests.post')
    def test_call_kimi_failure_returns_none(self, mock_post):
        from app.memory_v2.dream.phase2 import call_kimi
        mock_post.side_effect = Exception("connection timeout")
        result = call_kimi("test prompt")
        assert result is None


class TestShouldRunPhase2:
    """Test Phase 2 trigger logic."""

    def test_runs_when_conflicts_flagged(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport(flagged_conflicts=[['m1', 'm2']])
        assert should_run_phase2(report, pattern_threshold=15) is True

    def test_runs_when_low_value_flagged(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport(flagged_low_value=['m1'])
        assert should_run_phase2(report, pattern_threshold=15) is True

    def test_runs_when_many_active(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        from app.memory_v2.schema import MemoryItem
        report = ConsolidationReport(all_active=[MemoryItem() for _ in range(20)])
        assert should_run_phase2(report, pattern_threshold=15) is True

    def test_skips_when_nothing_to_do(self):
        from app.memory_v2.dream.phase2 import should_run_phase2
        from app.memory_v2.dream.report import ConsolidationReport
        report = ConsolidationReport(all_active=[])
        assert should_run_phase2(report, pattern_threshold=15) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_phase2.py -v`
Expected: ImportError

- [ ] **Step 3: Write Phase 2 implementation**

```python
# app/memory_v2/dream/phase2.py
"""Phase 2: Kimi LLM consolidation — conflict resolution, synthesis, promotion."""
import json
import logging
from typing import Dict, List, Any, Optional

import requests

from app.config import Config
from app.memory_v2.schema import MemoryItem
from app.memory_v2.dream.report import ConsolidationReport

logger = logging.getLogger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """You are a memory consolidation agent. You review a user's stored memories and make decisions to improve quality.

You will receive:
1. CONFLICTING memories that need resolution
2. LOW-VALUE memories that may need pruning
3. ALL ACTIVE memories for pattern synthesis

Respond with ONLY valid JSON in this exact format:
{
  "resolve_conflicts": [
    {"keep_id": "id_to_keep", "supersede_ids": ["id1", "id2"], "reason": "explanation"}
  ],
  "prune": [
    {"memory_id": "id_to_remove", "reason": "explanation"}
  ],
  "promote": [
    {"memory_id": "id_to_promote", "reason": "explanation"}
  ],
  "synthesize": [
    {"type": "preference|project|profile", "key": "pattern_name", "value": "description of pattern", "evidence_ids": ["id1", "id2"], "confidence": 0.75}
  ]
}

Rules:
- For conflicts: keep the memory with stronger evidence (newer, higher confidence, more usage)
- For pruning: only prune memories that are clearly outdated or irrelevant
- For promotion: only promote provisional memories with corroborating evidence from other memories
- For synthesis: only create new memories when you see a clear pattern across 3+ existing memories
- Set synthesis confidence between 0.5 and 0.8
- Be conservative — when in doubt, don't act"""


def should_run_phase2(report: ConsolidationReport, pattern_threshold: int = 15) -> bool:
    """Determine if Phase 2 should run."""
    if report.flagged_conflicts:
        return True
    if report.flagged_low_value:
        return True
    if len(report.all_active) >= pattern_threshold:
        return True
    return False


def build_consolidation_prompt(
    flagged_conflicts: List[List[str]],
    flagged_low_value: List[str],
    all_active: List[MemoryItem],
    memories_by_id: Dict[str, MemoryItem],
    token_budget: int = 4000,
) -> str:
    """Build the prompt for Kimi with flagged items and memory context."""
    sections = []

    # Section 1: Conflicts
    if flagged_conflicts:
        lines = ["## CONFLICTING MEMORIES (resolve these)"]
        for i, group in enumerate(flagged_conflicts):
            lines.append(f"\nConflict {i + 1}:")
            for mid in group:
                mem = memories_by_id.get(mid)
                if mem:
                    lines.append(
                        f"  - id={mem.id}, key={mem.key}, value={mem.value}, "
                        f"type={mem.type.value}, confidence={mem.confidence:.2f}, "
                        f"updated={mem.lifecycle.updated_at.isoformat() if mem.lifecycle.updated_at else 'unknown'}"
                    )
        sections.append("\n".join(lines))

    # Section 2: Low-value
    if flagged_low_value:
        lines = ["## LOW-VALUE MEMORIES (consider pruning)"]
        for mid in flagged_low_value:
            mem = memories_by_id.get(mid)
            if mem:
                lines.append(
                    f"  - id={mem.id}, key={mem.key}, value={mem.value}, "
                    f"type={mem.type.value}, confidence={mem.confidence:.2f}, "
                    f"use_count={mem.lifecycle.use_count}, "
                    f"last_used={mem.lifecycle.last_used_at.isoformat() if mem.lifecycle.last_used_at else 'never'}"
                )
        sections.append("\n".join(lines))

    # Section 3: All active (for pattern synthesis)
    lines = ["## ALL ACTIVE MEMORIES (look for patterns to synthesize)"]
    # Budget: estimate ~20 chars per memory summary line, ~4 chars per token
    max_memories = min(len(all_active), (token_budget * 4) // 80)
    for mem in all_active[:max_memories]:
        lines.append(f"  - [{mem.type.value}] {mem.key}={mem.value} (conf={mem.confidence:.2f})")
    if len(all_active) > max_memories:
        lines.append(f"  ... and {len(all_active) - max_memories} more memories")
    sections.append("\n".join(lines))

    return "\n\n".join(sections)


def call_kimi(prompt: str) -> Optional[str]:
    """Call Kimi/Moonshot API for consolidation. Returns raw response text or None on failure."""
    api_key = Config.MOONSHOT_API_KEY
    api_url = Config.MOONSHOT_API_URL

    if not api_key:
        logger.warning("[dream] Moonshot API key not configured, skipping Phase 2")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "kimi-latest",
        "messages": [
            {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"[dream] Kimi API call failed: {e}")
        return None


def parse_kimi_response(
    raw: str,
    max_syntheses: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """Parse Kimi's JSON response into structured decisions."""
    empty = {
        'resolve_conflicts': [],
        'prune': [],
        'promote': [],
        'synthesize': [],
    }

    try:
        # Handle markdown code blocks if Kimi wraps response
        text = raw.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[dream] Failed to parse Kimi response: {e}")
        return empty

    result = {}
    for key in empty:
        result[key] = data.get(key, [])
        if not isinstance(result[key], list):
            result[key] = []

    # Cap syntheses
    result['synthesize'] = result['synthesize'][:max_syntheses]

    return result


def run_phase2(
    report: ConsolidationReport,
    memories_by_id: Dict[str, MemoryItem],
    config_token_budget: int = 4000,
    config_max_syntheses: int = 3,
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Run Phase 2 Kimi consolidation. Returns parsed decisions or None on failure."""
    prompt = build_consolidation_prompt(
        flagged_conflicts=report.flagged_conflicts,
        flagged_low_value=report.flagged_low_value,
        all_active=report.all_active,
        memories_by_id=memories_by_id,
        token_budget=config_token_budget,
    )

    raw_response = call_kimi(prompt)
    if raw_response is None:
        return None

    return parse_kimi_response(raw_response, max_syntheses=config_max_syntheses)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_phase2.py -v`
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/phase2.py tests/test_dream_phase2.py
git commit -m "feat(dream): add Phase 2 Kimi LLM consolidation"
```

---

### Task 6: Executor — Batch Write, Snapshots, Undo

**Files:**
- Create: `app/memory_v2/dream/executor.py`
- Create: `tests/test_dream_executor.py`

- [ ] **Step 1: Write failing tests for executor**

```python
# tests/test_dream_executor.py
"""Tests for dream executor — batch writes, snapshots, undo."""
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta


def _mock_store(memories=None):
    """Create a mock MemoryStoreV2."""
    store = MagicMock()
    if memories:
        store.get.side_effect = lambda mid: next((m for m in memories if m.id == mid), None)
    else:
        store.get.return_value = None
    store.update.return_value = True
    store.supersede.return_value = True
    store.save.return_value = True
    store.promote_provisional.return_value = True
    return store


class TestExecutePhase1:
    """Test Phase 1 action execution."""

    def test_executes_prune(self):
        from app.memory_v2.dream.executor import execute_phase1_actions
        from app.memory_v2.schema import MemoryItem, MemoryStatus
        mem = MemoryItem(id='m1', key='k1', value='v1')
        store = _mock_store([mem])
        actions = execute_phase1_actions(
            store=store,
            pruned_ids=['m1'],
            merged_pairs=[],
        )
        assert len(actions) == 1
        assert actions[0].action_type == 'prune'
        assert actions[0].memory_id == 'm1'
        assert actions[0].before_snapshot is not None
        store.update.assert_called_once()

    def test_executes_merge(self):
        from app.memory_v2.dream.executor import execute_phase1_actions
        from app.memory_v2.schema import MemoryItem
        winner = MemoryItem(id='m1', key='style', value='concise', user_id='u1')
        loser = MemoryItem(id='m2', key='style', value='brief', user_id='u1')
        store = _mock_store([winner, loser])
        actions = execute_phase1_actions(
            store=store,
            pruned_ids=[],
            merged_pairs=[('m1', 'm2')],
        )
        assert len(actions) == 1
        assert actions[0].action_type == 'merge'
        assert actions[0].memory_id == 'm1'
        assert 'm2' in actions[0].related_ids


class TestExecutePhase2:
    """Test Phase 2 decision execution."""

    def test_executes_conflict_resolution(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem, MemoryType
        m1 = MemoryItem(id='m1', key='tz', value='PST', type=MemoryType.PROFILE, user_id='u1')
        m2 = MemoryItem(id='m2', key='tz', value='GMT', type=MemoryType.PROFILE, user_id='u1')
        store = _mock_store([m1, m2])
        decisions = {
            'resolve_conflicts': [{'keep_id': 'm2', 'supersede_ids': ['m1'], 'reason': 'newer'}],
            'prune': [],
            'promote': [],
            'synthesize': [],
        }
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'supersede' for a in actions)

    def test_executes_prune(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem
        mem = MemoryItem(id='m1', key='old', value='stale', user_id='u1')
        store = _mock_store([mem])
        decisions = {
            'resolve_conflicts': [],
            'prune': [{'memory_id': 'm1', 'reason': 'outdated'}],
            'promote': [],
            'synthesize': [],
        }
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'prune' for a in actions)

    def test_executes_promote(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem, MemoryStatus
        mem = MemoryItem(id='m1', key='role', value='engineer',
                         status=MemoryStatus.PROVISIONAL, user_id='u1')
        store = _mock_store([mem])
        decisions = {
            'resolve_conflicts': [],
            'prune': [],
            'promote': [{'memory_id': 'm1', 'reason': 'corroborated'}],
            'synthesize': [],
        }
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'promote' for a in actions)
        store.promote_provisional.assert_called_with('m1')

    def test_executes_synthesize(self):
        from app.memory_v2.dream.executor import execute_phase2_actions
        from app.memory_v2.schema import MemoryItem
        store = _mock_store()
        decisions = {
            'resolve_conflicts': [],
            'prune': [],
            'promote': [],
            'synthesize': [{
                'type': 'preference',
                'key': 'iteration_style',
                'value': 'Prefers iterative design',
                'evidence_ids': ['m1', 'm2'],
                'confidence': 0.75,
            }],
        }
        actions = execute_phase2_actions(store=store, decisions=decisions, user_id='u1')
        assert any(a.action_type == 'synthesize' for a in actions)
        store.save.assert_called_once()


class TestSaveDreamLog:
    """Test dream log persistence."""

    def test_saves_log_to_firestore(self):
        from app.memory_v2.dream.executor import save_dream_log
        from app.memory_v2.dream.report import DreamLog
        mock_db = MagicMock()
        log = DreamLog(
            user_id='u1',
            started_at=datetime(2026, 4, 4, 3, 0),
            completed_at=datetime(2026, 4, 4, 3, 1),
        )
        save_dream_log(db=mock_db, dream_log=log)
        mock_db.collection.assert_called()


class TestUndoDream:
    """Test undo capability."""

    def test_restores_snapshots(self):
        from app.memory_v2.dream.executor import undo_dream_log
        from app.memory_v2.dream.report import DreamLog, DreamAction
        mock_db = MagicMock()
        store = _mock_store()
        log = DreamLog(
            user_id='u1',
            started_at=datetime(2026, 4, 4, 3, 0),
            completed_at=datetime(2026, 4, 4, 3, 1),
            phase1_actions=[
                DreamAction(
                    action_type='prune', memory_id='m1', reason='expired', phase=1,
                    before_snapshot={'id': 'm1', 'status': 'active', 'key': 'k1', 'value': 'v1'},
                ),
            ],
        )
        result = undo_dream_log(db=mock_db, store=store, dream_log=log)
        assert result is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_executor.py -v`
Expected: ImportError

- [ ] **Step 3: Write executor implementation**

```python
# app/memory_v2/dream/executor.py
"""Dream executor: batch writes, snapshots, dream log persistence, undo."""
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional

from app.memory_v2.schema import (
    MemoryItem, MemoryType, MemoryStatus, MemoryEvidence, MemoryLifecycle,
    create_memory,
)
from app.memory_v2.dream.report import DreamAction, DreamLog

logger = logging.getLogger(__name__)


def _snapshot(mem: Optional[MemoryItem]) -> Optional[Dict[str, Any]]:
    """Create a before-snapshot of a memory for undo."""
    if mem is None:
        return None
    return {
        'id': mem.id,
        'status': mem.status.value if hasattr(mem.status, 'value') else str(mem.status),
        'key': mem.key,
        'value': mem.value,
        'confidence': mem.confidence,
        'type': mem.type.value if hasattr(mem.type, 'value') else str(mem.type),
    }


def execute_phase1_actions(
    store,
    pruned_ids: List[str],
    merged_pairs: List[Tuple[str, str]],
) -> List[DreamAction]:
    """Execute Phase 1 direct actions and return DreamAction records."""
    actions = []

    # Prune
    for mid in pruned_ids:
        mem = store.get(mid)
        snapshot = _snapshot(mem)
        try:
            store.update(mid, {'status': MemoryStatus.EXPIRED.value})
            actions.append(DreamAction(
                action_type='prune', memory_id=mid,
                reason='Expired TTL or stale provisional',
                phase=1, before_snapshot=snapshot,
            ))
        except Exception as e:
            logger.warning(f"[dream] Failed to prune {mid}: {e}")

    # Merge
    for winner_id, loser_id in merged_pairs:
        winner = store.get(winner_id)
        loser = store.get(loser_id)
        snapshot = _snapshot(loser)
        try:
            # Update winner with combined usage stats
            if winner and loser:
                combined_use = (winner.lifecycle.use_count or 0) + (loser.lifecycle.use_count or 0)
                higher_repeated = max(
                    winner.lifecycle.repeated_by_user or 0,
                    loser.lifecycle.repeated_by_user or 0,
                )
                store.update(winner_id, {
                    'lifecycle.use_count': combined_use,
                    'lifecycle.repeated_by_user': higher_repeated,
                    'lifecycle.updated_at': datetime.utcnow(),
                })
            # Supersede loser
            store.update(loser_id, {
                'status': MemoryStatus.SUPERSEDED.value,
                'superseded_by': winner_id,
            })
            actions.append(DreamAction(
                action_type='merge', memory_id=winner_id,
                reason=f'Merged near-duplicate',
                phase=1, before_snapshot=snapshot,
                related_ids=[loser_id],
            ))
        except Exception as e:
            logger.warning(f"[dream] Failed to merge {winner_id}/{loser_id}: {e}")

    return actions


def execute_phase2_actions(
    store,
    decisions: Dict[str, List[Dict[str, Any]]],
    user_id: str,
) -> List[DreamAction]:
    """Execute Phase 2 Kimi decisions and return DreamAction records."""
    actions = []

    # Resolve conflicts
    for resolution in decisions.get('resolve_conflicts', []):
        keep_id = resolution.get('keep_id')
        supersede_ids = resolution.get('supersede_ids', [])
        reason = resolution.get('reason', 'conflict resolution')
        for sid in supersede_ids:
            mem = store.get(sid)
            snapshot = _snapshot(mem)
            try:
                store.update(sid, {
                    'status': MemoryStatus.SUPERSEDED.value,
                    'superseded_by': keep_id,
                })
                actions.append(DreamAction(
                    action_type='supersede', memory_id=sid,
                    reason=reason, phase=2,
                    before_snapshot=snapshot, related_ids=[keep_id],
                ))
            except Exception as e:
                logger.warning(f"[dream] Failed to supersede {sid}: {e}")

    # Prune
    for prune_decision in decisions.get('prune', []):
        mid = prune_decision.get('memory_id')
        reason = prune_decision.get('reason', 'low value')
        mem = store.get(mid)
        snapshot = _snapshot(mem)
        try:
            store.update(mid, {'status': MemoryStatus.EXPIRED.value})
            actions.append(DreamAction(
                action_type='prune', memory_id=mid,
                reason=reason, phase=2, before_snapshot=snapshot,
            ))
        except Exception as e:
            logger.warning(f"[dream] Failed to prune {mid}: {e}")

    # Promote
    for promote_decision in decisions.get('promote', []):
        mid = promote_decision.get('memory_id')
        reason = promote_decision.get('reason', 'corroborated')
        mem = store.get(mid)
        snapshot = _snapshot(mem)
        try:
            store.promote_provisional(mid)
            actions.append(DreamAction(
                action_type='promote', memory_id=mid,
                reason=reason, phase=2, before_snapshot=snapshot,
            ))
        except Exception as e:
            logger.warning(f"[dream] Failed to promote {mid}: {e}")

    # Synthesize
    for synth in decisions.get('synthesize', []):
        try:
            type_str = synth.get('type', 'preference')
            type_map = {
                'profile': MemoryType.PROFILE,
                'preference': MemoryType.PREFERENCE,
                'project': MemoryType.PROJECT,
                'constraint': MemoryType.CONSTRAINT,
            }
            mem_type = type_map.get(type_str, MemoryType.PREFERENCE)
            confidence = min(0.8, float(synth.get('confidence', 0.7)))

            new_mem = create_memory(
                user_id=user_id,
                memory_type=mem_type,
                key=synth.get('key', 'unknown'),
                value=synth.get('value', ''),
                confidence=confidence,
                quote='Synthesized by dream consolidation',
                provisional=True,
            )
            new_mem.evidence.source = 'inferred'
            new_mem.evidence.extraction_method = 'dream_synthesis'

            store.save(new_mem, bypass_source_check=True)
            actions.append(DreamAction(
                action_type='synthesize', memory_id=new_mem.id,
                reason=f"Pattern from {len(synth.get('evidence_ids', []))} memories",
                phase=2,
                related_ids=synth.get('evidence_ids', []),
            ))
        except Exception as e:
            logger.warning(f"[dream] Failed to synthesize: {e}")

    return actions


def save_dream_log(db, dream_log: DreamLog) -> Optional[str]:
    """Save a dream log to Firestore. Returns the document ID."""
    try:
        log_ref = (
            db.collection('users')
            .document(dream_log.user_id)
            .collection('dream_logs')
            .document()
        )
        log_ref.set(dream_log.to_dict())
        return log_ref.id
    except Exception as e:
        logger.error(f"[dream] Failed to save dream log: {e}")
        return None


def undo_dream_log(db, store, dream_log: DreamLog) -> bool:
    """Undo all actions from a dream log by restoring before_snapshots."""
    all_actions = dream_log.phase1_actions + dream_log.phase2_actions
    restored = 0

    for action in reversed(all_actions):
        if action.before_snapshot is None:
            # Synthesized memories — delete them
            if action.action_type == 'synthesize':
                try:
                    store.delete(action.memory_id, hard_delete=True)
                    restored += 1
                except Exception as e:
                    logger.warning(f"[dream] Undo: failed to delete synthesized {action.memory_id}: {e}")
            continue

        snapshot = action.before_snapshot
        try:
            store.update(action.memory_id, {
                'status': snapshot.get('status', 'active'),
                'superseded_by': None,
            })
            restored += 1
        except Exception as e:
            logger.warning(f"[dream] Undo: failed to restore {action.memory_id}: {e}")

    logger.info(f"[dream] Undo: restored {restored}/{len(all_actions)} actions")
    return restored > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_executor.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/executor.py tests/test_dream_executor.py
git commit -m "feat(dream): add executor with batch writes, snapshots, and undo"
```

---

### Task 7: Dream Orchestrator — Tie It All Together

**Files:**
- Modify: `app/memory_v2/dream/__init__.py`
- Create: `tests/test_dream_orchestrator.py`

- [ ] **Step 1: Write failing test for the orchestrator**

```python
# tests/test_dream_orchestrator.py
"""Tests for dream orchestrator — the main entry point."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta


class TestMaybeConsolidate:
    """Test the main maybe_consolidate entry point."""

    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_skips_when_disabled(self, mock_state):
        from app.memory_v2.dream import maybe_consolidate
        mock_db = MagicMock()
        prefs = {'dream_enabled': False}
        result = maybe_consolidate(db=mock_db, user_id='u1', user_prefs=prefs)
        assert result is False
        mock_state.assert_not_called()

    @patch('app.memory_v2.dream.gates.get_dream_state')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    def test_skips_when_time_gate_fails(self, mock_time, mock_state):
        from app.memory_v2.dream import maybe_consolidate
        mock_db = MagicMock()
        mock_state.return_value = {'last_consolidated_at': datetime.utcnow()}
        mock_time.return_value = False
        result = maybe_consolidate(db=mock_db, user_id='u1')
        assert result is False

    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_runs_full_phase1_only(self, mock_state, mock_time, mock_sessions,
                                    mock_lock, mock_should_p2, mock_phase1,
                                    mock_exec_p1, mock_save_log,
                                    mock_update_state, mock_release):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = []

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = False
        mock_phase1.return_value = ConsolidationReport(pruned=['m1'])
        mock_exec_p1.return_value = []
        mock_save_log.return_value = 'log_123'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_phase1.assert_called_once()
        mock_save_log.assert_called_once()
        mock_update_state.assert_called_once()
        mock_release.assert_called_once()

    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase2_actions')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase2.run_phase2')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_runs_phase1_and_phase2(self, mock_state, mock_time, mock_sessions,
                                     mock_lock, mock_phase1, mock_should_p2,
                                     mock_run_p2, mock_exec_p1, mock_exec_p2,
                                     mock_save_log, mock_update_state, mock_release):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = []

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = True
        mock_phase1.return_value = ConsolidationReport(
            flagged_conflicts=[['m1', 'm2']],
        )
        mock_run_p2.return_value = {
            'resolve_conflicts': [{'keep_id': 'm2', 'supersede_ids': ['m1'], 'reason': 'newer'}],
            'prune': [], 'promote': [], 'synthesize': [],
        }
        mock_exec_p1.return_value = []
        mock_exec_p2.return_value = []
        mock_save_log.return_value = 'log_456'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_run_p2.assert_called_once()
        mock_exec_p2.assert_called_once()


class TestNotificationFlag:
    """Test pending notification flag."""

    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_sets_notification_on_success(self, mock_state, mock_time, mock_sessions,
                                           mock_lock, mock_should_p2, mock_phase1,
                                           mock_exec_p1, mock_save_log,
                                           mock_update_state, mock_release):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport, DreamAction

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = []

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = False
        mock_phase1.return_value = ConsolidationReport(pruned=['m1'])
        mock_exec_p1.return_value = [
            DreamAction(action_type='prune', memory_id='m1', reason='expired', phase=1)
        ]
        mock_save_log.return_value = 'log_789'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        # Check that update_dream_state was called with notification data
        call_args = mock_update_state.call_args
        updates = call_args[1].get('updates') or call_args[0][2]
        assert 'pending_notification' in updates
        assert updates['pending_notification']['pruned'] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_orchestrator.py -v`
Expected: ImportError

- [ ] **Step 3: Write the orchestrator in __init__.py**

```python
# app/memory_v2/dream/__init__.py
"""Dream System: Background memory consolidation.

Public API: maybe_consolidate() — call after each chat response in a background thread.
"""
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from app.memory_v2.dream.config import DreamConfig, get_dream_config
from app.memory_v2.dream.report import ConsolidationReport, DreamAction, DreamLog
from app.memory_v2.dream import gates
from app.memory_v2.dream import phase1
from app.memory_v2.dream import phase2
from app.memory_v2.dream import executor
from app.memory_v2.store import MemoryStoreV2

logger = logging.getLogger(__name__)


def maybe_consolidate(
    db,
    user_id: str,
    user_prefs: Optional[Dict[str, Any]] = None,
) -> bool:
    """Main entry point: check gates and run consolidation if due.
    
    Call this in a background thread after each chat response.
    Returns True if consolidation ran, False if skipped.
    """
    config = get_dream_config(user_prefs)

    # Pre-gate: is dream enabled?
    if not config.enabled:
        return False

    # Gate 1: Time gate
    dream_state = gates.get_dream_state(db, user_id)
    last_at = dream_state.get('last_consolidated_at')
    if not gates.check_time_gate(last_at, config):
        return False

    # Gate 2: Session count gate
    if not gates.check_session_count_gate(db, user_id, last_at, config):
        return False

    # Gate 3: Lock gate
    if not gates.try_acquire_lock(db, user_id, config=config):
        return False

    # All gates passed — run consolidation
    try:
        return _run_consolidation(db, user_id, config)
    except Exception as e:
        logger.error(f"[dream] Consolidation failed for {user_id}: {e}")
        gates.release_lock(db, user_id)
        return False


def _run_consolidation(db, user_id: str, config: DreamConfig) -> bool:
    """Execute the full consolidation pipeline."""
    started_at = datetime.utcnow()
    dream_log = DreamLog(user_id=user_id, started_at=started_at)

    store = MemoryStoreV2(user_id)

    # Load all memories
    all_memories = store.get_all(include_expired=False)

    # Pre-check: enough memories to consolidate?
    active_count = sum(1 for m in all_memories if m.status.value in ('active', 'provisional'))
    if active_count < config.min_active_memories:
        gates.release_lock(db, user_id)
        return False

    # Phase 1: Algorithmic
    report = phase1.run_phase1(all_memories, config)

    # Execute Phase 1 actions
    p1_actions = executor.execute_phase1_actions(
        store=store,
        pruned_ids=report.pruned,
        merged_pairs=report.merged,
    )
    dream_log.phase1_actions = p1_actions

    # Phase 2: Kimi LLM (conditional)
    p2_actions = []
    if phase2.should_run_phase2(report, config.pattern_synthesis_threshold):
        memories_by_id = {m.id: m for m in report.all_active}
        p2_decisions = phase2.run_phase2(
            report=report,
            memories_by_id=memories_by_id,
            config_token_budget=config.kimi_token_budget,
            config_max_syntheses=config.max_syntheses_per_cycle,
        )
        if p2_decisions is not None:
            dream_log.kimi_raw_response = str(p2_decisions)
            p2_actions = executor.execute_phase2_actions(
                store=store,
                decisions=p2_decisions,
                user_id=user_id,
            )
        else:
            dream_log.errors.append('Phase 2 Kimi call failed')

    dream_log.phase2_actions = p2_actions
    dream_log.completed_at = datetime.utcnow()

    # Save dream log
    executor.save_dream_log(db, dream_log)

    # Build notification summary
    summary = dream_log.summary()
    notification = {k: v for k, v in summary.items() if v > 0 and k != 'total_actions'}

    # Update dream state
    gates.update_dream_state(db, user_id, {
        'last_consolidated_at': datetime.utcnow(),
        'pending_notification': notification if notification else None,
    })

    # Release lock
    gates.release_lock(db, user_id)

    logger.info(f"[dream] Consolidation complete for {user_id}: {summary}")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_orchestrator.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/__init__.py tests/test_dream_orchestrator.py
git commit -m "feat(dream): add orchestrator — main maybe_consolidate entry point"
```

---

### Task 8: Chat Routes Integration

**Files:**
- Modify: `app/blueprints/chat_routes.py`

- [ ] **Step 1: Read the insertion point in chat_routes.py**

Read lines 1880-1950 of `app/blueprints/chat_routes.py` to find the exact location where memory_future is submitted.

- [ ] **Step 2: Add dream import at the top of chat_routes.py**

Find the imports section and add:

```python
from app.memory_v2.dream import maybe_consolidate as dream_maybe_consolidate
```

- [ ] **Step 3: Add dream trigger after memory post_turn**

After the block where `memory_future` and `reflection_future` are submitted (around line 1915), add:

```python
    # Dream: background memory consolidation (fire-and-forget)
    if not guest_mode and firestore_available:
        try:
            from app.config import db as firestore_db
            user_prefs_for_dream = session.get('user', {}).get('preferences', {})
            import threading
            dream_thread = threading.Thread(
                target=dream_maybe_consolidate,
                args=(firestore_db, user_id, user_prefs_for_dream),
                daemon=True,
            )
            dream_thread.start()
        except Exception as e:
            logger.debug(f"Dream trigger skipped: {e}")
```

- [ ] **Step 4: Add dream notification to chat response**

Find where the response JSON is built (the `jsonify()` call) and add the dream notification check:

```python
    # Check for pending dream notification
    dream_notification = None
    if not guest_mode and firestore_available:
        try:
            from app.memory_v2.dream.gates import get_dream_state, update_dream_state
            from app.config import db as firestore_db
            ds = get_dream_state(firestore_db, user_id)
            if ds.get('pending_notification'):
                dream_notification = ds['pending_notification']
                update_dream_state(firestore_db, user_id, {'pending_notification': None})
        except Exception:
            pass
```

Add `'dream_notification': dream_notification` to the response dict.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/chat_routes.py
git commit -m "feat(dream): integrate dream trigger into chat pipeline"
```

---

### Task 9: Memory Activity Routes

**Files:**
- Modify: `app/blueprints/memory_routes.py`

- [ ] **Step 1: Read existing memory_routes.py**

Read the file to find the blueprint setup and existing route patterns.

- [ ] **Step 2: Add activity log route**

Add to `memory_routes.py`:

```python
@memory_bp.route('/user/memory-activity', methods=['GET'])
def get_memory_activity():
    """Get dream consolidation activity log for the current user."""
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    user_id = user['uid']
    try:
        logs_ref = (
            db.collection('users')
            .document(user_id)
            .collection('dream_logs')
            .order_by('started_at', direction='DESCENDING')
            .limit(20)
        )
        logs = []
        for doc in logs_ref.stream():
            log_data = doc.to_dict()
            log_data['id'] = doc.id
            logs.append(log_data)
        return jsonify({'logs': logs})
    except Exception as e:
        logger.error(f"Failed to get dream activity: {e}")
        return jsonify({'error': 'Failed to load activity'}), 500
```

- [ ] **Step 3: Add undo route**

Add to `memory_routes.py`:

```python
@memory_bp.route('/user/memory-activity/<log_id>/undo', methods=['POST'])
def undo_memory_activity(log_id):
    """Undo a dream consolidation cycle."""
    user = session.get('user')
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401

    user_id = user['uid']
    try:
        from app.memory_v2.dream.report import DreamLog, DreamAction
        from app.memory_v2.dream.executor import undo_dream_log
        from app.memory_v2.store import MemoryStoreV2
        from datetime import datetime, timedelta

        # Load dream log
        log_ref = (
            db.collection('users')
            .document(user_id)
            .collection('dream_logs')
            .document(log_id)
        )
        snapshot = log_ref.get()
        if not snapshot.exists:
            return jsonify({'error': 'Log not found'}), 404

        log_data = snapshot.to_dict()

        # Check undo window (7 days)
        started = log_data.get('started_at')
        if started and hasattr(started, 'timestamp'):
            started = datetime.fromtimestamp(started.timestamp())
        if started and (datetime.utcnow() - started).days > 7:
            return jsonify({'error': 'Undo window expired (7 days)'}), 400

        # Reconstruct DreamLog
        def _actions_from_dicts(action_dicts):
            return [
                DreamAction(
                    action_type=a.get('action_type', ''),
                    memory_id=a.get('memory_id', ''),
                    reason=a.get('reason', ''),
                    phase=a.get('phase', 0),
                    before_snapshot=a.get('before_snapshot'),
                    related_ids=a.get('related_ids', []),
                )
                for a in (action_dicts or [])
            ]

        dream_log = DreamLog(
            user_id=user_id,
            started_at=started or datetime.utcnow(),
            phase1_actions=_actions_from_dicts(log_data.get('phase1_actions')),
            phase2_actions=_actions_from_dicts(log_data.get('phase2_actions')),
        )

        store = MemoryStoreV2(user_id)
        success = undo_dream_log(db=db, store=store, dream_log=dream_log)

        if success:
            # Mark log as undone
            log_ref.update({'undone': True, 'undone_at': datetime.utcnow()})
            return jsonify({'success': True, 'message': 'Dream actions undone'})
        else:
            return jsonify({'error': 'Nothing to undo'}), 400

    except Exception as e:
        logger.error(f"Failed to undo dream log {log_id}: {e}")
        return jsonify({'error': 'Undo failed'}), 500
```

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/memory_routes.py
git commit -m "feat(dream): add memory activity log and undo routes"
```

---

### Task 10: Frontend Toast Notification

**Files:**
- Modify: `app/static/js/script.js`

- [ ] **Step 1: Read the chat response handler in script.js**

Search for where the chat response JSON is processed after a successful fetch — look for where `response.json()` is parsed and the assistant message is displayed.

- [ ] **Step 2: Add dream toast check to response handler**

In the response handler, after processing the main response, add:

```javascript
// Dream notification toast
if (data.dream_notification) {
    const dn = data.dream_notification;
    const parts = [];
    if (dn.pruned) parts.push(`${dn.pruned} pruned`);
    if (dn.merged) parts.push(`${dn.merged} merged`);
    if (dn.promoted) parts.push(`${dn.promoted} promoted`);
    if (dn.synthesized) parts.push(`${dn.synthesized} new insight${dn.synthesized > 1 ? 's' : ''}`);
    if (dn.superseded) parts.push(`${dn.superseded} resolved`);
    if (parts.length > 0) {
        window.Toast.info(`Memory improved: ${parts.join(', ')}`, { duration: 5000 });
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/script.js
git commit -m "feat(dream): add toast notification for dream consolidation"
```

---

### Task 11: Run Full Test Suite

- [ ] **Step 1: Run all dream tests**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/test_dream_*.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run existing tests to check for regressions**

Run: `cd /root/xelas.io/xelasphere.io- && python -m pytest tests/ -v --timeout=30`
Expected: No regressions in existing tests

- [ ] **Step 3: Fix any failures**

If any tests fail, fix the issues and re-run.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(dream): complete dream system implementation"
```

# Kimi Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-layer circuit breaker (per-user + global) that disables Phase 2 Kimi calls after repeated failures, while Phase 1 continues unaffected.

**Architecture:** New `circuit_breaker.py` module with three public functions (`check_breaker`, `record_success`, `record_failure`) backed by Firestore collection `dream_circuit_breaker`. Integrates into the existing orchestrator (`__init__.py`) around the Phase 2 call site. Uses `firestore.Increment()` for atomic counter updates.

**Tech Stack:** Python, Firestore (google-cloud-firestore), pytest + unittest.mock

---

### Task 1: Add `circuit_breaker_global_max_failures` to DreamConfig

**Files:**
- Modify: `app/memory_v2/dream/config.py:18-19`
- Modify: `tests/test_dream_config.py:19-20`

- [ ] **Step 1: Update test to expect the new field**

In `tests/test_dream_config.py`, inside `TestDreamConfig.test_default_config`, add:

```python
assert cfg.circuit_breaker_global_max_failures == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dream_config.py::TestDreamConfig::test_default_config -v`
Expected: FAIL with `AttributeError: 'DreamConfig' object has no attribute 'circuit_breaker_global_max_failures'`

- [ ] **Step 3: Add the field to DreamConfig**

In `app/memory_v2/dream/config.py`, add after line 19 (`circuit_breaker_cooldown_hours: int = 1`):

```python
circuit_breaker_global_max_failures: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dream_config.py::TestDreamConfig::test_default_config -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/config.py tests/test_dream_config.py
git commit -m "feat(dream): add circuit_breaker_global_max_failures config field"
```

---

### Task 2: Create `circuit_breaker.py` with `check_breaker`

**Files:**
- Create: `app/memory_v2/dream/circuit_breaker.py`
- Create: `tests/test_dream_circuit_breaker.py`

- [ ] **Step 1: Write failing tests for `check_breaker`**

Create `tests/test_dream_circuit_breaker.py`:

```python
"""Tests for dream circuit breaker."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta


class TestCheckBreaker:
    def test_allows_when_no_documents_exist(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        mock_global_snap = MagicMock()
        mock_global_snap.exists = False
        mock_user_snap = MagicMock()
        mock_user_snap.exists = False
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is True

    def test_allows_when_disabled_until_is_in_past(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        expired = datetime.utcnow() - timedelta(minutes=5)
        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {
            'consecutive_failures': 5,
            'disabled_until': expired,
        }
        mock_user_snap = MagicMock()
        mock_user_snap.exists = False
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is True

    def test_blocks_when_global_breaker_active(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        future = datetime.utcnow() + timedelta(minutes=30)
        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {
            'consecutive_failures': 5,
            'disabled_until': future,
        }
        mock_user_snap = MagicMock()
        mock_user_snap.exists = False
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is False

    def test_blocks_when_per_user_breaker_active(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        future = datetime.utcnow() + timedelta(minutes=30)
        mock_global_snap = MagicMock()
        mock_global_snap.exists = False
        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {
            'consecutive_failures': 3,
            'disabled_until': future,
        }
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is False

    def test_allows_when_failures_below_threshold_no_disabled_until(self):
        from app.memory_v2.dream.circuit_breaker import check_breaker
        mock_db = MagicMock()
        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {
            'consecutive_failures': 2,
            'disabled_until': None,
        }
        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {
            'consecutive_failures': 1,
            'disabled_until': None,
        }
        mock_db.collection.return_value.document.return_value.get.side_effect = [
            mock_global_snap, mock_user_snap,
        ]
        assert check_breaker(mock_db, 'user_123') is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dream_circuit_breaker.py::TestCheckBreaker -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.memory_v2.dream.circuit_breaker'`

- [ ] **Step 3: Implement `check_breaker`**

Create `app/memory_v2/dream/circuit_breaker.py`:

```python
"""Circuit breaker for Kimi API calls in dream Phase 2.

Two layers:
- Per-user: 3 consecutive failures → disable Phase 2 for that user for 1 hour
- Global: 5 consecutive failures across any users → disable Phase 2 for all users for 1 hour

State stored in Firestore collection `dream_circuit_breaker`.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from app.memory_v2.dream.config import DreamConfig

logger = logging.getLogger(__name__)

COLLECTION = 'dream_circuit_breaker'
GLOBAL_DOC_ID = 'global'


def _user_doc_id(user_id: str) -> str:
    return f'user_{user_id}'


def _is_breaker_open(snapshot) -> bool:
    """Check if a breaker document indicates Phase 2 should be blocked."""
    if not snapshot.exists:
        return False
    data = snapshot.to_dict()
    disabled_until = data.get('disabled_until')
    if disabled_until is None:
        return False
    return disabled_until > datetime.utcnow()


def check_breaker(db, user_id: str) -> bool:
    """Check if Phase 2 is allowed for this user.

    Returns True if Phase 2 can proceed, False if a breaker is open.
    """
    try:
        global_snap = db.collection(COLLECTION).document(GLOBAL_DOC_ID).get()
        user_snap = db.collection(COLLECTION).document(_user_doc_id(user_id)).get()

        if _is_breaker_open(global_snap):
            disabled_until = global_snap.to_dict().get('disabled_until')
            logger.warning(
                "[dream] Phase 2 skipped for %s: global circuit breaker open until %s",
                user_id, disabled_until,
            )
            return False

        if _is_breaker_open(user_snap):
            disabled_until = user_snap.to_dict().get('disabled_until')
            logger.warning(
                "[dream] Phase 2 skipped for %s: per-user circuit breaker open until %s",
                user_id, disabled_until,
            )
            return False

        return True

    except Exception as exc:
        logger.warning("[dream] Circuit breaker check failed — allowing Phase 2: %s", exc)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dream_circuit_breaker.py::TestCheckBreaker -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/circuit_breaker.py tests/test_dream_circuit_breaker.py
git commit -m "feat(dream): add circuit breaker check_breaker function"
```

---

### Task 3: Add `record_success` to circuit breaker

**Files:**
- Modify: `app/memory_v2/dream/circuit_breaker.py`
- Modify: `tests/test_dream_circuit_breaker.py`

- [ ] **Step 1: Write failing test for `record_success`**

Append to `tests/test_dream_circuit_breaker.py`:

```python
class TestRecordSuccess:
    def test_resets_both_counters(self):
        from app.memory_v2.dream.circuit_breaker import record_success
        mock_db = MagicMock()
        mock_global_doc = MagicMock()
        mock_user_doc = MagicMock()

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        record_success(mock_db, 'user_123')

        mock_global_doc.set.assert_called_once_with({
            'consecutive_failures': 0,
            'disabled_until': None,
            'last_failure_at': None,
        })
        mock_user_doc.set.assert_called_once_with({
            'consecutive_failures': 0,
            'disabled_until': None,
            'last_failure_at': None,
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dream_circuit_breaker.py::TestRecordSuccess -v`
Expected: FAIL with `ImportError: cannot import name 'record_success'`

- [ ] **Step 3: Implement `record_success`**

Add to `app/memory_v2/dream/circuit_breaker.py`:

```python
def record_success(db, user_id: str) -> None:
    """Reset both circuit breaker counters after a successful Kimi call."""
    try:
        reset_data = {
            'consecutive_failures': 0,
            'disabled_until': None,
            'last_failure_at': None,
        }
        db.collection(COLLECTION).document(GLOBAL_DOC_ID).set(reset_data)
        db.collection(COLLECTION).document(_user_doc_id(user_id)).set(reset_data)
        logger.debug("[dream] Circuit breaker counters reset for %s", user_id)
    except Exception as exc:
        logger.warning("[dream] Failed to reset circuit breaker: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dream_circuit_breaker.py::TestRecordSuccess -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/memory_v2/dream/circuit_breaker.py tests/test_dream_circuit_breaker.py
git commit -m "feat(dream): add circuit breaker record_success function"
```

---

### Task 4: Add `record_failure` to circuit breaker

**Files:**
- Modify: `app/memory_v2/dream/circuit_breaker.py`
- Modify: `tests/test_dream_circuit_breaker.py`

- [ ] **Step 1: Write failing tests for `record_failure`**

Append to `tests/test_dream_circuit_breaker.py`:

```python
class TestRecordFailure:
    def test_increments_without_tripping(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {'consecutive_failures': 1}

        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {'consecutive_failures': 1}

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig()
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is False
        # Both docs should be updated (set called)
        assert mock_global_doc.set.called
        assert mock_user_doc.set.called
        # Check counters incremented to 2
        global_data = mock_global_doc.set.call_args[0][0]
        user_data = mock_user_doc.set.call_args[0][0]
        assert global_data['consecutive_failures'] == 2
        assert user_data['consecutive_failures'] == 2
        assert global_data['disabled_until'] is None
        assert user_data['disabled_until'] is None

    def test_trips_per_user_breaker_at_threshold(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {'consecutive_failures': 1}

        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {'consecutive_failures': 2}  # will become 3

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig(circuit_breaker_max_failures=3)
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is True
        user_data = mock_user_doc.set.call_args[0][0]
        assert user_data['consecutive_failures'] == 3
        assert user_data['disabled_until'] is not None

    def test_trips_global_breaker_at_threshold(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = True
        mock_global_snap.to_dict.return_value = {'consecutive_failures': 4}  # will become 5

        mock_user_snap = MagicMock()
        mock_user_snap.exists = True
        mock_user_snap.to_dict.return_value = {'consecutive_failures': 0}

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig(circuit_breaker_global_max_failures=5)
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is True
        global_data = mock_global_doc.set.call_args[0][0]
        assert global_data['consecutive_failures'] == 5
        assert global_data['disabled_until'] is not None

    def test_handles_missing_documents(self):
        from app.memory_v2.dream.circuit_breaker import record_failure
        from app.memory_v2.dream.config import DreamConfig
        mock_db = MagicMock()

        mock_global_snap = MagicMock()
        mock_global_snap.exists = False

        mock_user_snap = MagicMock()
        mock_user_snap.exists = False

        mock_global_doc = MagicMock()
        mock_global_doc.get.return_value = mock_global_snap
        mock_user_doc = MagicMock()
        mock_user_doc.get.return_value = mock_user_snap

        def mock_document(doc_id):
            if doc_id == 'global':
                return mock_global_doc
            return mock_user_doc

        mock_db.collection.return_value.document.side_effect = mock_document

        config = DreamConfig()
        tripped = record_failure(mock_db, 'user_123', config)

        assert tripped is False
        global_data = mock_global_doc.set.call_args[0][0]
        assert global_data['consecutive_failures'] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dream_circuit_breaker.py::TestRecordFailure -v`
Expected: FAIL with `ImportError: cannot import name 'record_failure'`

- [ ] **Step 3: Implement `record_failure`**

Add to `app/memory_v2/dream/circuit_breaker.py`:

```python
def record_failure(db, user_id: str, config: DreamConfig) -> bool:
    """Record a Kimi failure and trip breaker if threshold reached.

    Returns True if any breaker was tripped by this failure.
    """
    try:
        now = datetime.utcnow()
        cooldown = timedelta(hours=config.circuit_breaker_cooldown_hours)
        tripped = False

        # Update global counter
        global_ref = db.collection(COLLECTION).document(GLOBAL_DOC_ID)
        global_snap = global_ref.get()
        global_count = 0
        if global_snap.exists:
            global_count = global_snap.to_dict().get('consecutive_failures', 0)
        global_count += 1

        global_data = {
            'consecutive_failures': global_count,
            'last_failure_at': now,
            'disabled_until': None,
        }
        if global_count >= config.circuit_breaker_global_max_failures:
            global_data['disabled_until'] = now + cooldown
            tripped = True
            logger.warning(
                "[dream] Global circuit breaker tripped: %d consecutive failures",
                global_count,
            )
        global_ref.set(global_data)

        # Update per-user counter
        user_ref = db.collection(COLLECTION).document(_user_doc_id(user_id))
        user_snap = user_ref.get()
        user_count = 0
        if user_snap.exists:
            user_count = user_snap.to_dict().get('consecutive_failures', 0)
        user_count += 1

        user_data = {
            'consecutive_failures': user_count,
            'last_failure_at': now,
            'disabled_until': None,
        }
        if user_count >= config.circuit_breaker_max_failures:
            user_data['disabled_until'] = now + cooldown
            tripped = True
            logger.warning(
                "[dream] Per-user circuit breaker tripped for %s: %d consecutive failures",
                user_id, user_count,
            )
        user_ref.set(user_data)

        return tripped

    except Exception as exc:
        logger.warning("[dream] Failed to record circuit breaker failure: %s", exc)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dream_circuit_breaker.py::TestRecordFailure -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Run all circuit breaker tests**

Run: `pytest tests/test_dream_circuit_breaker.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/memory_v2/dream/circuit_breaker.py tests/test_dream_circuit_breaker.py
git commit -m "feat(dream): add circuit breaker record_failure function"
```

---

### Task 5: Integrate circuit breaker into dream orchestrator

**Files:**
- Modify: `app/memory_v2/dream/__init__.py:78-93`
- Modify: `tests/test_dream_orchestrator.py`

- [ ] **Step 1: Write failing test — Phase 2 skipped when breaker is open**

Append to `tests/test_dream_orchestrator.py`:

```python
class TestCircuitBreakerIntegration:
    @patch('app.memory_v2.dream.circuit_breaker.check_breaker')
    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_skips_phase2_when_breaker_open(self, mock_state, mock_time, mock_sessions,
                                             mock_lock, mock_phase1, mock_should_p2,
                                             mock_exec_p1, mock_save_log,
                                             mock_update_state, mock_release,
                                             mock_check_breaker):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = [MagicMock(status=MagicMock(value='active')) for _ in range(15)]

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = True
        mock_check_breaker.return_value = False  # breaker is open
        mock_phase1.return_value = ConsolidationReport(flagged_conflicts=[['m1', 'm2']])
        mock_exec_p1.return_value = []
        mock_save_log.return_value = 'log_789'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_phase1.assert_called_once()
        mock_check_breaker.assert_called_once_with(mock_db, 'u1')
        mock_save_log.assert_called_once()
        # Phase 2 should NOT have run
        log_arg = mock_save_log.call_args[0][1]
        assert 'Phase 2 skipped: circuit breaker open' in log_arg.errors

    @patch('app.memory_v2.dream.circuit_breaker.record_success')
    @patch('app.memory_v2.dream.circuit_breaker.check_breaker')
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
    def test_records_success_on_kimi_success(self, mock_state, mock_time, mock_sessions,
                                              mock_lock, mock_phase1, mock_should_p2,
                                              mock_run_p2, mock_exec_p1, mock_exec_p2,
                                              mock_save_log, mock_update_state,
                                              mock_release, mock_check_breaker,
                                              mock_record_success):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = [MagicMock(status=MagicMock(value='active')) for _ in range(15)]

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = True
        mock_check_breaker.return_value = True
        mock_phase1.return_value = ConsolidationReport(flagged_conflicts=[['m1', 'm2']])
        mock_run_p2.return_value = {'resolve_conflicts': [], 'prune': [], 'promote': [], 'synthesize': []}
        mock_exec_p1.return_value = []
        mock_exec_p2.return_value = []
        mock_save_log.return_value = 'log_ok'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_record_success.assert_called_once_with(mock_db, 'u1')

    @patch('app.memory_v2.dream.circuit_breaker.record_failure')
    @patch('app.memory_v2.dream.circuit_breaker.check_breaker')
    @patch('app.memory_v2.dream.gates.release_lock')
    @patch('app.memory_v2.dream.gates.update_dream_state')
    @patch('app.memory_v2.dream.executor.save_dream_log')
    @patch('app.memory_v2.dream.executor.execute_phase1_actions')
    @patch('app.memory_v2.dream.phase2.run_phase2')
    @patch('app.memory_v2.dream.phase2.should_run_phase2')
    @patch('app.memory_v2.dream.phase1.run_phase1')
    @patch('app.memory_v2.dream.gates.try_acquire_lock')
    @patch('app.memory_v2.dream.gates.check_session_count_gate')
    @patch('app.memory_v2.dream.gates.check_time_gate')
    @patch('app.memory_v2.dream.gates.get_dream_state')
    def test_records_failure_on_kimi_failure(self, mock_state, mock_time, mock_sessions,
                                              mock_lock, mock_phase1, mock_should_p2,
                                              mock_run_p2, mock_exec_p1,
                                              mock_save_log, mock_update_state,
                                              mock_release, mock_check_breaker,
                                              mock_record_failure):
        from app.memory_v2.dream import maybe_consolidate
        from app.memory_v2.dream.report import ConsolidationReport

        mock_db = MagicMock()
        mock_store = MagicMock()
        mock_store.get_all.return_value = [MagicMock(status=MagicMock(value='active')) for _ in range(15)]

        mock_state.return_value = {}
        mock_time.return_value = True
        mock_sessions.return_value = True
        mock_lock.return_value = True
        mock_should_p2.return_value = True
        mock_check_breaker.return_value = True
        mock_phase1.return_value = ConsolidationReport(flagged_conflicts=[['m1', 'm2']])
        mock_run_p2.return_value = None  # Kimi failed
        mock_exec_p1.return_value = []
        mock_save_log.return_value = 'log_fail'

        with patch('app.memory_v2.dream.MemoryStoreV2', return_value=mock_store):
            result = maybe_consolidate(db=mock_db, user_id='u1')

        assert result is True
        mock_record_failure.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dream_orchestrator.py::TestCircuitBreakerIntegration -v`
Expected: FAIL (circuit_breaker not imported/used in orchestrator yet)

- [ ] **Step 3: Modify the orchestrator to use circuit breaker**

Replace the Phase 2 block in `app/memory_v2/dream/__init__.py` (lines 78-93). The full `_run_consolidation` function becomes:

```python
def _run_consolidation(db, user_id: str, config: DreamConfig) -> bool:
    """Execute the full consolidation pipeline."""
    started_at = datetime.utcnow()
    dream_log = DreamLog(user_id=user_id, started_at=started_at)

    store = MemoryStoreV2(user_id)
    all_memories = store.get_all(include_expired=False)

    # Pre-check: enough memories?
    active_count = sum(1 for m in all_memories if m.status.value in ('active', 'provisional'))
    if active_count < config.min_active_memories:
        gates.release_lock(db, user_id)
        return False

    # Phase 1
    report = phase1.run_phase1(all_memories, config)
    p1_actions = executor.execute_phase1_actions(
        store=store, pruned_ids=report.pruned, merged_pairs=report.merged,
    )
    dream_log.phase1_actions = p1_actions

    # Phase 2 (conditional, with circuit breaker)
    p2_actions = []
    if phase2.should_run_phase2(report, config.pattern_synthesis_threshold):
        if not circuit_breaker.check_breaker(db, user_id):
            dream_log.errors.append('Phase 2 skipped: circuit breaker open')
        else:
            memories_by_id = {m.id: m for m in report.all_active}
            p2_decisions = phase2.run_phase2(
                report=report, memories_by_id=memories_by_id,
                config_token_budget=config.kimi_token_budget,
                config_max_syntheses=config.max_syntheses_per_cycle,
            )
            if p2_decisions is not None:
                circuit_breaker.record_success(db, user_id)
                dream_log.kimi_raw_response = str(p2_decisions)
                p2_actions = executor.execute_phase2_actions(
                    store=store, decisions=p2_decisions, user_id=user_id,
                )
            else:
                circuit_breaker.record_failure(db, user_id, config)
                dream_log.errors.append('Phase 2 Kimi call failed')

    dream_log.phase2_actions = p2_actions
    dream_log.completed_at = datetime.utcnow()

    # Save log
    executor.save_dream_log(db, dream_log)

    # Build notification
    summary = dream_log.summary()
    notification = {k: v for k, v in summary.items() if v > 0 and k != 'total_actions'}

    # Update state
    gates.update_dream_state(db, user_id, {
        'last_consolidated_at': datetime.utcnow(),
        'pending_notification': notification if notification else None,
    })

    gates.release_lock(db, user_id)

    # Trigger smart suggestions refresh (fire-and-forget, non-blocking)
    try:
        from app.services.smart_suggestions import refresh_pool_background
        import threading
        threading.Thread(
            target=refresh_pool_background,
            args=(db, user_id, "dream"),
            daemon=True,
        ).start()
    except Exception as e:
        logger.debug(f"[dream] Smart suggestions refresh skipped: {e}")

    logger.info(f"[dream] Consolidation complete for {user_id}: {summary}")
    return True
```

Also add the import at the top of `app/memory_v2/dream/__init__.py`:

```python
from app.memory_v2.dream import circuit_breaker
```

- [ ] **Step 4: Run new integration tests**

Run: `pytest tests/test_dream_orchestrator.py::TestCircuitBreakerIntegration -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run all orchestrator tests to check nothing broke**

Run: `pytest tests/test_dream_orchestrator.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 6: Run full dream test suite**

Run: `pytest tests/test_dream_*.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add app/memory_v2/dream/__init__.py tests/test_dream_orchestrator.py
git commit -m "feat(dream): integrate circuit breaker into consolidation orchestrator"
```

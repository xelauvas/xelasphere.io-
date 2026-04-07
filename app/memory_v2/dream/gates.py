"""Dream system gate checks: time, session count, lock."""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from app.memory_v2.dream.config import DreamConfig

logger = logging.getLogger(__name__)


def check_time_gate(last_consolidated_at: Optional[datetime], config: DreamConfig) -> bool:
    """Gate 1: Has enough time elapsed since last consolidation?"""
    if last_consolidated_at is None:
        return True
    now = datetime.utcnow()
    hours_since = (now - last_consolidated_at).total_seconds() / 3600
    if hours_since < config.hard_rate_floor_hours:
        return False
    return hours_since >= config.frequency_hours


def check_session_count_gate(db, user_id: str, last_consolidated_at: Optional[datetime], config: DreamConfig) -> bool:
    """Gate 2: Have enough sessions been touched since last consolidation?"""
    since = last_consolidated_at or datetime(2000, 1, 1)
    try:
        sessions_ref = (
            db.collection('users').document(user_id)
            .collection('chat_sessions').where('last_updated', '>', since)
        )
        docs = list(sessions_ref.stream())
        return len(docs) >= config.min_sessions
    except Exception as e:
        logger.warning(f"[dream] Session count gate failed: {e}")
        return False


def try_acquire_lock(db, user_id: str, worker_id: Optional[str] = None, config: Optional[DreamConfig] = None) -> bool:
    """Gate 3: Try to acquire the consolidation lock. Returns True if acquired."""
    if config is None:
        config = DreamConfig()
    if worker_id is None:
        worker_id = f"worker_{uuid.uuid4().hex[:8]}"
    lock_ref = db.collection('dream_locks').document(user_id)
    try:
        snapshot = lock_ref.get()
        if snapshot.exists:
            lock_data = snapshot.to_dict()
            locked_at = lock_data.get('locked_at')
            if locked_at:
                age_hours = (datetime.utcnow() - locked_at).total_seconds() / 3600
                if age_hours < config.stale_lock_hours:
                    return False
            else:
                return False
        lock_ref.set({'worker_id': worker_id, 'locked_at': datetime.utcnow()})
        return True
    except Exception as e:
        logger.warning(f"[dream] Lock acquisition failed: {e}")
        return False


def release_lock(db, user_id: str) -> None:
    """Release the consolidation lock."""
    try:
        db.collection('dream_locks').document(user_id).delete()
    except Exception as e:
        logger.warning(f"[dream] Lock release failed: {e}")


def get_dream_state(db, user_id: str) -> dict:
    """Read the dream state document for a user."""
    try:
        snapshot = (
            db.collection('users').document(user_id)
            .collection('dream_state').document('current')
            .get()
        )
        if snapshot.exists:
            return snapshot.to_dict()
        return {}
    except Exception as e:
        logger.warning(f"[dream] Failed to read dream state: {e}")
        return {}


def update_dream_state(db, user_id: str, updates: dict) -> None:
    """Update the dream state document for a user."""
    try:
        (
            db.collection('users').document(user_id)
            .collection('dream_state').document('current')
            .set(updates, merge=True)
        )
    except Exception as e:
        logger.warning(f"[dream] Failed to update dream state: {e}")

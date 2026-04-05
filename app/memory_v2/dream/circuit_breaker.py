"""Circuit breaker for Kimi API calls in dream Phase 2.

Two layers:
- Per-user: 3 consecutive failures -> disable Phase 2 for that user for 1 hour
- Global: 5 consecutive failures across any users -> disable Phase 2 for all users for 1 hour

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

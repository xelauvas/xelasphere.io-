"""Circuit breaker for Kimi API calls in dream Phase 2.

Two layers:
- Per-user: 3 consecutive failures -> disable Phase 2 for that user for 1 hour
- Global: 5 consecutive failures across any users -> disable Phase 2 for all users for 1 hour

State stored in Firestore collection `dream_circuit_breaker`.
"""
import logging
from datetime import datetime, timedelta
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


def record_failure(db, user_id: str, config: DreamConfig) -> bool:
    """Record a Kimi failure and trip breaker if threshold reached.

    Returns True if any breaker was tripped by this failure.

    Note: The global counter uses read-then-write without a transaction.
    Two concurrent workers may race, causing the counter to under-count
    by 1. This is acceptable — it may delay the global breaker by one
    extra failure, which is conservative and safe.
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

        # Update per-user counter (no race — per-user lock prevents concurrent runs)
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


def record_success(db, user_id: str) -> None:
    """Reset both circuit breaker counters after a successful Kimi call.

    Resets the global counter too: a single success proves Kimi is reachable,
    so accumulated global failures no longer indicate an outage.
    """
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

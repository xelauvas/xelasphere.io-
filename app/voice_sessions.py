"""Redis-backed cross-worker voice session tracking.

Uses a SET for session IDs (idempotent) + a counter computed from set
cardinality. TTL per session key so dead sessions expire automatically.
"""
import logging
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

SESSIONS_SET = 'voice:sessions'
SESSION_TTL = 300  # 5 min — renewed per heartbeat


def register(session_id: str) -> None:
    """Mark a voice session as active (idempotent)."""
    if not session_id:
        return
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.sadd(SESSIONS_SET, session_id)
        pipe.setex(f'voice:heartbeat:{session_id}', SESSION_TTL, '1')
        pipe.execute()
    except Exception as e:
        logger.warning(f"voice_sessions.register failed: {e}")


def release(session_id: str) -> None:
    """Mark a voice session as ended."""
    if not session_id:
        return
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.srem(SESSIONS_SET, session_id)
        pipe.delete(f'voice:heartbeat:{session_id}')
        pipe.execute()
    except Exception as e:
        logger.warning(f"voice_sessions.release failed: {e}")


def count() -> int:
    """Current active voice session count across all workers."""
    try:
        return int(get_redis().scard(SESSIONS_SET) or 0)
    except Exception as e:
        logger.warning(f"voice_sessions.count failed: {e}")
        return 0

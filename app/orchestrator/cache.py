"""
SemanticCache — cache responses by normalized message hash.
Stores in-memory with Firestore persistence.
24h TTL. Skips uncensored, vision, long_context jobs.
Zero tokens, zero cost, zero latency for cache hits.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# In-memory hot cache: key → (response, model_id, expires_ts)
_hot: Dict[str, Tuple[str, str, float]] = {}
_MAX_HOT = 200

# Jobs that should never be cached
_SKIP_JOBS = {"uncensored", "vision", "long_context"}


def check(message: str, job: str) -> Optional[Tuple[str, str]]:
    """
    Check if we have a cached response.
    Returns (cached_response, model_id) or None.
    """
    if job in _SKIP_JOBS:
        return None
    if not message or len(message) < 15 or len(message) > 500:
        return None

    key = _make_key(message)

    # Hot cache first
    entry = _hot.get(key)
    if entry:
        resp, model_id, expires = entry
        if time.time() < expires:
            logger.info(f"[Cache] HIT (hot, msg_len={len(message)})")
            return resp, model_id
        else:
            del _hot[key]

    # Firestore fallback
    return _check_firestore(key, message)


def store(message: str, response: str, model_id: str, job: str):
    """Store a response in cache."""
    if job in _SKIP_JOBS:
        return
    if not message or not response:
        return
    if len(message) < 15 or len(message) > 500:
        return
    if len(response) < 30:
        return  # Don't cache very short responses

    key = _make_key(message)
    expires = time.time() + 86400  # 24h

    # Evict oldest if full
    if len(_hot) >= _MAX_HOT:
        oldest_key = min(_hot, key=lambda k: _hot[k][2])
        del _hot[oldest_key]

    _hot[key] = (response, model_id, expires)

    # Persist to Firestore (async-safe, fire and forget)
    try:
        from app.config import db
        from google.cloud import firestore
        db.collection("semantic_cache").document(key).set({
            "key": key,
            "message_preview": message[:100],
            "response": response[:10000],  # Cap at 10K chars
            "model_id": model_id,
            "job": job,
            "expires_at": datetime.utcnow() + timedelta(hours=24),
            "created_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        logger.debug(f"[Cache] Firestore store failed: {e}")


def get_stats() -> Dict:
    """Return cache stats for dashboard."""
    now = time.time()
    valid = sum(1 for _, _, exp in _hot.values() if now < exp)
    return {
        "hot_entries": valid,
        "max_entries": _MAX_HOT,
    }


def _make_key(message: str) -> str:
    """Normalize message and hash it."""
    normalized = message.lower().strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    # Remove punctuation for fuzzy matching
    normalized = re.sub(r'[^\w\s]', '', normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()[:20]


def _check_firestore(key: str, message: str) -> Optional[Tuple[str, str]]:
    """Check Firestore for a cached response."""
    try:
        from app.config import db
        doc = db.collection("semantic_cache").document(key).get()
        if not doc.exists:
            return None

        data = doc.to_dict()
        expires = data.get("expires_at")
        if expires and expires.replace(tzinfo=None) < datetime.utcnow():
            return None  # Expired

        response = data.get("response", "")
        model_id = data.get("model_id", "")
        if response:
            # Warm hot cache
            _hot[key] = (response, model_id, time.time() + 86400)
            logger.info(f"[Cache] HIT (firestore, msg_len={len(message)})")
            return response, model_id
    except Exception as e:
        logger.debug(f"[Cache] Firestore check failed: {e}")

    return None

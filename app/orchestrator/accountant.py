"""
QuotaAccountant — tracks token usage per provider per day.
In-memory buffer flushed to Firestore every 30 seconds.
Each Gunicorn worker has its own buffer — worst case 4x overshoot.
Free tier limits set to 80% of actual to compensate.
"""
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict

from app.orchestrator.models import Model

logger = logging.getLogger(__name__)

_buffer: Dict[str, int] = defaultdict(int)
_buffer_lock = threading.Lock()

_daily_totals: Dict[str, int] = defaultdict(int)       # tokens per provider
_daily_requests: Dict[str, int] = defaultdict(int)      # requests per provider

_flush_thread = None
_running = False


def start():
    """Start the background flush thread."""
    global _flush_thread, _running
    if _running:
        return
    _running = True
    _flush_thread = threading.Thread(
        target=_flush_loop, daemon=True, name="QuotaAccountant"
    )
    _flush_thread.start()
    logger.info("[Accountant] Started")
    _load_today_from_firestore()


def record(model: Model, input_tokens: int, output_tokens: int):
    """Record token usage after a request."""
    total = input_tokens + output_tokens
    key = f"{model.provider}:{datetime.utcnow().date()}"

    with _buffer_lock:
        _buffer[key] += total
        _daily_totals[model.provider] += total
        _daily_requests[model.provider] += 1

    model.tokens_used_today += total
    model.requests_used_today += 1

    _check_quota_threshold(model)


def has_quota(provider: str) -> bool:
    """Check if a provider has quota remaining (tokens AND requests)."""
    from app.orchestrator.config import FREE_TIER_LIMITS

    limits = FREE_TIER_LIMITS.get(provider, {})

    # Check daily request limit (e.g. Gemini: 16 RPD at 80%)
    req_limit = limits.get("daily_requests", 0)
    if req_limit > 0:
        reqs_used = _daily_requests.get(provider, 0)
        if reqs_used >= req_limit:
            return False

    # Check daily token limit
    token_limit = limits.get("daily_tokens", 0)
    if token_limit > 0:
        tokens_used = _daily_totals.get(provider, 0)
        if tokens_used >= token_limit:
            return False

    return True


def get_status() -> Dict:
    """Return quota status for all providers."""
    from app.orchestrator.config import FREE_TIER_LIMITS

    status = {}
    for provider in ("groq", "cerebras", "gemini", "deepseek", "openai"):
        limits = FREE_TIER_LIMITS.get(provider, {})
        token_limit = limits.get("daily_tokens", 0)
        req_limit = limits.get("daily_requests", 0)
        tokens_used = _daily_totals.get(provider, 0)
        reqs_used = _daily_requests.get(provider, 0)
        status[provider] = {
            "tokens_used": tokens_used,
            "token_limit": token_limit,
            "requests_used": reqs_used,
            "request_limit": req_limit,
            "exhausted": not has_quota(provider),
        }
    return status


def _check_quota_threshold(model: Model):
    from app.orchestrator.config import FREE_TIER_LIMITS

    limits = FREE_TIER_LIMITS.get(model.provider, {})
    daily_limit = limits.get("daily_tokens", 0)

    if daily_limit <= 0:
        return

    used = _daily_totals.get(model.provider, 0)

    if used >= daily_limit:
        if not model.quota_exhausted:
            model.quota_exhausted = True
            logger.warning(
                f"🚫 QUOTA EXHAUSTED [{model.provider}]: "
                f"{used:,} / {daily_limit:,} tokens"
            )
    elif used >= daily_limit * 0.8:
        logger.info(
            f"⚠️ QUOTA WARNING [{model.provider}]: "
            f"{used:,} / {daily_limit:,} tokens (80%+)"
        )


def _flush_loop():
    while _running:
        time.sleep(30)
        try:
            _flush_to_firestore()
        except Exception as e:
            logger.error(f"[Accountant] Flush failed: {e}")


def _flush_to_firestore():
    try:
        from app.config import db
        from google.cloud import firestore
    except Exception:
        return  # Firestore not available

    with _buffer_lock:
        if not _buffer:
            return
        snapshot = dict(_buffer)
        _buffer.clear()

    batch = db.batch()
    for key, tokens in snapshot.items():
        provider, date_str = key.split(":", 1)
        ref = db.collection("orchestrator_quota").document(f"{provider}_{date_str}")
        batch.set(ref, {
            "provider": provider,
            "date": date_str,
            "tokens_total": firestore.Increment(tokens),
            "last_updated": firestore.SERVER_TIMESTAMP,
        }, merge=True)

    batch.commit()
    logger.debug(f"[Accountant] Flushed {len(snapshot)} quota records")


def _load_today_from_firestore():
    """Load today's usage on startup."""
    try:
        from app.config import db
    except Exception as e:
        logger.warning(f"[Accountant] Firestore not available: {e}")
        return

    today = str(datetime.utcnow().date())
    try:
        for provider in ("groq", "cerebras", "deepseek", "openai"):
            doc = db.collection("orchestrator_quota").document(
                f"{provider}_{today}"
            ).get()
            if doc.exists:
                data = doc.to_dict()
                tokens = data.get("tokens_total", 0)
                _daily_totals[provider] = tokens
                logger.info(f"[Accountant] {provider} today: {tokens:,} tokens")
    except Exception as e:
        logger.warning(f"[Accountant] Could not load today's quota: {e}")

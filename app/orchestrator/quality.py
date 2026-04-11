"""
QualitySignalCollector — passively learns which models perform best
for which job types by watching user behavior.

Signals collected:
  - on_followup: user sent another message → previous model was good enough
  - on_error: model returned an error → negative signal
  - on_short_response: model returned < 50 chars → likely unhelpful

Quality scores are exponential moving averages (0.0-1.0).
Default is 0.5 for models with no data.
Flushed to Firestore every 5 minutes.
"""
import logging
import threading
import time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_dirty: Dict[Tuple[str, str], float] = {}
_lock = threading.Lock()
_flush_thread = None
_running = False


def start():
    global _flush_thread, _running
    if _running:
        return
    _running = True
    _flush_thread = threading.Thread(
        target=_flush_loop, daemon=True, name="QualityCollector"
    )
    _flush_thread.start()
    logger.info("[Quality] Started")


def on_followup(model_id: str, job: str):
    """User sent another message — the previous response was useful."""
    _update(model_id, job, signal=+0.02)


def on_error(model_id: str, job: str):
    """Model returned an error — strong negative signal."""
    _update(model_id, job, signal=-0.10)


def on_short_response(model_id: str, job: str):
    """Model returned a very short response — mild negative signal."""
    _update(model_id, job, signal=-0.03)


def on_happy_face(model_id: str, job: str):
    """User clicked happy face — strongest positive signal (explicit feedback)."""
    _update(model_id, job, signal=+0.10)


def on_sad_face(model_id: str, job: str):
    """User clicked sad face — strongest negative signal (explicit feedback)."""
    _update(model_id, job, signal=-0.15)


def _update(model_id: str, job: str, signal: float):
    from app.orchestrator import registry

    model = registry.get(model_id)
    if not model:
        return

    current = model.quality_for(job)
    # Exponential moving average: 95% old, 5% new signal
    adjusted = max(0.0, min(1.0, current + signal * 0.05))
    model.quality_scores[job] = adjusted

    with _lock:
        _dirty[(model_id, job)] = adjusted

    logger.debug(
        f"[Quality] {model_id}/{job}: "
        f"{current:.3f} → {adjusted:.3f} (signal={signal:+.2f})"
    )


def _flush_loop():
    while _running:
        time.sleep(300)  # 5 minutes
        try:
            _flush_to_firestore()
        except Exception as e:
            logger.error(f"[Quality] Flush failed: {e}")


def _flush_to_firestore():
    try:
        from app.config import db
        from google.cloud import firestore
    except Exception:
        return

    with _lock:
        if not _dirty:
            return
        snapshot = dict(_dirty)
        _dirty.clear()

    batch = db.batch()
    for (model_id, job), score in snapshot.items():
        doc_id = f"{model_id}__{job}".replace("/", "__")
        ref = db.collection("orchestrator_quality").document(doc_id)
        batch.set(ref, {
            "model_id": model_id,
            "job": job,
            "quality_score": score,
            "last_updated": firestore.SERVER_TIMESTAMP,
        }, merge=True)

    batch.commit()
    logger.info(f"[Quality] Flushed {len(snapshot)} scores to Firestore")

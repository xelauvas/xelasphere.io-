"""
ModelRegistry — loads models from existing MODEL_CATALOG,
enriches with live data from Firestore, serves as single source of truth.
"""
import logging
from typing import Dict, List, Optional

from app.orchestrator.models import Model

logger = logging.getLogger(__name__)

_models: Dict[str, Model] = {}
_initialized = False


def initialize():
    """Called once at startup. Loads models from existing catalog."""
    global _initialized
    if _initialized:
        return

    from app.model_catalog import MODEL_CATALOG

    for model_id, meta in MODEL_CATALOG.items():
        provider = meta.get("provider", "unknown")
        strengths = _derive_strengths(model_id, meta)

        model = Model(
            id=model_id,
            provider=provider,
            display_name=meta.get("label", model_id),
            strengths=strengths,
            context_window=meta.get("context", 32_000),
            supports_vision=meta.get("vision", False),
            supports_streaming=provider in ("deepseek", "moonshot", "openai", "groq", "ollama", "cerebras"),
            cost_tier=_infer_cost_tier(provider),
            free_tier_daily_tokens=_get_free_tier_limit(provider),
            quality_scores={s: 0.5 for s in strengths},
        )
        _models[model_id] = model

    logger.info(f"[Registry] Loaded {len(_models)} models from catalog")

    _load_firestore_state()
    _initialized = True


def all_models() -> List[Model]:
    return list(_models.values())


def get(model_id: str) -> Optional[Model]:
    return _models.get(model_id)


def get_by_provider(provider: str) -> List[Model]:
    return [m for m in _models.values() if m.provider == provider]


def add_discovered(model: Model):
    """Add a newly discovered model in pending_review state."""
    _models[model.id] = model
    logger.info(f"[Registry] Added discovered model: {model.id} ({model.provider})")


def _derive_strengths(model_id: str, meta: dict) -> List[str]:
    """Derive model strengths from catalog metadata and naming."""
    strengths = []
    name = model_id.lower()

    if meta.get("vision"):
        strengths.append("vision")
    if meta.get("safety"):
        strengths.append("safety")
    if meta.get("prompt_guard"):
        strengths.append("safety")
    if meta.get("coding"):
        strengths.append("coding")
    if meta.get("reasoning"):
        strengths.append("reasoning")
    if meta.get("uncensored"):
        strengths.append("uncensored")
    if meta.get("long_context"):
        strengths.append("long_context")

    # Infer from name if no explicit flags
    if not strengths:
        if any(x in name for x in ["8b", "instant", "nano", "mini", "scout"]):
            strengths.append("fast_chat")
        if any(x in name for x in ["kimi", "128k"]):
            strengths.append("long_context")

    if not strengths:
        strengths = ["general_chat"]

    # Everything except safety/guard/uncensored models can do general chat
    if "general_chat" not in strengths and not any(
        s in strengths for s in ("safety", "uncensored")
    ):
        strengths.append("general_chat")

    return strengths


def _infer_cost_tier(provider: str) -> str:
    if provider in ("groq", "cerebras", "ollama"):
        return "free"
    if provider in ("deepseek", "moonshot"):
        return "cheap"
    return "paid"


def _get_free_tier_limit(provider: str) -> int:
    from app.orchestrator.config import FREE_TIER_LIMITS
    limits = FREE_TIER_LIMITS.get(provider, {})
    return limits.get("daily_tokens", 0)


def _load_firestore_state():
    """Load persisted quality scores and deprecation overrides from Firestore."""
    try:
        from app.config import db

        # Load quality scores
        docs = db.collection("orchestrator_quality").stream()
        quality_count = 0
        for doc in docs:
            data = doc.to_dict()
            model_id = data.get("model_id")
            job = data.get("job")
            score = data.get("quality_score", 0.5)
            if model_id in _models and job:
                _models[model_id].quality_scores[job] = score
                quality_count += 1

        # Load deprecation overrides from health docs
        health_docs = db.collection("orchestrator_health").stream()
        override_count = 0
        for doc in health_docs:
            data = doc.to_dict()
            overrides = data.get("model_overrides", {})
            for old_id, new_id in overrides.items():
                if old_id in _models:
                    _models[old_id].is_deprecated = True
                    _models[old_id].replacement_id = new_id
                    _models[old_id].status = "down"
                    override_count += 1

        if quality_count or override_count:
            logger.info(
                f"[Registry] Loaded Firestore state: "
                f"{quality_count} quality scores, {override_count} overrides"
            )
    except Exception as e:
        logger.warning(f"[Registry] Could not load Firestore state: {e}")

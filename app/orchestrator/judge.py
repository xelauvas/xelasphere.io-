"""
ModelJudge — given a job type, returns the best available model.
Walks the fallback chain considering health, quota, deprecation, and quality.
Never returns None — always finds something.
"""
import logging
from typing import Optional, Tuple

from app.orchestrator.config import FALLBACK_CHAINS
from app.orchestrator.models import Model

logger = logging.getLogger(__name__)


def hire(job: str, context: dict = None) -> Tuple[Model, str]:
    """
    Main routing function.
    Returns (Model, quality_tier).
    """
    context = context or {}
    from app.orchestrator import registry, accountant

    chain = FALLBACK_CHAINS.get(job, FALLBACK_CHAINS["general_chat"])

    for provider, model_id, tier in chain:
        model = registry.get(model_id)

        # Handle deprecation overrides
        if model and model.is_deprecated and model.replacement_id:
            replacement = registry.get(model.replacement_id)
            if replacement and replacement.is_available():
                logger.info(
                    f"[Judge] {model_id} deprecated → using {replacement.id}"
                )
                return replacement, tier
            else:
                continue

        if not model:
            continue

        if not model.is_available():
            logger.debug(
                f"[Judge] Skip {model_id}: status={model.status} "
                f"deprecated={model.is_deprecated} quota={model.quota_exhausted}"
            )
            continue

        if not accountant.has_quota(provider):
            logger.debug(f"[Judge] Skip {provider}: quota exhausted")
            continue

        logger.info(
            f"[Judge] {job} → {model_id} ({provider}) "
            f"tier={tier} quality={model.quality_for(job):.2f}"
        )
        return model, tier

    # Emergency fallback — try anything healthy
    logger.error(f"[Judge] All options exhausted for {job}")
    return _emergency_fallback(), "tier2"


def _emergency_fallback() -> Model:
    """Last resort — return any healthy model."""
    from app.orchestrator import registry

    # Any healthy model
    for model in registry.all_models():
        if model.is_available():
            return model

    # Absolute last — first model regardless of state
    all_models = registry.all_models()
    if all_models:
        return all_models[0]

    raise RuntimeError("No models available — check provider configuration")


def get_ui_indicator(tier: str) -> str:
    """Subtle UI indicator for the quality tier."""
    return {
        "tier0": "⭐",   # Premium paid
        "tier1": "",     # Standard — no indicator
        "tier2": "⚡",   # Fast/local/degraded
    }.get(tier, "")

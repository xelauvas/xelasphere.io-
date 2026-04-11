"""
ModelWatcher — background daemon thread (one per Gunicorn worker).
Checks provider health every 60s+jitter, detects deprecations, discovers new models.
Coordinated via Firestore: if another worker checked <55s ago, skip.
"""
import logging
import random
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Set

import requests as http_requests

from app.orchestrator.config import PROVIDER_ENDPOINTS, FALLBACK_CHAINS, MODEL_FAMILIES
from app.orchestrator.models import Model

logger = logging.getLogger(__name__)

# Models that should never be auto-registered as chat candidates
IGNORED_MODEL_PATTERNS = frozenset({
    'whisper', 'tts-', 'dall-e', 'davinci-', 'babbage-',
    'embedding', 'moderation', 'realtime', 'audio',
    'canopylabs/', 'allam-',
})

_running = False
_thread: Optional[threading.Thread] = None


def start():
    """Called once at app startup."""
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_watch_loop, daemon=True, name="ModelWatcher")
    _thread.start()
    logger.info("[Watcher] Started")


def stop():
    global _running
    _running = False


def _watch_loop():
    # Stagger startup across workers
    time.sleep(random.randint(5, 30))

    while _running:
        try:
            _check_all_providers()
        except Exception as e:
            logger.error(f"[Watcher] Unexpected error: {e}", exc_info=True)
        # 60s + jitter to stagger across workers
        time.sleep(60 + random.randint(0, 30))


def _check_all_providers():
    from app.config import Config

    providers = {
        "groq":     getattr(Config, "GROQ_API_KEY", ""),
        "cerebras": getattr(Config, "CEREBRAS_API_KEY", ""),
        "gemini":   getattr(Config, "GEMINI_API_KEY", ""),
        "openai":   getattr(Config, "OPENAI_API_KEY", ""),
        "deepseek": getattr(Config, "DEEPSEEK_API_KEY", ""),
    }

    # Ollama is local — check separately
    ollama_url = getattr(Config, "OLLAMA_API_URL", None)
    if ollama_url:
        try:
            _check_ollama(ollama_url)
        except Exception as e:
            logger.warning(f"[Watcher] Ollama check failed: {e}")

    for provider, api_key in providers.items():
        if not api_key:
            continue
        try:
            _check_provider(provider, api_key)
        except Exception as e:
            logger.warning(f"[Watcher] Failed to check {provider}: {e}")


def _check_provider(provider: str, api_key: str):
    from app.config import db
    from google.cloud import firestore
    from app.orchestrator import registry

    # Coordination: skip if another worker checked recently
    health_ref = db.collection("orchestrator_health").document(provider)
    health_doc = health_ref.get()

    if health_doc.exists:
        data = health_doc.to_dict()
        last_check = data.get("last_check")
        if last_check:
            age = (datetime.utcnow() - last_check.replace(tzinfo=None)).total_seconds()
            if age < 55:
                # Another worker already checked — refresh local cache only
                _apply_health_to_registry(provider, data)
                return

    # Fetch live models from provider
    live_models = _fetch_live_models(provider, api_key)

    if live_models is None:
        _handle_provider_down(provider, health_ref, health_doc)
        return

    # Normalize model IDs — Gemini returns "models/gemini-2.0-flash", we store "gemini-2.0-flash"
    raw_ids = set(live_models.keys())
    _strip = (lambda x: x.removeprefix("models/")) if provider == "gemini" else (lambda x: x)
    live_ids = {_strip(mid) for mid in raw_ids}
    # Rebuild live_models with normalized keys for discovery
    live_models = {_strip(k): v for k, v in live_models.items()}

    known_models = registry.get_by_provider(provider)
    known_ids = {m.id for m in known_models if not m.is_deprecated}

    # Deprecation detection
    disappeared = known_ids - live_ids
    model_overrides = {}

    for model_id in disappeared:
        model = registry.get(model_id)
        if not model:
            continue

        replacement = _find_replacement(model, live_ids, provider)
        model.is_deprecated = True
        model.status = "down"
        model.replacement_id = replacement

        if replacement:
            model_overrides[model_id] = replacement

        logger.warning(
            f"⚠️ DEPRECATED [{provider}]: {model_id} "
            f"→ replacement: {replacement or 'none found'}"
        )

    # New model discovery
    new_ids = live_ids - known_ids
    new_models_list = []

    for model_id in new_ids:
        meta = live_models[model_id]
        new_model = _classify_new_model(model_id, meta, provider)
        if new_model is None:
            continue
        registry.add_discovered(new_model)
        new_models_list.append(model_id)

        logger.info(f"🆕 NEW [{provider}]: {model_id} → {new_model.strengths}")

    # Update health document
    health_ref.set({
        "provider": provider,
        "status": "healthy",
        "last_check": firestore.SERVER_TIMESTAMP,
        "last_healthy": firestore.SERVER_TIMESTAMP,
        "available_models": list(live_ids),
        "deprecated_models": list(disappeared),
        "new_models": new_models_list,
        "model_overrides": model_overrides,
        "consecutive_failures": 0,
    }, merge=True)

    # Update local registry
    for model in registry.get_by_provider(provider):
        if model.status in ("down", "degraded") and not model.is_deprecated:
            model.status = "healthy"
            model.consecutive_failures = 0

    if disappeared:
        logger.warning(
            f"[Watcher] {provider}: {len(disappeared)} deprecated, "
            f"{len(new_models_list)} new"
        )


def _fetch_live_models(provider: str, api_key: str) -> Optional[Dict]:
    """Fetch available models from provider API."""
    endpoint = PROVIDER_ENDPOINTS.get(provider)

    if not endpoint:
        return None

    try:
        resp = http_requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {m["id"]: m for m in data.get("data", [])}
    except Exception as e:
        logger.warning(f"[Watcher] {provider} /models failed: {e}")
        return None


def _check_ollama(ollama_url: str):
    """Check local Ollama health."""
    from app.orchestrator import registry

    # Ollama uses /api/tags not /v1/models
    base = ollama_url.rstrip("/").replace("/v1/chat/completions", "")
    try:
        resp = http_requests.get(f"{base}/api/tags", timeout=5)
        if resp.status_code == 200:
            for model in registry.get_by_provider("ollama"):
                if model.status in ("down", "degraded"):
                    model.status = "healthy"
        else:
            for model in registry.get_by_provider("ollama"):
                model.status = "down"
    except Exception:
        for model in registry.get_by_provider("ollama"):
            model.status = "down"


def _handle_provider_down(provider: str, health_ref, health_doc):
    """Handle a provider that didn't respond."""
    from google.cloud import firestore
    from app.orchestrator import registry

    consecutive = 0
    if health_doc and health_doc.exists:
        consecutive = health_doc.to_dict().get("consecutive_failures", 0)

    consecutive += 1
    new_status = "down" if consecutive >= 3 else "degraded"

    for model in registry.get_by_provider(provider):
        model.status = new_status
        model.consecutive_failures = consecutive

    health_ref.set({
        "status": new_status,
        "consecutive_failures": consecutive,
        "last_check": firestore.SERVER_TIMESTAMP,
    }, merge=True)

    logger.warning(f"[Watcher] {provider} {new_status} (failure #{consecutive})")


def _find_replacement(model: Model, live_ids: Set[str], provider: str) -> Optional[str]:
    """Find best replacement for a deprecated model."""
    name = model.id.lower()

    # Strategy 1: Same family, different version on same provider
    for family, patterns in MODEL_FAMILIES.items():
        if any(p in name for p in patterns):
            same_family = [
                mid for mid in live_ids
                if any(p in mid.lower() for p in patterns)
                and mid != model.id
            ]
            if same_family:
                return sorted(same_family)[-1]

    # Strategy 2: Cross-provider from fallback chain
    for job in model.strengths:
        chain = FALLBACK_CHAINS.get(job, [])
        for chain_provider, chain_model, tier in chain:
            if chain_provider != provider and tier == "tier1":
                from app.orchestrator import registry
                replacement = registry.get(chain_model)
                if replacement and replacement.is_available():
                    return chain_model

    return None


def _classify_new_model(model_id: str, meta: dict, provider: str) -> Optional[Model]:
    """Auto-classify a newly discovered model."""
    strengths = []
    name = model_id.lower()

    # Skip non-chat models (TTS, whisper, embeddings, legacy, etc.)
    if any(pat in name for pat in IGNORED_MODEL_PATTERNS):
        return None

    if any(x in name for x in ["vision", "vl", "maverick", "visual"]):
        strengths.append("vision")
    if any(x in name for x in ["coder", "code"]):
        strengths.append("coding")
    if any(x in name for x in ["r1", "reasoner", "thinking"]):
        strengths.append("reasoning")
    if any(x in name for x in ["8b", "instant", "flash", "nano", "mini", "scout"]):
        strengths.append("fast_chat")
    if any(x in name for x in ["guard", "safety", "moderation"]):
        strengths.append("safety")
    if any(x in name for x in ["dolphin", "uncensored"]):
        strengths.append("uncensored")

    if not strengths:
        strengths = ["general_chat"]
    elif not any(s in strengths for s in ("safety", "uncensored")):
        strengths.append("general_chat")

    cost_tier = "free" if provider in ("groq", "cerebras", "ollama") else "paid"
    context = meta.get("context_window", 32_000) if isinstance(meta, dict) else 32_000

    return Model(
        id=model_id,
        provider=provider,
        display_name=model_id,
        strengths=strengths,
        context_window=context,
        cost_tier=cost_tier,
        status="pending_review",
        quality_scores={s: 0.5 for s in strengths},
    )


def _apply_health_to_registry(provider: str, data: dict):
    """Apply a Firestore health document to local registry."""
    from app.orchestrator import registry

    status = data.get("status", "healthy")
    overrides = data.get("model_overrides", {})

    for model in registry.get_by_provider(provider):
        if model.id in overrides:
            model.is_deprecated = True
            model.replacement_id = overrides[model.id]
            model.status = "down"
        elif status == "down":
            model.status = "down"
        elif status == "degraded":
            model.status = "degraded"
        elif model.status in ("down", "degraded") and not model.is_deprecated:
            model.status = "healthy"

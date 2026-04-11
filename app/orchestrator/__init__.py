"""
Model Orchestrator — public API.

Usage:
    from app.orchestrator import get_model, record_usage

    model_id, provider, indicator = get_model("coding", {})
    # ... use model_id and provider to make API call ...
    record_usage(model_id, input_tokens=500, output_tokens=200)
"""
from app.orchestrator import registry, accountant, quality
from app.orchestrator.watcher import start as _start_watcher
from app.orchestrator.judge import hire, get_ui_indicator

_initialized = False


def initialize():
    """Call once at app startup."""
    global _initialized
    if _initialized:
        return

    registry.initialize()
    accountant.start()
    quality.start()
    _start_watcher()

    _initialized = True


def get_model(job: str, context: dict = None):
    """
    Main entry point. Returns (model_id, provider, tier_indicator).
    Never fails — always returns a usable model.
    """
    if not _initialized:
        initialize()

    model, tier = hire(job, context or {})
    indicator = get_ui_indicator(tier)
    return model.id, model.provider, indicator


def record_usage(model_id: str, input_tokens: int, output_tokens: int):
    """Call after each request to track quota."""
    model = registry.get(model_id)
    if model:
        accountant.record(model, input_tokens, output_tokens)


def signal_followup(model_id: str, job: str):
    """User sent a follow-up message — previous model did well."""
    quality.on_followup(model_id, job)


def signal_error(model_id: str, job: str):
    """Model returned an error."""
    quality.on_error(model_id, job)


def signal_short_response(model_id: str, job: str):
    """Model returned a very short response."""
    quality.on_short_response(model_id, job)


def get_quota_status():
    """Return quota status for all providers."""
    return accountant.get_status()

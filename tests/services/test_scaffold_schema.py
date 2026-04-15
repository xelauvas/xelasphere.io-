"""Tests for app.services.scaffold_schema.

W-04 — classifier response schema.
W-05 — provider capability map + get_json_mode().
W-14 — structured response schema (added later).
"""
import pytest
from jsonschema import Draft7Validator, ValidationError

from app.services.chat_engine import ALLOWED_LAYOUTS
from app.services.scaffold_schema import CLASSIFIER_RESPONSE_SCHEMA


# ---------- W-04 ----------


def test_classifier_schema_is_valid_jsonschema():
    Draft7Validator.check_schema(CLASSIFIER_RESPONSE_SCHEMA)


def test_classifier_schema_has_all_allowed_layouts():
    layout_enum = (
        CLASSIFIER_RESPONSE_SCHEMA
        ["properties"]["scaffold"]["properties"]["layout"]["enum"]
    )
    assert set(layout_enum) == set(ALLOWED_LAYOUTS), (
        "Schema layout enum must mirror ALLOWED_LAYOUTS — drift breaks the "
        "classifier validation contract."
    )


def test_classifier_schema_rejects_unknown_intent():
    bad = {
        "intent_type": "bogus_intent",
        "scaffold": _valid_scaffold(),
    }
    with pytest.raises(ValidationError):
        Draft7Validator(CLASSIFIER_RESPONSE_SCHEMA).validate(bad)


def test_classifier_schema_rejects_unknown_layout():
    bad = {
        "intent_type": "question",
        "scaffold": {**_valid_scaffold(), "layout": "spaghetti"},
    }
    with pytest.raises(ValidationError):
        Draft7Validator(CLASSIFIER_RESPONSE_SCHEMA).validate(bad)


def test_classifier_schema_rejects_out_of_range_max_paragraph():
    bad = {
        "intent_type": "question",
        "scaffold": {**_valid_scaffold(), "max_paragraph_sentences": 99},
    }
    with pytest.raises(ValidationError):
        Draft7Validator(CLASSIFIER_RESPONSE_SCHEMA).validate(bad)


def test_classifier_schema_validates_minimal_golden_sample():
    """Minimal but complete — every required key present."""
    good = {
        "intent_type": "question",
        "has_crypto_mention": False,
        "has_stock_mention": False,
        "asset_symbol": None,
        "wants_chart": False,
        "wants_image": False,
        "wants_code": False,
        "scaffold": _valid_scaffold(),
    }
    Draft7Validator(CLASSIFIER_RESPONSE_SCHEMA).validate(good)


def test_classifier_schema_validates_full_golden_sample():
    good = {
        "intent_type": "financial_query",
        "has_crypto_mention": True,
        "has_stock_mention": False,
        "asset_symbol": "BTC",
        "wants_chart": True,
        "wants_image": False,
        "wants_code": False,
        "scaffold": {
            "layout": "structured",
            "opening": "hook",
            "separator": True,
            "sections": [
                {"title": "Price", "emoji": "💰", "style": "prose"},
                {"title": "Trend", "emoji": "📈", "style": "prose"},
            ],
            "emphasis": "bold",
            "max_paragraph_sentences": 3,
            "list_threshold": 3,
            "width_hint": "medium",
        },
    }
    Draft7Validator(CLASSIFIER_RESPONSE_SCHEMA).validate(good)


def test_classifier_schema_allows_null_asset_symbol():
    good = {
        "intent_type": "general_chat",
        "has_crypto_mention": False,
        "has_stock_mention": False,
        "asset_symbol": None,
        "wants_chart": False,
        "wants_image": False,
        "wants_code": False,
        "scaffold": _valid_scaffold(),
    }
    Draft7Validator(CLASSIFIER_RESPONSE_SCHEMA).validate(good)


# ---------- W-05 — provider capability map ----------


from app.services.scaffold_schema import PROVIDER_JSON_MODE, get_json_mode


def test_provider_json_mode_has_all_catalog_providers():
    from app.model_catalog import MODEL_PROVIDERS
    catalog_providers = set(MODEL_PROVIDERS.values())
    mapped = set(PROVIDER_JSON_MODE.keys())
    missing = catalog_providers - mapped
    assert not missing, (
        f"PROVIDER_JSON_MODE missing entries for: {missing}. "
        "Every provider in model_catalog must map to 'schema' | 'json_mode'."
    )


def test_every_catalog_model_resolves_to_a_mode():
    from app.model_catalog import MODEL_PROVIDERS
    for model_id, provider in MODEL_PROVIDERS.items():
        mode = get_json_mode(provider, model_id)
        assert mode in ("schema", "json_mode"), (
            f"model={model_id!r} provider={provider!r} → mode={mode!r}; "
            "every catalog model must resolve to schema or json_mode."
        )


def test_get_json_mode_returns_schema_for_openai_gpt41():
    assert get_json_mode("openai", "gpt-4.1") == "schema"


def test_get_json_mode_falls_through_to_wildcard():
    # Some newly-announced Groq model we haven't listed specifically
    assert get_json_mode("groq", "future-groq-model-9000") == "json_mode"


def test_get_json_mode_returns_unsupported_for_unknown_provider():
    assert get_json_mode("not_a_real_provider", "any") == "unsupported"


def test_get_json_mode_specific_overrides_wildcard():
    """OpenAI wildcard could say json_mode, but gpt-4.1 specifically is schema."""
    assert get_json_mode("openai", "gpt-4.1-mini") == "schema"


def _valid_scaffold() -> dict:
    return {
        "layout": "conversational",
        "opening": "direct",
        "separator": False,
        "sections": [],
        "emphasis": "none",
        "max_paragraph_sentences": 3,
        "list_threshold": 3,
        "width_hint": "medium",
    }

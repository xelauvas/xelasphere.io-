"""Scaffold JSON schemas + provider capability map.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §4
Plan: docs/superpowers/plans/2026-04-15-structure-intelligence-v2-implementation.md

W-04 — CLASSIFIER_RESPONSE_SCHEMA
W-05 — PROVIDER_JSON_MODE + get_json_mode()
W-14 — STRUCTURED_RESPONSE_SCHEMA
"""
from typing import List

from app.services.chat_engine import (
    ALLOWED_LAYOUTS, ALLOWED_OPENINGS, ALLOWED_EMPHASIS, ALLOWED_WIDTHS,
)


# OpenAI strict-schema mode requires additionalProperties:false on every
# object, and every property listed as required. We comply.
_SECTION_SCHEMA = {
    "type": "object",
    "required": ["title", "emoji", "style"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "emoji": {"type": "string"},
        "style": {"enum": ["prose", "list", "steps"]},
    },
    "additionalProperties": False,
}


_SCAFFOLD_SCHEMA = {
    "type": "object",
    "required": [
        "layout", "opening", "separator", "sections",
        "emphasis", "max_paragraph_sentences", "list_threshold", "width_hint",
    ],
    "properties": {
        "layout":    {"enum": list(ALLOWED_LAYOUTS)},
        "opening":   {"enum": list(ALLOWED_OPENINGS)},
        "separator": {"type": "boolean"},
        "sections":  {"type": "array", "items": _SECTION_SCHEMA, "maxItems": 6},
        "emphasis":  {"enum": list(ALLOWED_EMPHASIS)},
        "max_paragraph_sentences": {"type": "integer", "minimum": 2, "maximum": 4},
        "list_threshold":          {"type": "integer", "minimum": 2, "maximum": 4},
        "width_hint": {"enum": list(ALLOWED_WIDTHS)},
    },
    "additionalProperties": False,
}


CLASSIFIER_RESPONSE_SCHEMA = {
    "type": "object",
    "required": [
        "intent_type", "has_crypto_mention", "has_stock_mention",
        "asset_symbol", "wants_chart", "wants_image", "wants_code", "scaffold",
    ],
    "properties": {
        "intent_type": {
            "enum": [
                "financial_query", "creative_task", "question",
                "recipe_request", "code_request", "image_request", "general_chat",
            ],
        },
        "has_crypto_mention": {"type": "boolean"},
        "has_stock_mention": {"type": "boolean"},
        "asset_symbol": {"type": ["string", "null"]},
        "wants_chart": {"type": "boolean"},
        "wants_image": {"type": "boolean"},
        "wants_code": {"type": "boolean"},
        "scaffold": _SCAFFOLD_SCHEMA,
    },
    "additionalProperties": False,
}


# ============================================================
# W-14 — Structured response schema
# ============================================================
# Emitted by the generation model (not the classifier) for structured
# layouts. Client renders to markdown via scaffoldRenderer.js (W-23).
#
# Strict mode compliance: every object has additionalProperties:false
# and every property is required (OpenAI schema mode requirement).
_RESPONSE_SECTION_SCHEMA = {
    "type": "object",
    "required": ["title", "emoji", "body", "style"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "emoji": {"type": "string"},
        "body":  {"type": "string", "minLength": 1},
        "style": {"enum": ["prose", "list", "steps"]},
    },
    "additionalProperties": False,
}


STRUCTURED_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "required": ["opening", "sections", "closing"],
    "properties": {
        "opening":  {"type": "string"},
        "sections": {"type": "array", "items": _RESPONSE_SECTION_SCHEMA, "maxItems": 10},
        "closing":  {"type": "string"},
    },
    "additionalProperties": False,
}


# ============================================================
# W-05 — Provider capability map
# ============================================================
# Two enforcement levels:
#   'schema'    — API-enforced json_schema validation at decode time
#                 (OpenAI gpt-4.1 family, Gemini). Compliance ~99%.
#   'json_mode' — API returns JSON-parseable text; caller validates
#                 shape against schema post-parse.
#
# Per-provider dict keys:
#   - specific model id → exact match wins
#   - "*"                → wildcard fallback for that provider
PROVIDER_JSON_MODE: dict = {
    "openai": {
        "gpt-4.1":      "schema",
        "gpt-4.1-mini": "schema",
        "gpt-4.1-nano": "schema",
        "gpt-4o":       "schema",
        "gpt-4o-mini":  "schema",
        # Reasoning models ship JSON mode only, not full schema enforcement.
        "o1":                     "json_mode",
        "o4-mini-deep-research":  "json_mode",
        "*":            "json_mode",
    },
    "gemini":   {"*": "schema"},
    "groq":     {"*": "json_mode"},
    "deepseek": {"*": "json_mode"},
    "moonshot": {"*": "json_mode"},
    "cerebras": {"*": "json_mode"},
    "ollama":   {"*": "json_mode"},
}


def get_json_mode(provider: str, model: str) -> str:
    """Resolve the structured-output mechanism for (provider, model).

    Returns 'schema' | 'json_mode' | 'unsupported'.
    Specific model entries win over the provider wildcard.
    """
    provider_map = PROVIDER_JSON_MODE.get(provider)
    if not provider_map:
        return "unsupported"
    if model in provider_map:
        return provider_map[model]
    return provider_map.get("*", "unsupported")

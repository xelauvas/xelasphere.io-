"""Structured-output generator for scaffolded chat responses.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §4.5
Plan: W-15 (native OpenAI schema path) + W-16 (JSON-mode fallback chain).

Emits exactly one chunk:
  {"type": "structured", "data": <parsed JSON matching STRUCTURED_RESPONSE_SCHEMA>}
on success, or
  {"type": "fallback_markdown", "reason": "..."}
when the caller should fall through to v1.1 markdown skeleton.
"""
import json
import logging
import os
import re
from typing import Any, Dict, Iterator, Optional

import requests
from jsonschema import Draft7Validator, ValidationError

from app.model_catalog import MODEL_PROVIDERS, resolve_model_id
from app.services.scaffold_schema import (
    STRUCTURED_RESPONSE_SCHEMA, get_json_mode,
)

logger = logging.getLogger(__name__)


# Endpoint by provider — matches app/config.py defaults.
_PROVIDER_ENDPOINTS = {
    "openai":   "https://api.openai.com/v1/chat/completions",
    "groq":     "https://api.groq.com/openai/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "moonshot": "https://api.moonshot.ai/v1/chat/completions",
    "cerebras": "https://api.cerebras.ai/v1/chat/completions",
}

_PROVIDER_KEY_ENV = {
    "openai":   "OPENAI_API_KEY",
    "groq":     "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}


def stream_structured_response(
    prompt: str,
    model: str,
    scaffold: Any,
    system_prompt_override: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Stream a structured response enforced against STRUCTURED_RESPONSE_SCHEMA.

    Yields a single chunk: structured (success) or fallback_markdown.
    See spec §4.5 + §4.8 fallback chain.
    """
    resolved = resolve_model_id(model) if model else model
    provider = MODEL_PROVIDERS.get(resolved, "groq")
    json_mode = get_json_mode(provider, resolved)

    if json_mode == "unsupported":
        yield {"type": "fallback_markdown", "reason": "provider_unsupported"}
        return

    endpoint = _PROVIDER_ENDPOINTS.get(provider)
    api_key = os.environ.get(_PROVIDER_KEY_ENV.get(provider, ""))
    if not endpoint or not api_key:
        yield {"type": "fallback_markdown", "reason": "no_endpoint_or_key"}
        return

    system_text = _build_system_prompt(scaffold, json_mode, system_prompt_override)

    # Up to two attempts: first call, then one retry with stricter wording.
    last_reason = "unknown"
    for attempt in range(2):
        body = _build_request_body(
            model=resolved, json_mode=json_mode,
            system_text=system_text, user_prompt=prompt,
            retry=(attempt == 1),
        )
        try:
            resp = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
        except requests.RequestException as e:
            last_reason = f"request_exception: {e}"
            break

        if resp.status_code != 200:
            last_reason = f"http_{resp.status_code}"
            # Retry only on 5xx — 4xx errors (bad schema etc.) won't fix themselves.
            if resp.status_code < 500:
                break
            continue

        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            last_reason = f"malformed_provider_envelope: {e}"
            break

        parsed = _parse_and_validate(content)
        if parsed is not None:
            yield {"type": "structured", "data": parsed}
            return
        last_reason = "schema_or_json_invalid"
        # Retry once — next loop iteration will flag retry=True.

    yield {"type": "fallback_markdown", "reason": last_reason}


def _build_system_prompt(scaffold: Any, json_mode: str,
                          system_prompt_override: Optional[str]) -> str:
    """Build the generator's system message.

    When json_mode == 'schema', the API enforces shape — we only need to
    describe intent and section titles. When json_mode == 'json_mode',
    we also inline the schema text so the model sees the exact shape.
    """
    sections = getattr(scaffold, "sections", None) or []
    section_titles = [
        s.get("title", "") if isinstance(s, dict) else getattr(s, "title", "")
        for s in sections
    ]

    parts = [
        system_prompt_override or "You are XelaSphere AI.",
        "",
        "Produce ONE JSON object answering the user's message. "
        "Each element of `sections` must have a matching `title` in this "
        f"exact order: {section_titles}.",
        "`opening` is a 1-3 sentence hook framing the topic. "
        "`closing` is optional (may be empty).",
        "Each `section.body` is a markdown-safe string: 2-3 paragraphs "
        "(blank-line separated) for style=prose, a bullet list for "
        "style=list, a numbered list for style=steps.",
    ]
    if json_mode == "json_mode":
        parts.extend([
            "",
            "Response schema (match exactly — no other keys, no markdown fence):",
            json.dumps(STRUCTURED_RESPONSE_SCHEMA, separators=(",", ":")),
        ])
    return "\n".join(parts)


def _build_request_body(*, model: str, json_mode: str, system_text: str,
                         user_prompt: str, retry: bool) -> Dict[str, Any]:
    if retry:
        system_text = (
            system_text
            + "\n\nCRITICAL: Your previous response did not match the schema. "
            "Return ONLY the JSON object — no preamble, no markdown fence, "
            "no trailing text. Every field required. Every section in order."
        )
    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.3,
    }
    if json_mode == "schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "schema": STRUCTURED_RESPONSE_SCHEMA,
                "strict": True,
            },
        }
    else:  # json_mode
        body["response_format"] = {"type": "json_object"}
    return body


def _parse_and_validate(content: str) -> Optional[dict]:
    """Extract JSON from content, validate against STRUCTURED_RESPONSE_SCHEMA."""
    try:
        match = re.search(r"\{", content, re.DOTALL)
        if not match:
            return None
        parsed, _ = json.JSONDecoder().raw_decode(content, match.start())
    except (json.JSONDecodeError, ValueError):
        return None
    try:
        Draft7Validator(STRUCTURED_RESPONSE_SCHEMA).validate(parsed)
    except ValidationError as e:
        logger.debug(f"Structured payload failed schema: {e.message}")
        return None
    return parsed

"""Structured-output generator for scaffolded chat responses.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §4.5
Plan: W-02 skeleton → W-15 (OpenAI native) → W-16 (JSON mode + fallback).

At W-02 this module is a stub. stream_structured_response() raises
NotImplementedError so nothing wires it in accidentally before W-15.
"""
from typing import Any, Dict, Iterator, Optional


def stream_structured_response(
    prompt: str,
    model: str,
    scaffold: Any,
    system_prompt_override: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Stream a structured (JSON-schema-enforced) response.

    Yields {type: "structured", data: {...}} on success, or
    {type: "fallback_markdown", reason: "..."} when the caller should
    fall through to the v1.1 markdown skeleton path.

    Implementation lands in W-15 (native OpenAI schema) and W-16
    (JSON-mode fallback chain).
    """
    raise NotImplementedError("stream_structured_response — scheduled for W-15")

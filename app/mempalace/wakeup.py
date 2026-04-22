"""M0.17 Stage 1 RED — wake-up formatters scaffold.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4056-4137 (Task M0.17.2). Provider-specific formatters that turn
a sidecar ``WakeupContext`` (as a dict — not the Pydantic model, because
the adapter receives JSON-deserialized sidecar responses) into a
system-prompt block for each model family.

Stage 1 RED shape: functions exist; bodies raise NotImplementedError so
tests fail predictably. Stage 3 GREEN lands the real rendering.
"""

from __future__ import annotations


def _render_drawer(d: dict) -> str:
    raise NotImplementedError("M0.17 Stage 3 GREEN lands _render_drawer")


def to_anthropic_system(ctx: dict) -> str:
    raise NotImplementedError("M0.17 Stage 3 GREEN lands to_anthropic_system")


def to_openai_system_msg(ctx: dict) -> dict:
    raise NotImplementedError("M0.17 Stage 3 GREEN lands to_openai_system_msg")


def to_gemini_contents(ctx: dict) -> list[dict]:
    raise NotImplementedError("M0.17 Stage 3 GREEN lands to_gemini_contents")


def to_local_prompt_block(ctx: dict) -> str:
    raise NotImplementedError("M0.17 Stage 3 GREEN lands to_local_prompt_block")


__all__ = [
    "to_anthropic_system",
    "to_openai_system_msg",
    "to_gemini_contents",
    "to_local_prompt_block",
]

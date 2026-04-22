"""Wake-up context formatters for each model-family system prompt (M0.17).

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4056-4137 (Task M0.17.2). Spec §3: "Orchestrator's per-model
adapter picks the right formatter — formatting does not live inside
the palace adapter."

All formatters take a ``ctx`` dict (JSON-deserialized sidecar
``WakeupContext`` response) with keys:
  ``user_id``, ``schema_version``, ``l0_recent``, ``l1_session``,
  ``l2_topic``, ``l3_semantic``, ``meta``

Each Lx layer is a list of Drawer dicts with keys:
  ``text``, ``wing``, ``room``, ``ts``, ``pair_id``, ``score``

``wing`` and ``room`` are ``Optional[str]`` per the sidecar schema —
the renderer treats None identically to empty-string (omits the
corresponding prefix segment) so sidecar responses with null fields
don't leak the literal "None" into model context.
"""

from __future__ import annotations


_LAYER_LABELS = (
    ("Recent", "l0_recent"),
    ("Session", "l1_session"),
    ("Topic", "l2_topic"),
    ("Semantic", "l3_semantic"),
)


def _render_drawer(d: dict) -> str:
    """Render one drawer as ``[ts · wing/room] text``.

    Optional wing/room semantics: absent or None → segment omitted
    (no "None" leakage). Empty string handled identically to None.
    """
    ts = d.get("ts", "?")
    wing = d.get("wing") or ""
    room = d.get("room") or ""
    text = d.get("text", "")
    prefix = f"[{ts}"
    if wing:
        prefix += f" · {wing}"
        if room:
            prefix += f"/{room}"
    elif room:
        # Room without wing is unusual but the renderer shouldn't drop it;
        # prepend a bullet so the room remains visible.
        prefix += f" · /{room}"
    prefix += "]"
    return f"{prefix} {text}"


def to_anthropic_system(ctx: dict) -> str:
    """Anthropic system-block rendering.

    Emits ``# Memory context (verbatim)`` header, then a ``## <Label>``
    section per non-empty layer. Empty layers are omitted (no section
    header, no body) — avoids wasting tokens and reduces confusion.
    """
    lines: list[str] = ["# Memory context (verbatim)"]
    for label, key in _LAYER_LABELS:
        entries = ctx.get(key) or []
        if not entries:
            continue
        lines.append(f"## {label}")
        for d in entries:
            lines.append(_render_drawer(d))
    return "\n".join(lines)


def to_openai_system_msg(ctx: dict) -> dict:
    """OpenAI Chat Completions system-role message wrapper around the
    Anthropic rendering. OpenAI uses role-keyed messages, so we package
    the anthropic text as the ``content`` field with ``role="system"``.
    """
    return {"role": "system", "content": to_anthropic_system(ctx)}


def to_gemini_contents(ctx: dict) -> list[dict]:
    """Gemini ``contents`` array rendering. Gemini doesn't have a
    "system" role; conventional pattern is to prepend a user message
    containing the system-style context. Single-element list keeps the
    shape compatible with the ``contents`` parameter.
    """
    return [
        {"role": "user", "parts": [{"text": to_anthropic_system(ctx)}]}
    ]


def to_local_prompt_block(ctx: dict) -> str:
    """Local-model plain-string rendering. Same content as the anthropic
    rendering, returned as bare text for concatenation into a
    non-chat-formatted prompt.
    """
    return to_anthropic_system(ctx)


__all__ = [
    "to_anthropic_system",
    "to_openai_system_msg",
    "to_gemini_contents",
    "to_local_prompt_block",
]

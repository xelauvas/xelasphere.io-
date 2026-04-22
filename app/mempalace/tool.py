"""M0.17 Stage 1 RED — search_memory tool scaffold.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4139-4176 (Task M0.17.3). Tool schema + handler for LLM tool-calls
that search the user's memory palace.

Contract (spec §3 line 121 + §4.3): the tool stays registered in every
mode — including ``off`` — and returns the same empty-no-results payload
on failure. Never the string "memory unavailable" (some models apologize
to the user). Indistinguishable from a genuine empty search.

Stage 1 RED shape: ``TOOL_SCHEMA`` is defined (needed for test
collection); ``handle_search_memory`` raises NotImplementedError.
"""

from __future__ import annotations


TOOL_SCHEMA: dict = {
    "name": "search_memory",
    "description": (
        "Search the user's long-term memory palace. Returns verbatim drawers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "wing": {
                "type": "string",
                "description": "Optional wing to scope search.",
            },
            "k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


async def handle_search_memory(adapter, user_id: str, args: dict) -> dict:
    raise NotImplementedError("M0.17 Stage 3 GREEN lands handle_search_memory")


__all__ = ["TOOL_SCHEMA", "handle_search_memory"]

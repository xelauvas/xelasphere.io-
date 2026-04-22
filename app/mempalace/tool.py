"""search_memory tool schema + handler for LLM tool-calls (M0.17).

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4139-4176 (Task M0.17.3). Spec §3 line 121 + §4.3.

**No-regression contract (spec §3 line 121).** The tool stays registered
in EVERY mode, including ``off``. In ``off`` the adapter's ``search()``
returns ``[]`` and the handler produces ``{"results": []}`` — the same
shape a genuine empty search returns. Failure modes produce
``{"results": [], "note": "no results"}`` — the ``note`` distinguishes
"we tried but nothing worked" from "we succeeded and found nothing" for
forensic log analysis, but both shapes are indistinguishable to the
model (no "unavailable" language). Never mutate the advertised tool
list based on feature-flag state; some providers break on mid-
conversation tool-list changes.
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
            # Spec M5 fold: bounds mirror site-wide convention for numeric
            # inputs (e.g., sidecar list_drawers Query(ge=1, le=200) at
            # routes/palace.py:371). Hostile or confused tool-calls with
            # k=0/k=-1/k=10000 fail schema validation at the LLM provider
            # layer rather than propagating through the adapter.
            "k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    },
}


async def handle_search_memory(adapter, user_id: str, args: dict) -> dict:
    """Dispatch a ``search_memory`` tool call to ``adapter.search``.

    Returns ``{"results": [...]}`` on success (including the empty-list
    case from off-mode or genuine empty search). Returns
    ``{"results": [], "note": "no results"}`` on any exception — the
    shape matches empty-results for the model, but the ``note`` field
    is forensic signal for post-hoc log analysis.

    Drawer serialization: each result may be a Pydantic BaseModel with
    ``.model_dump()`` OR a plain dict (the sidecar returns JSON; the
    client returns the parsed list-of-dicts at runtime). Handle both
    via duck-typing on ``model_dump`` attribute.
    """
    try:
        drawers = await adapter.search(
            user_id=user_id,
            query=args["query"],
            wing=args.get("wing"),
            k=int(args.get("k", 5)),
        )
        return {
            "results": [
                d.model_dump() if hasattr(d, "model_dump") else d
                for d in drawers
            ]
        }
    except Exception:
        # Spec §3 line 121: indistinguishable from empty-results; never
        # the word "unavailable." The `note` is log-only signal.
        return {"results": [], "note": "no results"}


__all__ = ["TOOL_SCHEMA", "handle_search_memory"]

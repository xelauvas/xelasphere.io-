"""Post-stream compliance auditor for scaffolded responses.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §5
Plan: W-02 skeleton → W-17 (markdown parser) → W-18 (structured short-circuit).

Fires after every chat stream completes. Parses the rendered output,
compares against the planned scaffold, emits an AuditReport. The
caller (chat_routes.py, W-20) writes the report to Firestore.

Never breaks the stream — any audit error is logged and dropped.
"""
from typing import Any, Dict, Optional


def audit_response(
    scaffold: Any,
    rendered_output: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute a compliance report for a completed stream.

    Returns a dict matching the Firestore schema in spec §5.4.
    Implementation lands in W-17 (markdown parser) and W-18
    (structured JSON short-circuit).
    """
    raise NotImplementedError("audit_response — scheduled for W-17")

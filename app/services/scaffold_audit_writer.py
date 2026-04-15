"""Firestore writer for scaffold audit reports.

Spec: docs/superpowers/specs/2026-04-15-structure-intelligence-v2-design.md §5.4-5.5
Plan: W-19

Called from chat_routes (W-20) after every completed stream. Fire-and-forget,
never raises, hashes user_id before storing. If Firestore is unavailable
(e.g. local dev without creds), logs and returns — no crash.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_COLLECTION_NAME = "scaffold_audits"


def write_scaffold_audit(
    report: Dict[str, Any],
    *,
    user_id: Optional[str],
    session_id: Optional[str],
    model: str,
    provider: str,
) -> None:
    """Write a scaffold audit report to Firestore.

    Never raises — all exceptions are caught and logged. The calling
    stream has already completed; audit infra can't break it.
    """
    try:
        db = _get_db()
        if db is None:
            logger.debug("[audit] Firestore client unavailable — skipping write")
            return
        payload = _build_payload(
            report=report, user_id=user_id, session_id=session_id,
            model=model, provider=provider,
        )
        db.collection(_COLLECTION_NAME).add(payload)
    except Exception as e:
        logger.warning(f"[audit] Firestore write failed (swallowed): {e}")


def _build_payload(
    *,
    report: Dict[str, Any],
    user_id: Optional[str],
    session_id: Optional[str],
    model: str,
    provider: str,
) -> Dict[str, Any]:
    """Assemble the document per spec §5.4."""
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return {
        **report,
        "ts": ts,
        "user_id_hash": _hash_user_id(user_id),
        "session_id": session_id or "",
        "model": model,
        "provider": provider,
    }


def _hash_user_id(user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


def _get_db() -> Optional[Any]:
    """Resolve the Firestore client, returning None on any failure.

    Separated so tests can monkeypatch it without needing Firebase creds.
    """
    try:
        from app.config import db  # project's shared Firestore client
        return db
    except Exception as e:
        logger.debug(f"[audit] Firestore import failed: {e}")
        return None

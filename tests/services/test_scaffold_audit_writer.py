"""W-19 — Firestore audit writer behavior.

Writes scaffold audit reports to the `scaffold_audits` Firestore collection.
Hashes user_id before storing. Swallows errors so stream completion is
never affected by audit infra failures.
"""
import hashlib
from unittest.mock import MagicMock, patch

import pytest


def test_writer_serializes_report_to_firestore():
    from app.services.scaffold_audit_writer import write_scaffold_audit
    report = {
        "layout": "structured",
        "render_path": "structured",
        "metrics": {"compliance_rate": 0.95},
    }
    mock_db = MagicMock()
    with patch("app.services.scaffold_audit_writer._get_db", return_value=mock_db):
        write_scaffold_audit(
            report, user_id="u_alice", session_id="s_123",
            model="gpt-4.1-mini", provider="openai",
        )
    # db.collection('scaffold_audits').add(payload) — check the call shape:
    coll_call = mock_db.collection.call_args
    assert coll_call.args[0] == "scaffold_audits"
    added = mock_db.collection.return_value.add.call_args.args[0]
    # Must include the report fields and the metadata:
    assert added["layout"] == "structured"
    assert added["session_id"] == "s_123"
    assert added["model"] == "gpt-4.1-mini"
    assert added["provider"] == "openai"
    # Timestamp present:
    assert "ts" in added


def test_writer_hashes_user_id():
    from app.services.scaffold_audit_writer import write_scaffold_audit
    mock_db = MagicMock()
    with patch("app.services.scaffold_audit_writer._get_db", return_value=mock_db):
        write_scaffold_audit(
            {"layout": "prose", "metrics": {}},
            user_id="u_alice", session_id="s_x",
            model="m", provider="p",
        )
    added = mock_db.collection.return_value.add.call_args.args[0]
    # Raw user_id must not be present; a hash prefix must be.
    assert "user_id" not in added
    assert added["user_id_hash"] == hashlib.sha256(
        "u_alice".encode("utf-8")
    ).hexdigest()[:16]


def test_writer_handles_none_user_id():
    """Guest sessions have no user_id — write an empty hash marker."""
    from app.services.scaffold_audit_writer import write_scaffold_audit
    mock_db = MagicMock()
    with patch("app.services.scaffold_audit_writer._get_db", return_value=mock_db):
        write_scaffold_audit(
            {"layout": "prose", "metrics": {}},
            user_id=None, session_id="s_x", model="m", provider="p",
        )
    added = mock_db.collection.return_value.add.call_args.args[0]
    assert added["user_id_hash"] == ""


def test_writer_swallows_firestore_exceptions():
    """Audit failure must never bubble — stream already completed."""
    from app.services.scaffold_audit_writer import write_scaffold_audit
    mock_db = MagicMock()
    mock_db.collection.return_value.add.side_effect = RuntimeError("firestore down")
    with patch("app.services.scaffold_audit_writer._get_db", return_value=mock_db):
        # Must NOT raise:
        write_scaffold_audit(
            {"layout": "prose", "metrics": {}},
            user_id="u", session_id="s", model="m", provider="p",
        )


def test_writer_skips_when_db_unavailable():
    """If Firestore client can't be obtained, just log and return — no crash."""
    from app.services.scaffold_audit_writer import write_scaffold_audit
    with patch("app.services.scaffold_audit_writer._get_db", return_value=None):
        write_scaffold_audit(
            {"layout": "prose", "metrics": {}},
            user_id="u", session_id="s", model="m", provider="p",
        )
        # No exception = success.

"""Unit tests for mempalace_sidecar.schemas (plan M0.4.1).

These are fast, library-free tests that exercise the Pydantic models on the
HTTP surface. The richer integration-level behavior (duplicate/advisory-key
flow) lives in ``tests/integration/test_ingest_idempotency.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mempalace_sidecar.schemas import IngestRequest


def test_ingest_request_roundtrip() -> None:
    """A well-formed ingest body parses and preserves all fields.

    Plan M0.4.1 Step 1. The turn_id/pair_id shape here is the UUIDv7 form
    upstream callers (Flask outbox M0.15) will send, but the model itself
    treats these as opaque strings; the test asserts survival, not format.
    """
    r = IngestRequest(
        turn_id="018f1bce-9ab7-7bca-baaa-000000000000",
        session_id="s1",
        pair_id="018f1bce-9ab7-7bca-baaa-111111111111",
        role="user",
        text="hello",
        ts=1_700_000_000.0,
    )
    assert r.role == "user"
    assert r.turn_id == "018f1bce-9ab7-7bca-baaa-000000000000"
    assert r.session_id == "s1"
    assert r.pair_id == "018f1bce-9ab7-7bca-baaa-111111111111"
    assert r.text == "hello"
    assert r.ts == 1_700_000_000.0
    # attachments is optional — absence must not default to something truthy.
    assert r.attachments is None


def test_ingest_request_rejects_bad_role() -> None:
    """role is ``Literal["user", "assistant"]``; anything else is a 422.

    Plan M0.4.1 Step 1. This is the contract the route layer relies on —
    without it the handle would accept a role it can't store (the
    ``turns.sqlite`` schema has a CHECK constraint on this column, but we
    want the error surface at the HTTP layer, not at sqlite insert time).
    """
    with pytest.raises(ValidationError):
        IngestRequest(
            turn_id="x",
            session_id="s",
            pair_id="y",
            role="system",  # type: ignore[arg-type]
            text="hi",
            ts=1.0,
        )

"""Integration tests for ``POST /palace/{user_id}/rebuild`` (plan M0.8.2).

Three mandatory tests:

- ``test_rebuild_is_idempotent`` — the load-bearing property that
  makes rebuild safe to retry. Second rebuild of the same
  conversations is all-skipped, zero ingested. Relies on
  ``turns.sqlite`` PK dedup from M0.4.
- ``test_rebuild_counts_accepted_and_duplicates`` — mixed path:
  some conversations are new, some already exist from prior
  ingests. Result should split correctly.
- ``test_rebuild_rejects_over_100_conversations`` — the 100-cap
  from deviation #7 (originally 500 in the brief, dropped to 100
  per Q3 follow-up on writer_lock hold time).

All three fail at RED because the route is a 501 stub.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


def _make_conv(i: int, session: str = "s") -> dict:
    """Build a single conversation dict matching IngestRequest shape."""
    return {
        "turn_id": f"t{'0' * 30}{i:02d}",
        "session_id": session,
        "pair_id": f"p{'0' * 30}{i:02d}",
        "role": "user" if i % 2 == 0 else "assistant",
        "text": f"conversation turn number {i}",
        "ts": 1_700_000_000.0 + i,
    }


async def test_rebuild_is_idempotent(client, auth) -> None:
    """Rebuild the same conversations twice. First pass: all
    ``ingested``. Second pass: all ``skipped``. ``turns.sqlite``
    PK blocks the library call for duplicate turn_ids — same
    idempotency contract as /ingest at M0.4.
    """
    conversations = [_make_conv(i) for i in range(5)]

    r1 = await client.post(
        "/palace/alice/rebuild",
        headers=auth,
        json={"conversations": conversations},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["ingested"] == 5, (
        f"first rebuild should ingest all 5, got {body1}"
    )
    assert body1["skipped"] == 0

    r2 = await client.post(
        "/palace/alice/rebuild",
        headers=auth,
        json={"conversations": conversations},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["ingested"] == 0, (
        f"second rebuild of same conversations should ingest nothing, "
        f"got {body2}"
    )
    assert body2["skipped"] == 5


async def test_rebuild_counts_accepted_and_duplicates(
    client, auth
) -> None:
    """Mixed-input rebuild: 5 conversations, 2 of which duplicate
    already-ingested turns. Result must split 3 ingested / 2
    skipped.
    """
    # Pre-ingest 2 turns with specific IDs.
    for i in (10, 11):
        r = await client.post(
            "/palace/bob/ingest",
            headers=auth,
            json=_make_conv(i),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "accepted"

    # Rebuild with 5 conversations — two of them are the pre-ingested.
    conversations = [
        _make_conv(10),  # duplicate
        _make_conv(12),  # new
        _make_conv(13),  # new
        _make_conv(11),  # duplicate
        _make_conv(14),  # new
    ]
    r = await client.post(
        "/palace/bob/rebuild",
        headers=auth,
        json={"conversations": conversations},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 3, (
        f"expected 3 accepted (12, 13, 14), got {body['ingested']}; "
        f"full body {body}"
    )
    assert body["skipped"] == 2, (
        f"expected 2 duplicates (10, 11), got {body['skipped']}; "
        f"full body {body}"
    )


async def test_rebuild_rejects_over_100_conversations(
    client, auth
) -> None:
    """Deviation #7 cap. 101 conversations must return HTTP 413,
    not silently accept or hold the writer_lock for minutes.

    Bound exists because rebuild holds the per-user writer_lock for
    the duration, and at ~100ms per drawer, 100 conversations = ~10s
    worst-case lock hold — the ceiling for not timing out parallel
    reads at Flask's 2s client budget.
    """
    conversations = [_make_conv(i) for i in range(101)]
    r = await client.post(
        "/palace/eve/rebuild",
        headers=auth,
        json={"conversations": conversations},
    )
    assert r.status_code == 413, (
        f"101-conversation rebuild should return 413, got {r.status_code}: "
        f"{r.text}"
    )

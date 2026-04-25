"""Integration tests for GET /palace/{user_id}/drawers (plan M0.6.2).

Exercises the paginated GDPR-admin listing route end-to-end: HTTP →
FastAPI → PalaceRegistry → PalaceHandle.list_drawers → raw Chroma
``col.get(limit, offset)``. Marked ``integration``.

Two tests (the second is teeth-bearing by plan):

  * ``test_drawers_pagination_walks_full_collection`` — seeds five
    drawers, walks with ``limit=2``, asserts all three pagination
    boundary cases in one run: full page (``next_offset=offset+limit``),
    short page (``next_offset=None``), and past-the-end
    (``offset>=total`` → empty list + ``next_offset=None``).

  * ``test_drawers_filtered_by_wing_returns_none_total`` — seeds via
    ``handle.add_drawer`` directly under the writer_lock (HTTP /ingest
    has no wing field), 3 kitchen + 2 bath. Filter by wing=kitchen,
    asserts the honest-totals contract: when a filter applies,
    ``total`` MUST be None (not an unfiltered integer that the Flask
    adapter would render as a lie). See deltas "2026-04-20 list_drawers
    honest totals" for the rationale.

Why seed via handle for the wing test: ``IngestRequest`` doesn't carry
wing/room fields (by design — wing/room are library-inferred from
content in v1), but ``PalaceHandle.add_drawer`` does accept explicit
``wing=`` kwargs. Seeding directly lets the test exercise the
filtered-listing path without changing the HTTP surface. Deliberate
test-only shortcut documented in the M0.6 dispatch prompt's
"Fixture seeding for wing-filter test" section.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


async def test_drawers_pagination_walks_full_collection(client, auth) -> None:
    """Plan M0.6.2 teeth-bearing pagination contract.

    Seed five drawers via HTTP, walk with ``limit=2``:

        offset=0 → len=2, total=5, next_offset=2
        offset=2 → len=2, total=5, next_offset=4
        offset=4 → len=1, total=5, next_offset=None     (short page)
        offset=5 → len=0, total=5, next_offset=None     (past the end)

    All four cases in one test — a broken implementation that drops
    ``next_offset=None`` on the short page (keeps returning
    ``offset+limit``) fails the third assertion; one that always
    returns the same total regardless of offset fails none of them, but
    the fourth case catches the "infinite loop" version where offset
    past the end returns 500 or wraps back to 0.
    """
    for i in range(5):
        r = await client.post(
            "/palace/u/ingest",
            headers=auth,
            json={
                "turn_id": f"p{'0' * 30}{i:02d}",
                "session_id": "s",
                "pair_id": f"q{'0' * 30}{i:02d}",
                "role": "user",
                "text": f"drawer number {i} with enough words to be real",
                "ts": 1_700_000_000.0 + i,
            },
        )
        assert r.status_code == 200, r.text

    # offset=0 — full page.
    r = await client.get("/palace/u/drawers?limit=2&offset=0", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 5, f"expected total=5, got {page['total']}"
    assert page["next_offset"] == 2, f"expected next_offset=2, got {page['next_offset']}"
    assert len(page["drawers"]) == 2, f"expected 2 drawers, got {len(page['drawers'])}"

    # offset=2 — full page.
    r = await client.get("/palace/u/drawers?limit=2&offset=2", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 5
    assert page["next_offset"] == 4, f"expected next_offset=4, got {page['next_offset']}"
    assert len(page["drawers"]) == 2

    # offset=4 — short page (one drawer left).
    r = await client.get("/palace/u/drawers?limit=2&offset=4", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 5
    assert page["next_offset"] is None, (
        f"short page must set next_offset=None, got {page['next_offset']}"
    )
    assert len(page["drawers"]) == 1, f"expected 1 drawer, got {len(page['drawers'])}"

    # offset=5 — past the end (empty list).
    r = await client.get("/palace/u/drawers?limit=2&offset=5", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 5
    assert page["next_offset"] is None
    assert page["drawers"] == [], f"past-end page must be empty, got {page['drawers']}"


async def test_drawers_filtered_by_wing_returns_none_total(app, client, auth) -> None:
    """Honest-totals contract (deltas "2026-04-20 list_drawers honest totals").

    When wing or room filter applies, ``total`` MUST be None — Chroma's
    ``col.count()`` has no filter argument, so returning an unfiltered
    total under a filter would give "showing 3 of 8" when the real
    filtered set is exactly 3 (a user-visible lie). ``None`` lets the
    Flask adapter render "showing 3 of many" or omit the count.

    Seed 3 kitchen drawers + 2 bath drawers directly via
    ``handle.add_drawer`` (HTTP /ingest has no wing field). Filter by
    wing=kitchen; assert len=3, total=None, next_offset=None.
    """
    # Driving any /palace route first ensures the registry is ready and
    # the palace exists before we reach in. Use a lightweight call.
    r = await client.get("/palace/wingu/drawers?limit=1&offset=0", headers=auth)
    assert r.status_code == 200, r.text  # empty palace is fine, just warm it

    registry = app.state.registry
    entry = await registry.open("wingu")
    lock = registry.lock_for("wingu")

    seeds = [
        ("kitchen", "baked sourdough"),
        ("kitchen", "pasta from scratch"),
        ("kitchen", "tried a new curry"),
        ("bath", "re-tiled the shower"),
        ("bath", "replaced the faucet"),
    ]
    async with lock.writer_lock:
        for i, (wing, text) in enumerate(seeds):
            await asyncio.to_thread(
                entry.handle.add_drawer,
                turn_id=f"w{'0' * 30}{i:02d}",
                session_id="s",
                pair_id=f"v{'0' * 30}{i:02d}",
                role="user",
                text=text,
                ts=1_700_000_100.0 + i,
                wing=wing,
            )

    r = await client.get(
        "/palace/wingu/drawers?wing=kitchen&limit=10&offset=0", headers=auth
    )
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] is None, (
        f"wing-filter must produce total=None per honest-totals contract; "
        f"got {page['total']}"
    )
    assert len(page["drawers"]) == 3, (
        f"expected 3 kitchen drawers, got {len(page['drawers'])}: "
        f"{[d['text'] for d in page['drawers']]}"
    )
    assert page["next_offset"] is None, (
        f"short filtered page must set next_offset=None, got {page['next_offset']}"
    )
    # Every returned drawer should actually be in the kitchen.
    for d in page["drawers"]:
        assert d.get("wing") == "kitchen" or "kitchen" in (d.get("wing") or ""), (
            f"filtered page contained non-kitchen drawer: {d}"
        )

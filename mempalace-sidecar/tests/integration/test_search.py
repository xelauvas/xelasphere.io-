"""Integration tests for POST /palace/{user_id}/search (plan M0.6.1).

Exercises the tool-call search route end-to-end: HTTP → FastAPI →
PalaceRegistry → PalaceHandle.search → real Chroma hybrid-retrieval.
Marked ``integration`` because every run opens a real palace (embedding
model + Chroma PersistentClient + sqlite). A unit-level test would have
to mock PalaceHandle.search, which mocks past the one interesting
property — that the route actually surfaces library hits in rank order.

Teeth-bearing: we seed SIX drawers with one clear semantic match and
five unrelated ones, then query with ``k=3``. A broken /search that
returned random drawers (or returned all drawers ignoring k) would fail
because the semantic-match drawer has to be in the top-3 out of six.
Position-0 assertions would be flake-prone across chromadb versions;
"must appear in top-3 out of six" is robust to ordering jitter without
giving away the test.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_search_finds_keyword_in_top_k(client, auth) -> None:
    """Plan M0.6.1 Step 1, strengthened from the plan-floor ``any(...)``.

    Seed six drawers: one about GraphQL (the target) + five unrelated
    kitchen/bathroom chatter. Query ``"GraphQL"`` with ``k=3``. The
    GraphQL drawer MUST appear in the returned results — it's the only
    one with any semantic overlap with the query, and asking for top-3
    out of six means the route has to actually rank, not just return
    everything.
    """
    drawers = [
        "baked sourdough bread recipe over the weekend",
        "tried a new pasta dish with clam sauce",
        "deep-cleaned the kitchen counters",
        "re-tiled the bathroom shower wall",
        "fixed the leaky faucet in the downstairs bath",
        "GraphQL schema migration is complete and the resolver tests pass",
    ]
    for i, text in enumerate(drawers):
        r = await client.post(
            "/palace/u/ingest",
            headers=auth,
            json={
                "turn_id": f"s{'0' * 30}{i:02d}",
                "session_id": "s",
                "pair_id": f"p{'0' * 30}{i:02d}",
                "role": "user",
                "text": text,
                "ts": 1_700_000_000.0 + i,
            },
        )
        assert r.status_code == 200, r.text

    r = await client.post(
        "/palace/u/search",
        headers=auth,
        json={"query": "GraphQL", "k": 3},
    )
    assert r.status_code == 200, r.text
    results = r.json()
    assert isinstance(results, list), f"expected list, got {type(results)}"
    assert len(results) <= 3, f"k=3 but got {len(results)} results"
    assert any("GraphQL" in d["text"] for d in results), (
        "GraphQL drawer should appear in top-3 out of six: "
        f"got {[d['text'][:30] for d in results]}"
    )


async def test_search_empty_palace_returns_empty_list(client, auth) -> None:
    """Query against a user with no drawers. The route MUST NOT 404 or
    500; it returns 200 with an empty list. This is the "palace exists
    but is cold/empty" path — first call creates the palace, search
    finds nothing, route returns [].

    Exercises the ``raws or []`` coalesce in the route: some library
    search paths return ``None`` instead of ``[]`` for zero-hit queries,
    and the route must normalise.
    """
    r = await client.post(
        "/palace/empty/search",
        headers=auth,
        json={"query": "anything at all", "k": 5},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []

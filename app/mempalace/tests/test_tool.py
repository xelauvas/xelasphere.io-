"""M0.17 Stage 1 RED — search_memory tool tests.

Plan sketch: `docs/superpowers/plans/2026-04-20-mempalace-integration.md`
lines 4139-4176 (Task M0.17.3).

At Stage 1 RED: tests requiring handle_search_memory fail at its body
(NotImplementedError). Schema-shape tests pass (TOOL_SCHEMA is defined
at module level so tests can import + assert its structure at RED).

Spec §3 line 121 (critical no-regression test): "The tool stays
registered in every mode, including off — in off mode it returns the
same empty-no-results payload. Never mutate the advertised tool list
based on feature-flag state." Tested here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mempalace.tool import TOOL_SCHEMA, handle_search_memory


# --- TOOL_SCHEMA structural tests (pass at Stage 1 RED) ---


def test_tool_schema_name():
    assert TOOL_SCHEMA["name"] == "search_memory"


def test_tool_schema_has_description():
    assert isinstance(TOOL_SCHEMA.get("description"), str)
    assert len(TOOL_SCHEMA["description"]) > 0


def test_tool_schema_required_query_only():
    """`query` is the only required arg; wing and k have defaults/are
    optional per spec §3 line 121.
    """
    schema = TOOL_SCHEMA["input_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"query"}
    props = schema["properties"]
    assert "query" in props
    assert "wing" in props
    assert "k" in props


def test_tool_schema_k_default_is_5():
    """Spec §3 line 121 + §4.3: k defaults to 5 in the tool schema."""
    assert TOOL_SCHEMA["input_schema"]["properties"]["k"].get("default") == 5


def test_tool_schema_k_has_minimum_and_maximum():
    """Spec M5 fold: k is bounded to prevent hostile/confused tool-calls
    (k=-1, k=10000) from reaching the sidecar. Bounds mirror site-wide
    numeric-input convention.
    """
    k_schema = TOOL_SCHEMA["input_schema"]["properties"]["k"]
    assert k_schema.get("minimum") == 1
    assert k_schema.get("maximum") == 50


# --- handle_search_memory behavior tests (fail at RED) ---


class FakeAdapter:
    """Test double: records search() args, returns a programmable result."""

    def __init__(self, result: Any = None, raise_exc: Exception | None = None):
        self._result = result if result is not None else []
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    async def search(self, *, user_id, query, wing=None, k=5):
        self.calls.append(
            {"user_id": user_id, "query": query, "wing": wing, "k": k}
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


@pytest.mark.asyncio
async def test_happy_path_returns_results():
    drawers = [
        {"text": "one", "wing": None, "room": None, "ts": 1.0,
         "pair_id": "p1", "score": None},
    ]
    adapter = FakeAdapter(result=drawers)
    out = await handle_search_memory(
        adapter, user_id="u-1", args={"query": "hello"}
    )
    assert out == {"results": drawers}
    # args threaded through correctly:
    assert adapter.calls == [
        {"user_id": "u-1", "query": "hello", "wing": None, "k": 5}
    ]


@pytest.mark.asyncio
async def test_failure_returns_empty_no_results_note():
    """Spec §3 line 121: on failure, returns the empty-no-results
    payload. Never 'unavailable' — indistinguishable from a genuine
    empty search so the model doesn't apologize to the user.
    """
    adapter = FakeAdapter(raise_exc=RuntimeError("sidecar circuit open"))
    out = await handle_search_memory(
        adapter, user_id="u-1", args={"query": "hello"}
    )
    assert out == {"results": [], "note": "no results"}
    assert "unavailable" not in str(out).lower()


@pytest.mark.asyncio
async def test_wing_passed_through():
    adapter = FakeAdapter(result=[])
    await handle_search_memory(
        adapter,
        user_id="u-1",
        args={"query": "hello", "wing": "conversations", "k": 3},
    )
    assert adapter.calls[0]["wing"] == "conversations"
    assert adapter.calls[0]["k"] == 3


@pytest.mark.asyncio
async def test_k_coerces_from_string():
    """LLM may emit `k` as a JSON number or string. Plan says
    `int(args.get("k", 5))` — handles both.
    """
    adapter = FakeAdapter(result=[])
    await handle_search_memory(
        adapter, user_id="u-1", args={"query": "hello", "k": "7"}
    )
    assert adapter.calls[0]["k"] == 7


@pytest.mark.asyncio
async def test_off_mode_still_returns_empty_payload():
    """Spec §3 line 121: tool stays registered in off mode; returns
    same empty payload as a genuine empty search or failure.

    Adapter in OFF mode returns [] from search(); this test uses an
    adapter that simulates that behavior.
    """
    adapter = FakeAdapter(result=[])  # simulates OFF-mode empty
    out = await handle_search_memory(
        adapter, user_id="u-1", args={"query": "anything"}
    )
    # Shape must match the failure-path empty-no-results payload
    # when adapter.search returns []. Stage 3 GREEN decides: does
    # `[]` return {"results": []} or {"results": [], "note": "no results"}?
    # Plan locks: happy-path empty is {"results": []} (no note).
    # Failure-path is {"results": [], "note": "no results"} (note present).
    # So OFF-mode-via-search-returning-[] falls under happy-path:
    assert out == {"results": []}


@pytest.mark.asyncio
async def test_missing_query_arg_returns_no_results_note():
    """Quality M1 fold: hostile/malformed tool-call with no `query` key
    triggers KeyError on args["query"]. The generic except-path returns
    the standard no-results-note payload — spec §3 line 121 contract
    (indistinguishable from genuine empty search).
    """
    adapter = FakeAdapter(result=[])
    out = await handle_search_memory(adapter, user_id="u-1", args={})
    assert out == {"results": [], "note": "no results"}


@pytest.mark.asyncio
async def test_non_integer_k_returns_no_results_note():
    """Quality M1 fold: tool-call with `k` coerce-to-int failure
    (e.g., k="abc") triggers ValueError at int(args["k"]). The generic
    except-path returns the standard no-results-note payload.
    """
    adapter = FakeAdapter(result=[])
    out = await handle_search_memory(
        adapter, user_id="u-1", args={"query": "hi", "k": "abc"}
    )
    assert out == {"results": [], "note": "no results"}


@pytest.mark.parametrize("mempalace_mode", ["off", "shadow", "active"])
@pytest.mark.asyncio
async def test_handle_search_memory_returns_valid_payload_in_every_mode(
    monkeypatch, mempalace_mode
):
    """Spec §3 line 121 contract (D5 / H7 fold): the search_memory tool
    stays registered in every mode, including `off`. In off mode the
    adapter's search() returns [] (no sidecar call); the handler still
    produces a {"results": []} shape — indistinguishable from a genuine
    empty search.

    This test lock the contract mechanically: no matter the mode, the
    handler never raises, and the returned payload has the `results`
    key. Catches regressions where someone conditionalizes the tool
    handler on mode (forbidden per spec — mid-conversation tool-list
    changes break some LLM providers).
    """
    monkeypatch.setenv("MEMPALACE_MODE", mempalace_mode)
    adapter = FakeAdapter(result=[])  # simulates either OFF-empty or genuine-empty
    out = await handle_search_memory(
        adapter, user_id="u-1", args={"query": "anything"}
    )
    assert "results" in out
    assert isinstance(out["results"], list)


@pytest.mark.asyncio
async def test_model_dump_supported_on_drawer_objects():
    """Plan line 4169: `d.model_dump() if hasattr(d, "model_dump") else d`.
    Pydantic BaseModel instances have model_dump; plain dicts don't.
    Handler returns plain-dict view in both cases.
    """
    class PydLikeDrawer:
        def model_dump(self):
            return {"text": "pyd", "ts": 1.0}

    adapter = FakeAdapter(result=[PydLikeDrawer(), {"text": "plain", "ts": 2.0}])
    out = await handle_search_memory(
        adapter, user_id="u-1", args={"query": "x"}
    )
    assert out["results"] == [
        {"text": "pyd", "ts": 1.0},
        {"text": "plain", "ts": 2.0},
    ]

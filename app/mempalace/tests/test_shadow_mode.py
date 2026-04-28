"""P1-entry shadow-mode behavior tests — Path B (structural + unit).

These tests land the subset of the P1-entry shadow-mode coverage that
can be verified without the deferred M0.23.5 Phase 3 mocking scaffold
(seven-target mock of Groq/OpenRouter/memory_v2/memory_v3/chat_engine/
smart_router/ActionParser; tracked at issue #10).

**Scope of this file (Path B):**
  - Test 1: unit-level value assertion on ``adapter.log_shadow`` payload
    schema. Real execution — payload enqueued through a real
    ``ShadowSink`` writing JSONL to tmp_path, read back via the same
    ``json.loads(line).get(...)`` path that ``tools/mempalace-rubric``
    uses. Pins the 5-key schema documented at spec §6 line 14 and at
    chat_routes.py call sites.
  - Tests 2-4: AST-structural checks on the mempalace wiring inside
    ``app/blueprints/chat_routes.py``. Catches the variable-ordering
    class of bug tracked at issue #9 and pins the name-level contract
    between chat_routes and the adapter call sites.

**What this file does NOT cover:**
  - Value-level verification of pair_id propagation across both halves
    of a real turn (requires executing ``POST /chat`` under mode=shadow
    with a patched adapter).
  - log_shadow being called at all — structural check confirms the
    call-site exists, but does NOT confirm it fires under mode=shadow
    during a live request.
  - wake_up_sync return value flowing into the log_shadow payload's
    ``mempalace`` key (round-trip property).

All three are value-level assertions that need real runtime execution
of the chat endpoints, and are gated on issue #10 (full integration
test scaffold) landing. **Shadow rotation (P0 → P1) waits on #10, not
on the Path B tests passing.** The phase-log P1 entry section spells
this out.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from app.mempalace.adapter import MempalaceAdapter
from app.mempalace.shadow_sink import ShadowSink


# ============================================================
# Test 1 — log_shadow payload schema (real execution, value-level)
# ============================================================


def test_log_shadow_payload_round_trips_through_rubric_compatible_parse(
    tmp_path, monkeypatch
):
    """The chat_routes log_shadow payload survives JSONL write + re-read
    with all documented keys accessible via the same ``json.loads + .get()``
    path that ``tools/mempalace-rubric`` uses.

    Pins the schema documented at three places that must stay in sync:
      - spec §6 line 14 (rubric input)
      - tools/mempalace-rubric docstring lines 12-14 (parser expectation)
      - app/blueprints/chat_routes.py:2701-2707 (non-streaming build site)
      - app/blueprints/chat_routes.py:948-954 (streaming build site)

    Failure mode surfaced: schema key dropped or renamed at any of these
    sites without the others being updated.

    Boundary: does NOT verify that chat_routes actually *reaches* the
    log_shadow call under mode=shadow during a live request. That
    requires real ``POST /chat`` execution and is tracked at issue #10.
    """
    monkeypatch.setenv("MEMPALACE_MODE", "shadow")

    sink = ShadowSink(log_dir=tmp_path)
    adapter = MempalaceAdapter(
        client=None,
        sync_client=None,
        outbox=None,
        shadow_sink=sink,
        redaction_rules=[],
    )

    # Payload shape identical to chat_routes.py:2701-2707 (non-streaming)
    # and :948-954 (streaming). If either site drifts, update here in
    # lockstep — this test is the cross-site pin.
    payload = {
        "ts": time.time(),
        "user_id": "u-1",
        "query": "what did we discuss about mempalace yesterday?",
        "legacy": "LTM snapshot: mempalace is the new memory system.",
        "mempalace": {
            "user_id": "u-1",
            "l0_recent": [{"text": "mempalace is the new memory system"}],
            "meta": {"hot": True},
        },
    }
    adapter.log_shadow(payload)

    # ShadowSink's daemon writer drains the queue asynchronously. stop()
    # joins the thread so all queued records are flushed to disk before
    # we read.
    sink.stop(timeout=2.0)

    today = time.strftime("%Y-%m-%d")
    shadow_file = tmp_path / f"shadow-{today}.jsonl"
    assert shadow_file.exists(), "ShadowSink did not write a JSONL file"

    lines = shadow_file.read_text().splitlines()
    assert len(lines) == 1, f"expected 1 record, got {len(lines)}"

    # Parse the way tools/mempalace-rubric does (rubric script lines 58-61
    # + 79-81): json.loads + .get() for each key.
    record = json.loads(lines[0])
    assert record.get("ts") == pytest.approx(payload["ts"])
    assert record.get("user_id") == "u-1"
    assert record.get("query") == payload["query"]
    assert record.get("legacy") == payload["legacy"]
    assert record.get("mempalace") == payload["mempalace"]


# ============================================================
# Tests 2-4 — AST structural checks on chat_routes.py mempalace wiring
# ============================================================

_CHAT_ROUTES_PATH = (
    Path(__file__).resolve().parents[3] / "app" / "blueprints" / "chat_routes.py"
)


@pytest.fixture(scope="module")
def chat_routes_ast():
    """Parse app/blueprints/chat_routes.py once per test module.

    Returns the top-level ast.Module. Individual tests locate their
    target functions via ``_find_function(tree, name)``.
    """
    source = _CHAT_ROUTES_PATH.read_text()
    return ast.parse(source, filename=str(_CHAT_ROUTES_PATH))


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in chat_routes.py")


def _assignment_linenos(func: ast.FunctionDef, target_name: str) -> list[int]:
    """Return line numbers where ``target_name`` is assigned inside ``func``."""
    linenos: list[int] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == target_name:
                    linenos.append(node.lineno)
    return linenos


def _record_turn_calls(func: ast.FunctionDef) -> list[ast.Call]:
    """Return Call nodes in ``func`` that look like ``...record_turn(...)``.

    Matches adapter.record_turn and adapter-getter().record_turn shapes.
    Excludes memory_v3's rollout_mgr.record_turn (different receiver).
    """
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "record_turn":
            continue
        # Exclude rollout_mgr.record_turn (memory_v3).
        recv = node.func.value
        if isinstance(recv, ast.Name) and recv.id == "rollout_mgr":
            continue
        calls.append(node)
    return calls


def _kwarg_value(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# ------------------------------------------------------------
# Test 2 — record_turn pair_id contract (non-streaming chat)
# ------------------------------------------------------------


def test_record_turn_pair_id_defined_before_use_in_chat(chat_routes_ast):
    """AST: _mp_pair_id and _mp_user_turn_id are assigned before their
    first use inside the non-streaming ``chat()`` function.

    Catches the use-before-assignment class of bug tracked at issue #9
    (silent NameError under mode != OFF in non-streaming path). The
    bug itself was fixed at commit f6c6906; this test prevents
    regression.

    Boundary: does NOT verify that record_turn actually fires twice per
    turn at runtime, or that both halves propagate the same pair_id
    value. Those are runtime assertions requiring ``POST /chat``
    execution under mode=shadow, tracked at issue #10.
    """
    chat_fn = _find_function(chat_routes_ast, "chat")

    for name in ("_mp_pair_id", "_mp_user_turn_id", "_mp_assistant_turn_id"):
        assign_linenos = _assignment_linenos(chat_fn, name)
        assert assign_linenos, (
            f"{name} is referenced in chat() but never assigned — "
            f"NameError class bug (#9 regression)"
        )
        first_assign = min(assign_linenos)

        # Find first reference (load context) of this name inside the function.
        first_use = None
        for node in ast.walk(chat_fn):
            if (
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, ast.Load)
            ):
                if first_use is None or node.lineno < first_use:
                    first_use = node.lineno

        assert first_use is not None, (
            f"{name} is assigned but never used in chat() — dead variable"
        )
        assert first_assign < first_use, (
            f"{name} first used at line {first_use} but first assigned at "
            f"line {first_assign} — use-before-assignment (#9 class)"
        )


def test_record_turn_both_halves_share_pair_id_variable_name(chat_routes_ast):
    """AST: the two record_turn calls in chat() (role=user + role=assistant)
    reference the SAME variable name for the ``pair_id`` kwarg.

    Boundary: pins the name-level contract — both halves pass the same
    identifier. Does NOT verify runtime that the value is actually
    shared across calls (a refactor that re-assigned _mp_pair_id between
    the calls would pass this test but break the contract). Tracked at
    issue #10 for value-level coverage.

    Asymmetric note: streaming path (``chat_stream``) intentionally has
    only the user-half record_turn — assistant-half is deferred per
    M0.18 finish commit af7549a. That asymmetry is verified in Test 4.
    """
    chat_fn = _find_function(chat_routes_ast, "chat")
    calls = _record_turn_calls(chat_fn)
    assert len(calls) == 2, (
        f"expected exactly 2 adapter.record_turn calls in chat() "
        f"(user-half + assistant-half), found {len(calls)} at lines "
        f"{[c.lineno for c in calls]}"
    )

    roles: dict[str, ast.Call] = {}
    for call in calls:
        role_node = _kwarg_value(call, "role")
        assert isinstance(role_node, ast.Constant), (
            f"record_turn at line {call.lineno} has non-literal role= kwarg; "
            f"test cannot classify user vs assistant half"
        )
        roles[role_node.value] = call

    assert set(roles.keys()) == {"user", "assistant"}, (
        f"expected one user-half + one assistant-half record_turn call, "
        f"got roles={set(roles.keys())}"
    )

    user_pair_id = _kwarg_value(roles["user"], "pair_id")
    assistant_pair_id = _kwarg_value(roles["assistant"], "pair_id")

    assert isinstance(user_pair_id, ast.Name), (
        f"user-half record_turn pair_id= is not a Name node; "
        f"test cannot verify shared-variable contract"
    )
    assert isinstance(assistant_pair_id, ast.Name), (
        f"assistant-half record_turn pair_id= is not a Name node"
    )
    assert user_pair_id.id == assistant_pair_id.id, (
        f"user-half pair_id uses {user_pair_id.id!r} but assistant-half "
        f"uses {assistant_pair_id.id!r} — both halves must reference the "
        f"same variable per spec §3 line 122"
    )


# ------------------------------------------------------------
# Test 3 — wake_up_sync call shape at chat_routes
# ------------------------------------------------------------


def _wake_up_sync_calls(func: ast.FunctionDef) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "wake_up_sync"
        ):
            calls.append(node)
    return calls


@pytest.mark.parametrize("func_name", ["chat", "chat_stream"])
def test_wake_up_sync_called_with_expected_args(chat_routes_ast, func_name):
    """AST: both chat() and chat_stream() call adapter.wake_up_sync with
    two positional args (user_id, display_prompt) and the session_id
    kwarg.

    Boundary: does NOT verify runtime that the values passed are the
    correct session's user_id / prompt / session, or that the return
    value flows into the log_shadow payload's ``mempalace`` key. Both
    are value-level assertions tracked at issue #10.
    """
    func = _find_function(chat_routes_ast, func_name)
    calls = _wake_up_sync_calls(func)
    assert calls, f"wake_up_sync not called in {func_name}"

    call = calls[0]
    assert len(call.args) == 2, (
        f"{func_name}: wake_up_sync expects 2 positional args "
        f"(user_id, prompt); got {len(call.args)} at line {call.lineno}"
    )
    for arg in call.args:
        assert isinstance(arg, ast.Name), (
            f"{func_name}: wake_up_sync positional arg at line "
            f"{call.lineno} is not a Name node"
        )
    # User ID position first, prompt-like second (pinned by variable names
    # today; loosen to "two names" to tolerate future renames that
    # preserve the shape).
    assert call.args[0].id == "user_id", (
        f"{func_name}: first wake_up_sync arg is {call.args[0].id!r}, "
        f"expected 'user_id'"
    )

    session_id_kwarg = _kwarg_value(call, "session_id")
    assert session_id_kwarg is not None, (
        f"{func_name}: wake_up_sync at line {call.lineno} missing "
        f"session_id= kwarg — sidecar structured-logging correlation breaks"
    )


# ------------------------------------------------------------
# Test 4 — streaming has both user-half and assistant-half record_turn
# ------------------------------------------------------------


def test_chat_stream_has_both_user_and_assistant_record_turn_halves(
    chat_routes_ast,
):
    """AST: chat_stream() has exactly 1 user-half record_turn and at least
    1 assistant-half record_turn, with ALL halves referencing the same
    variable name for the ``pair_id`` kwarg.

    Mirrors test_record_turn_both_halves_share_pair_id_variable_name's
    pair_id-sharing contract for the non-streaming path. Diverges in
    that the streaming path has multiple SSE exit branches that each
    need their own assistant-half record_turn — they're mutually
    exclusive at runtime, but all must propagate the same pair_id
    variable as the (single) user-half. Documented exit branches:

      - main path (after Memory V2 post_turn at the SSE done-event)
      - forced-search mode (early return after search synthesis)
      - cache-hit (early return after serving the cached response)

    The streaming assistant-half was deferred at M0.18-finish commit
    af7549a; P1 dogfood (2026-04-28) surfaced the impact —
    turns.sqlite contained 0 assistant rows for every user, so
    semantic_search retrieved user statements and user recall-questions
    only, never assistant-side answers. This commit lands the
    streaming-completion hook at every documented exit branch.

    Boundary: does NOT verify runtime that all branches fire correctly
    in their own paths, or that the pair_id value is shared across
    calls at runtime. Value-level coverage at issue #10.
    """
    chat_stream_fn = _find_function(chat_routes_ast, "chat_stream")
    calls = _record_turn_calls(chat_stream_fn)

    user_halves = []
    assistant_halves = []
    for call in calls:
        role_node = _kwarg_value(call, "role")
        assert isinstance(role_node, ast.Constant), (
            f"record_turn at line {call.lineno} has non-literal role= kwarg; "
            f"test cannot classify user vs assistant half"
        )
        if role_node.value == "user":
            user_halves.append(call)
        elif role_node.value == "assistant":
            assistant_halves.append(call)

    assert len(user_halves) == 1, (
        f"chat_stream: expected exactly 1 user-half record_turn (one input "
        f"per turn), found {len(user_halves)} at lines "
        f"{[c.lineno for c in user_halves]}"
    )
    assert len(assistant_halves) >= 1, (
        f"chat_stream: expected at least 1 assistant-half record_turn "
        f"(one per SSE exit branch), found 0. Streaming assistant-half "
        f"is the M0.18-finish (af7549a) deferred wrapper — see PR #102 "
        f"for the lift."
    )

    pair_id_names = set()
    for call in (*user_halves, *assistant_halves):
        pair_id_node = _kwarg_value(call, "pair_id")
        assert isinstance(pair_id_node, ast.Name), (
            f"record_turn at line {call.lineno} pair_id= is not a Name "
            f"node — test cannot verify shared-variable contract"
        )
        pair_id_names.add(pair_id_node.id)

    assert len(pair_id_names) == 1, (
        f"chat_stream: all record_turn halves must reference the same "
        f"pair_id variable per spec §3 line 122; found "
        f"{sorted(pair_id_names)} across "
        f"{len(user_halves) + len(assistant_halves)} call(s) at lines "
        f"{[c.lineno for c in (*user_halves, *assistant_halves)]}"
    )

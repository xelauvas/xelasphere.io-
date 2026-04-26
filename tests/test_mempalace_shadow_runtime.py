"""Runtime shadow-mode behavior tests (issue #10, Test 2).

Closes the runtime-coverage gap that Path B (AST/structural) tests in
``app/mempalace/tests/test_shadow_mode.py`` cannot cover. The Path B
tests pin the variable *name* used for ``pair_id`` across the two
``record_turn`` halves; this file pins the runtime *value* — that the
two halves actually receive the same string, not just the same
identifier name.

The bug class this catches: a refactor that renames the variable
correctly but accidentally re-binds it between the user-half and
assistant-half call sites. Path B's name-equality check would still
pass; this runtime test would fail.

Scope (this commit lands Test 2 only; Tests 3 + 4 remain open on #10):

  - **Test 2 (this file):** ``record_turn`` fires exactly twice per
    turn with role='user' + role='assistant' sharing one pair_id;
    ``log_shadow`` fires exactly once with the documented 5-key
    payload {ts, user_id, query, legacy, mempalace}.

  - **Test 3 (deferred):** ``wake_up_sync`` arg-shape assertions
    (positional user_id + display_prompt, keyword session_id, return
    value round-tripping into log_shadow's ``mempalace`` key).

  - **Test 4 (deferred):** ``POST /chat/stream`` at mode=shadow —
    asserts user-half record_turn fires with the streaming path's
    own pair_id, and skips the assistant-half assertion per M0.18
    finish commit ``af7549a``.

Tests 3 + 4 share the same ``mempalace_shadow_env`` fixture from
``tests/conftest.py`` and can layer on without re-doing scaffold work.
"""

from __future__ import annotations


def test_record_turn_both_halves_share_pair_id_at_runtime(
    authenticated_client, mempalace_shadow_env
):
    """POST /chat at MEMPALACE_MODE=shadow fires record_turn exactly twice
    with role='user' + role='assistant' sharing one pair_id value.

    The runtime equality assertion is what Path B's AST tests cannot
    deliver. A future refactor that re-binds ``_mp_pair_id`` between the
    two call sites would pass the AST checks (same name) and fail this
    test (different runtime values).
    """
    response = authenticated_client.post(
        "/chat",
        data={
            "prompt": "hello mempalace, what's in memory?",
            "display_prompt": "hello mempalace, what's in memory?",
        },
        headers={"X-CSRFToken": "test-bypass"},
    )
    assert response.status_code < 500, (
        f"chat() returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:300]}"
    )

    record_calls = mempalace_shadow_env.adapter.record_turn.call_args_list
    assert len(record_calls) == 2, (
        f"expected exactly 2 record_turn calls (user-half + assistant-half), "
        f"got {len(record_calls)}: "
        f"{[c.kwargs.get('role') for c in record_calls]}"
    )

    by_role = {}
    for call in record_calls:
        role = call.kwargs.get("role")
        assert role in ("user", "assistant"), (
            f"record_turn called with unexpected role={role!r}; expected "
            f"'user' or 'assistant'"
        )
        assert role not in by_role, (
            f"role={role!r} captured twice — both halves should fire once"
        )
        by_role[role] = call

    assert set(by_role.keys()) == {"user", "assistant"}, (
        f"expected one user-half + one assistant-half record_turn, "
        f"got roles={set(by_role.keys())}"
    )

    user_pair_id = by_role["user"].kwargs.get("pair_id")
    assistant_pair_id = by_role["assistant"].kwargs.get("pair_id")

    assert user_pair_id is not None, "user-half record_turn missing pair_id"
    assert assistant_pair_id is not None, "assistant-half record_turn missing pair_id"

    assert user_pair_id == assistant_pair_id, (
        f"pair_id runtime values diverge between halves: "
        f"user={user_pair_id!r}, assistant={assistant_pair_id!r}. "
        f"Spec §3 line 122: both halves of a turn must share one pair_id "
        f"so downstream drawers can be grouped as a turn pair."
    )

    user_turn_id = by_role["user"].kwargs.get("turn_id")
    assistant_turn_id = by_role["assistant"].kwargs.get("turn_id")
    assert user_turn_id and assistant_turn_id and user_turn_id != assistant_turn_id, (
        f"turn_ids must be distinct per half (user={user_turn_id!r}, "
        f"assistant={assistant_turn_id!r}); shared turn_id would collapse "
        f"the two halves into one record"
    )


def test_log_shadow_fires_once_with_five_key_payload(
    authenticated_client, mempalace_shadow_env
):
    """POST /chat at MEMPALACE_MODE=shadow fires log_shadow exactly once
    with the documented payload schema {ts, user_id, query, legacy,
    mempalace}.

    Pins the runtime contract for ``tools/mempalace-rubric`` and the
    shadow-rotation read path. A schema drift here would silently
    invalidate every shadow-window rubric report until someone notices
    the parser warnings.
    """
    response = authenticated_client.post(
        "/chat",
        data={
            "prompt": "what did we discuss about mempalace yesterday?",
            "display_prompt": "what did we discuss about mempalace yesterday?",
        },
        headers={"X-CSRFToken": "test-bypass"},
    )
    assert response.status_code < 500, (
        f"chat() returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:300]}"
    )

    log_calls = mempalace_shadow_env.adapter.log_shadow.call_args_list
    assert len(log_calls) == 1, (
        f"expected exactly 1 log_shadow call per chat turn under shadow mode, "
        f"got {len(log_calls)}"
    )

    payload = log_calls[0].args[0] if log_calls[0].args else log_calls[0].kwargs
    assert isinstance(payload, dict), (
        f"log_shadow payload must be a dict; got {type(payload).__name__}"
    )

    expected_keys = {"ts", "user_id", "query", "legacy", "mempalace"}
    assert set(payload.keys()) == expected_keys, (
        f"log_shadow payload keys diverged from spec §6 line 14: "
        f"got {set(payload.keys())}, expected {expected_keys}. "
        f"Schema drift here breaks tools/mempalace-rubric parsing."
    )

    assert isinstance(payload["ts"], (int, float)), (
        f"log_shadow ts must be numeric (time.time()); got {type(payload['ts']).__name__}"
    )
    assert payload["query"] == "what did we discuss about mempalace yesterday?"
    assert payload["mempalace"] == mempalace_shadow_env.wake_up_return, (
        f"log_shadow's 'mempalace' key must round-trip the wake_up_sync "
        f"return value; got {payload['mempalace']!r}, expected "
        f"{mempalace_shadow_env.wake_up_return!r}"
    )

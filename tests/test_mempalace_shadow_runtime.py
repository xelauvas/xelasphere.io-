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


def test_wake_up_sync_called_with_positional_user_id_and_session_id_kwarg(
    authenticated_client, mempalace_shadow_env
):
    """Test 3 (issue #10) — runtime arg-shape assertion on wake_up_sync.

    Pins the call contract at chat_routes.py:2710 (non-streaming):
      - exactly two positional args: user_id, display_prompt
      - session_id passed as a keyword arg

    The Path B AST test pinned this at the name level; this test pins
    runtime values. A future refactor that flips the positional order
    or drops the session_id kwarg would pass AST and fail here.
    """
    user_prompt = "ping mempalace for the wake_up_sync arg test"
    response = authenticated_client.post(
        "/chat",
        data={"prompt": user_prompt, "display_prompt": user_prompt},
        headers={"X-CSRFToken": "test-bypass"},
    )
    assert response.status_code < 500, (
        f"chat() returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:300]}"
    )

    wake_calls = mempalace_shadow_env.adapter.wake_up_sync.call_args_list
    assert len(wake_calls) == 1, (
        f"expected exactly 1 wake_up_sync call per /chat turn under "
        f"shadow mode, got {len(wake_calls)}"
    )

    call = wake_calls[0]
    assert len(call.args) == 2, (
        f"wake_up_sync expects 2 positional args (user_id, display_prompt); "
        f"got {len(call.args)}: args={call.args!r}, kwargs={call.kwargs!r}"
    )

    positional_user_id, positional_prompt = call.args
    assert isinstance(positional_user_id, str) and positional_user_id, (
        f"first positional arg (user_id) must be a non-empty string; "
        f"got {positional_user_id!r}"
    )
    assert positional_prompt == user_prompt, (
        f"second positional arg (display_prompt) must be the user-supplied "
        f"display_prompt; got {positional_prompt!r}, expected {user_prompt!r}"
    )

    assert "session_id" in call.kwargs, (
        f"wake_up_sync at chat_routes.py:2710 must pass session_id as a "
        f"kwarg — sidecar structured-logging correlation depends on it. "
        f"Got kwargs={call.kwargs!r}"
    )
    assert isinstance(call.kwargs["session_id"], str) and call.kwargs["session_id"], (
        f"session_id kwarg must be a non-empty string; got "
        f"{call.kwargs['session_id']!r}"
    )

    log_calls = mempalace_shadow_env.adapter.log_shadow.call_args_list
    assert len(log_calls) == 1
    payload = log_calls[0].args[0] if log_calls[0].args else log_calls[0].kwargs
    assert payload["mempalace"] is mempalace_shadow_env.wake_up_return, (
        f"log_shadow's 'mempalace' key must be the IDENTITY-preserved "
        f"return value of wake_up_sync — not a copy or re-built dict. "
        f"This pins the round-trip property: chat_routes.py:2717 must "
        f"pass the bare _mp_wake reference, not e.g. dict(_mp_wake)."
    )


def test_chat_stream_fires_user_half_record_turn_only(
    authenticated_client, mempalace_shadow_env
):
    """Test 4 (issue #10) — runtime version of chat_stream's intentional
    half-pair asymmetry.

    Pins three properties for ``POST /chat/stream`` at MEMPALACE_MODE=shadow:

      1. Exactly ONE adapter.record_turn call fires, with role='user'.
      2. ZERO adapter.record_turn calls with role='assistant' fire — the
         streaming assistant-half is intentionally deferred per M0.18
         finish commit ``af7549a`` (multi-exit-branch SSE flow lacks a
         single assistant-finalization point).
      3. The streaming pair_id is NOT shared with any concurrent
         non-streaming pair_id from this test session — chat_stream
         allocates its own ``_mp_stream_pair_id`` at chat_routes.py:967.

    When the streaming-completion wrapper lands (issue tracker for that
    deferral lives in the M0.18 commit body), update this test in the
    same commit to assert the new expected counts.
    """
    response = authenticated_client.post(
        "/chat/stream",
        data={
            "prompt": "stream test prompt",
            "display_prompt": "stream test prompt",
        },
        headers={"X-CSRFToken": "test-bypass"},
    )

    # Drain the SSE response body so any generator-side errors surface.
    # The mempalace calls fire in the outer chat_stream body BEFORE the
    # stream generator is invoked, so they're already recorded by the
    # time we reach this line — but draining still validates the
    # streaming path doesn't crash with our scaffold mocks.
    body = response.get_data(as_text=True)
    assert response.status_code < 500, (
        f"chat_stream returned {response.status_code}; body[:500]: {body[:500]!r}"
    )

    record_calls = mempalace_shadow_env.adapter.record_turn.call_args_list
    user_halves = [c for c in record_calls if c.kwargs.get("role") == "user"]
    assistant_halves = [c for c in record_calls if c.kwargs.get("role") == "assistant"]

    assert len(user_halves) == 1, (
        f"expected exactly 1 user-half record_turn from /chat/stream, got "
        f"{len(user_halves)}. All record_turn calls: "
        f"{[(c.kwargs.get('role'), c.kwargs.get('pair_id')) for c in record_calls]}"
    )

    assert len(assistant_halves) == 0, (
        f"chat_stream emitted {len(assistant_halves)} assistant-half "
        f"record_turn call(s) — streaming assistant-half is deferred per "
        f"M0.18 finish commit af7549a. If the streaming-completion wrapper "
        f"has landed, update this test in the same commit to assert the "
        f"new expected counts."
    )

    # Streaming pair_id allocated fresh at chat_routes.py:967 — verify it
    # exists and is a non-empty string. We don't compare against a
    # non-streaming pair_id here because that's a property of the spec
    # (different requests get different uuids); cross-test leakage would
    # be a fixture bug, not a contract violation.
    user_pair_id = user_halves[0].kwargs.get("pair_id")
    assert user_pair_id, (
        f"streaming user-half record_turn missing pair_id; got {user_pair_id!r}"
    )
    assert isinstance(user_pair_id, str), (
        f"streaming pair_id must be a string (uuid4 hex); got "
        f"{type(user_pair_id).__name__}"
    )

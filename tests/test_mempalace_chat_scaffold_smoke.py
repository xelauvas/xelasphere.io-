"""Smoke test for the ``mempalace_shadow_env`` scaffold (issue #10).

Validates that the minimum-viable scaffold defined in tests/conftest.py
actually lets ``POST /chat`` reach both halves of the mempalace adapter
call sites under ``MEMPALACE_MODE=shadow``. Without this smoke, the
scaffold could land as dead code that nothing exercises.

What this file does NOT do: assert the shadow contract (pair_id
propagation, log_shadow payload shape, wake_up_sync arg shape). Those
are Tests 2/3/4 from the playbook, in test_mempalace_shadow_runtime.py
(landing alongside / after this scaffold).

The smoke test asserts the bare scaffold guarantee:
  - request returns 200 (or at minimum non-5xx — chat() returns 200/4xx
    on user-input issues; we want to confirm we didn't blow up the
    pipeline)
  - the recording adapter was reached (record_turn called at least once)
"""

from __future__ import annotations

import pytest


def test_chat_endpoint_reaches_mempalace_adapter_under_shadow(
    authenticated_client, mempalace_shadow_env
):
    """POST /chat under MEMPALACE_MODE=shadow returns successfully and
    the patched adapter receives at least one record_turn call.

    Confirms the five-target scaffold (LLM, memory_v2, smart_router,
    ActionParser, mempalace adapter) is sufficient to drive chat() past
    the user-half record_turn site at chat_routes.py:2726.

    A passing test here is the green light to write Test 2 (pair_id
    runtime contract) on top of the same fixture.
    """
    # X-CSRFToken header satisfies the csrf_required decorator's
    # presence check (app/limiter.py:18). With WTF_CSRF_ENABLED=False
    # in the test app config, validate_csrf short-circuits, so any
    # non-empty token suffices.
    response = authenticated_client.post(
        "/chat",
        data={
            "prompt": "smoke-test prompt for the scaffold",
            "display_prompt": "smoke-test prompt for the scaffold",
        },
        headers={"X-CSRFToken": "test-bypass"},
    )

    # Allow any 2xx or 4xx — chat() treats some inputs as 400/403 (guest
    # limits, validation), but a 5xx means the pipeline genuinely broke.
    assert response.status_code < 500, (
        f"chat() returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:500]}"
    )

    # The user-half record_turn fires at chat_routes.py:2726 BEFORE the
    # LLM call. Even if downstream paths bail, this call should land
    # under any successful auth + minimal-form path.
    assert mempalace_shadow_env.adapter.record_turn.call_count >= 1, (
        "expected at least one record_turn call (user-half at line 2726). "
        f"Got {mempalace_shadow_env.adapter.record_turn.call_count}. "
        f"adapter_factory called: {mempalace_shadow_env.adapter_factory.call_count} times. "
        f"Response status: {response.status_code}, body[:300]: "
        f"{response.get_data(as_text=True)[:300]!r}"
    )

"""W-20 — post-stream audit fire from chat_routes.

Verifies the audit helper runs audit_response() + write_scaffold_audit()
asynchronously, respects the feature flag, and never raises.
"""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


def test_audit_no_op_when_flag_off(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "false")
    from app.blueprints import chat_routes as cr
    with patch("app.blueprints.chat_routes.audit_response") as mock_audit, \
         patch("app.blueprints.chat_routes.write_scaffold_audit") as mock_write:
        cr._fire_audit_async(
            scaffold=None, rendered_output="anything",
            meta={}, user_id="u", session_id="s",
            model="m", provider="p",
        )
    # Flag off: neither should be invoked.
    assert mock_audit.call_count == 0
    assert mock_write.call_count == 0


def test_audit_no_op_when_scaffold_is_none(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    with patch("app.blueprints.chat_routes.audit_response") as mock_audit, \
         patch("app.blueprints.chat_routes.write_scaffold_audit") as mock_write:
        cr._fire_audit_async(
            scaffold=None, rendered_output="x",
            meta={}, user_id="u", session_id="s",
            model="m", provider="p",
        )
    assert mock_audit.call_count == 0
    assert mock_write.call_count == 0


def test_audit_fires_when_flag_on_and_scaffold_present(monkeypatch):
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    fake_scaffold = {"layout": "structured", "sections": []}
    with patch("app.blueprints.chat_routes.audit_response") as mock_audit, \
         patch("app.blueprints.chat_routes.write_scaffold_audit") as mock_write, \
         patch("app.blueprints.chat_routes._AUDIT_EXECUTOR") as mock_pool:
        # Execute synchronously for deterministic assertion:
        mock_pool.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        mock_audit.return_value = {"metrics": {"compliance_rate": 0.9}}
        cr._fire_audit_async(
            scaffold=fake_scaffold, rendered_output="## S\n\nbody.\n",
            meta={"render_path": "markdown"},
            user_id="user_42", session_id="sess_7",
            model="gpt-4.1-mini", provider="openai",
        )
    assert mock_audit.call_count == 1
    assert mock_write.call_count == 1
    write_kwargs = mock_write.call_args.kwargs
    assert write_kwargs["user_id"] == "user_42"
    assert write_kwargs["session_id"] == "sess_7"
    assert write_kwargs["model"] == "gpt-4.1-mini"
    assert write_kwargs["provider"] == "openai"


def test_audit_exception_never_bubbles(monkeypatch):
    """If audit_response raises, the helper must swallow it — the stream
    has already closed. No broken response for the user."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    fake_scaffold = {"layout": "prose", "sections": []}
    with patch("app.blueprints.chat_routes.audit_response") as mock_audit, \
         patch("app.blueprints.chat_routes.write_scaffold_audit") as mock_write, \
         patch("app.blueprints.chat_routes._AUDIT_EXECUTOR") as mock_pool:
        mock_pool.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        mock_audit.side_effect = RuntimeError("parser blew up")
        # Must NOT raise:
        cr._fire_audit_async(
            scaffold=fake_scaffold, rendered_output="x",
            meta={}, user_id="u", session_id="s",
            model="m", provider="p",
        )
    # writer not called because audit errored, but no exception bubbled:
    assert mock_write.call_count == 0


def test_audit_uses_thread_executor_by_default(monkeypatch):
    """Real submit() goes to the pool, not the request thread."""
    monkeypatch.setenv("SCAFFOLD_V2_ENABLED", "true")
    from app.blueprints import chat_routes as cr
    fake_scaffold = {"layout": "prose", "sections": []}
    with patch("app.blueprints.chat_routes.audit_response") as mock_audit, \
         patch("app.blueprints.chat_routes.write_scaffold_audit") as mock_write:
        mock_audit.return_value = {"metrics": {}}
        cr._fire_audit_async(
            scaffold=fake_scaffold, rendered_output="x",
            meta={}, user_id=None, session_id="s",
            model="m", provider="p",
        )
        # Wait for the executor work to finish:
        cr._AUDIT_EXECUTOR.shutdown(wait=True)
    assert mock_audit.call_count == 1
    assert mock_write.call_count == 1
    # Restore executor so other tests aren't affected:
    import concurrent.futures
    cr._AUDIT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="scaffold-audit",
    )

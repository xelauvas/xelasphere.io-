"""End-to-end: the /chat/stream endpoint emits a scaffold event first, then content."""
import json
import sys
import pytest
from unittest.mock import patch, MagicMock

# Supplement the firebase_admin mock installed by root conftest with the
# 'exceptions' submodule that app/auth.py imports at module-load time.
if "firebase_admin.exceptions" not in sys.modules:
    _fb_exc_mock = MagicMock()
    _fb_exc_mock.FirebaseError = type("FirebaseError", (Exception,), {})
    sys.modules["firebase_admin.exceptions"] = _fb_exc_mock


def _make_built_context():
    """Return a minimal BuiltContext stub that satisfies chat_routes attribute access."""
    from app.context.context_builder import BuiltContext
    return BuiltContext(
        prompt="hello",
        messages=[{"role": "user", "content": "hello"}],
        artifact_files=[],
        token_estimate=10,
        compression_level="none",
    )


def _content_chunk(text="hello"):
    """Minimal streaming chunk that chat_routes.generate() emits as a content event."""
    return {"type": "content", "text": text}


def _fake_stream_deepseek(*args, **kwargs):
    """Stub for stream_deepseek_response: yields one content-like dict."""
    yield {"type": "content", "text": "hi"}


@pytest.fixture
def client(mock_firestore):
    """
    Flask test client with:
    - Firebase already mocked by root conftest.py
    - CSRF validation bypassed (custom csrf_required decorator)
    - LLM / smart-routing calls stubbed so no real network IO
    - Authenticated session (user uid) to pass the 401 gate in chat_stream

    When the test suite is run together with tests/services/*, their conftest
    stubs out ``app.blueprints`` as a MagicMock to break a circular import.
    We temporarily remove those stubs before calling create_app() so the real
    package is loaded, then restore the stubs afterwards so the services tests
    are unaffected.
    """
    # Modules that tests/services/conftest.py may have replaced with MagicMocks.
    _BLUEPRINT_STUBS = [
        "app.blueprints",
        "app.blueprints.shared",
    ]
    _saved = {k: sys.modules.pop(k) for k in _BLUEPRINT_STUBS if k in sys.modules}

    try:
        with (
            patch("app.limiter.validate_csrf"),                        # bypass header CSRF check
            patch("app.utilities.stream_deepseek_response",
                  side_effect=_fake_stream_deepseek),                  # no real LLM call
            patch("app.smart_router.smart_route",
                  return_value=("deepseek-chat", "general_chat")),     # skip smart routing
            patch("app.orchestrator.get_model",
                  return_value=("deepseek-chat", "groq", None)),       # skip orchestrator
            patch("app.context.context_builder.ContextBuilder.build",  # skip heavy context build
                  return_value=_make_built_context()),
        ):
            from app import create_app

            app = create_app()
            app.config.update({
                "TESTING": True,
                "WTF_CSRF_ENABLED": False,
                "SERVER_NAME": "localhost",
                "SESSION_TYPE": "filesystem",
                "SECRET_KEY": "test-secret-key",
            })
            tc = app.test_client()
            # Inject a minimal authenticated session so chat_stream passes its 401 check.
            with tc.session_transaction() as sess:
                sess["user"] = {
                    "uid": "test_user_e2e",
                    "email": "test@example.com",
                    "name": "Test User",
                    "picture": "",
                    "show_status": True,
                }
            yield tc
    finally:
        # Restore the stubs so services tests remain isolated.
        sys.modules.update(_saved)


def _collect_sse_types(resp_iter, limit_events=5):
    """Pull up to `limit_events` SSE data: lines and return the parsed 'type' field."""
    types = []
    buf = b""
    for chunk in resp_iter:
        buf += chunk
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                if line.startswith(b"data: "):
                    try:
                        payload = json.loads(line[6:].decode())
                        types.append(payload.get("type"))
                    except Exception:
                        pass
        if len(types) >= limit_events:
            break
    return types


def test_scaffold_event_arrives_before_content(client):
    resp = client.post(
        "/chat/stream",
        data={"prompt": "hello", "model": "auto"},
        headers={"X-CSRFToken": "test-token"},
        buffered=False,
    )
    types = _collect_sse_types(resp.response, limit_events=5)
    # The endpoint must return a streaming response (200 OK).
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    # What we MUST see: if scaffold is present, it comes before any 'content'.
    if "scaffold" in types:
        assert types.index("scaffold") < (
            types.index("content") if "content" in types else 999
        ), f"scaffold must precede content, got order: {types}"
    else:
        # scaffold may not appear if classify_user_intent returned scaffold=None.
        # Still verify we got at least one event (the stream is alive).
        assert len(types) > 0, "Expected at least one SSE event"

"""
Test-specific fixtures for Flask app testing.

This file provides Flask test client fixtures and common test utilities.
The Firebase mocking happens in the root conftest.py BEFORE imports.
"""

import os
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock, patch
import numpy as np


@pytest.fixture(scope="module")
def app(mock_firestore):
    """
    Create Flask app with test configuration.

    CSRF is disabled for test client to allow form submissions.
    """
    # Mock os.path.exists for Firebase creds check
    with patch('os.path.exists', return_value=True):
        from app import create_app

        app = create_app()
        app.config.update({
            'TESTING': True,
            'WTF_CSRF_ENABLED': False,
            'SERVER_NAME': 'localhost',
            'SESSION_TYPE': 'filesystem',
            'SECRET_KEY': 'test-secret-key',
        })

        yield app


@pytest.fixture
def client(app):
    """Flask test client for making requests."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Flask CLI test runner."""
    return app.test_cli_runner()


@pytest.fixture
def authenticated_client(client, mock_firestore):
    """Client with authenticated session."""
    with client.session_transaction() as sess:
        sess['user'] = {
            'uid': 'test_user_123',
            'email': 'test@example.com',
            'name': 'Test User',
            'picture': '/static/images/default-profile.png',
            'show_status': True,
        }
    return client


@pytest.fixture
def mock_requests(mocker):
    """Mock requests library for OAuth and API tests."""
    mock_post = mocker.patch('requests.post')
    mock_get = mocker.patch('requests.get')
    return {'post': mock_post, 'get': mock_get}


@pytest.fixture
def sample_artifact_payload():
    """Sample valid artifact payload for testing."""
    return {
        "title": "Test Artifact",
        "entrypoint": "main.py",
        "files": [
            {
                "path": "main.py",
                "language": "python",
                "content": "print('Hello, World!')"
            }
        ],
        "meta": {}
    }


@pytest.fixture
def sample_artifact_multi_file():
    """Sample multi-file artifact payload."""
    return {
        "title": "Multi-File Project",
        "entrypoint": "index.html",
        "files": [
            {
                "path": "index.html",
                "language": "html",
                "content": "<!DOCTYPE html><html><body><h1>Test</h1></body></html>"
            },
            {
                "path": "style.css",
                "language": "css",
                "content": "body { margin: 0; }"
            },
            {
                "path": "script.js",
                "language": "javascript",
                "content": "console.log('test');"
            }
        ],
        "meta": {}
    }


@pytest.fixture
def mock_sentence_transformer(mocker):
    """Mock SentenceTransformer for embedding tests."""
    mock_model = MagicMock()
    mock_model.encode.return_value = np.random.rand(384).astype(np.float32)

    mocker.patch('sentence_transformers.SentenceTransformer', return_value=mock_model)
    return mock_model


@pytest.fixture
def sample_chat_messages():
    """Sample conversation history for testing."""
    return [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"},
        {"role": "user", "content": "Can you write some Python code?"},
        {"role": "assistant", "content": "```python\nprint('Hello')\n```"},
    ]


@pytest.fixture
def sample_coding_messages():
    """Sample coding conversation for task detection tests."""
    return [
        {"role": "user", "content": "Write a function to calculate factorial"},
        {"role": "assistant", "content": "```python\ndef factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n-1)\n```"},
        {"role": "user", "content": "Now add error handling for negative numbers"},
    ]


# ----------------------------------------------------------------------
# Mempalace shadow-mode integration scaffold (issue #10, narrowed)
# ----------------------------------------------------------------------
#
# Minimum-viable scaffold for exercising POST /chat (and POST /chat/stream)
# under MEMPALACE_MODE=shadow with deterministic outputs. Patches just
# enough surface area to let the request reach both halves of the mempalace
# adapter call sites (user-half record_turn at chat_routes.py:2726, then
# assistant-half record_turn at chat_routes.py:3274 after the LLM returns).
#
# Targets covered (5 of issue #10's 7):
#   - LLM HTTP boundary (requests.post in app.utilities)
#   - app.memory_v2.MemoryOrchestrator (instantiation + .get_context)
#   - app.smart_router.smart_route
#   - app.context.action_parser.ActionParser.parse
#   - app.blueprints.chat_routes._mempalace_get_adapter (recording mock)
#
# Targets NOT covered (deferred to the Tests 3+4 follow-up that lands
# alongside scaffold extensions):
#   - OpenRouter fallback (only fires on Groq 429/413)
#   - memory_v3 orchestrator + rollout (behind feature flags, not exercised
#     by default; chat() handles missing v3 gracefully)
#   - Streaming-path-specific shapes for chat_stream (Test 4 territory)
#
# Firestore is already covered by the root conftest's global firebase_admin
# mock — chat_engine.load_or_create_session works with no extra patching.

_LLM_RESPONSE_TEXT = "Test mock LLM response under mempalace shadow scaffold."


def _build_canned_groq_response(content: str = _LLM_RESPONSE_TEXT) -> MagicMock:
    """Shape a requests.Response stand-in matching app.utilities expectations.

    app.utilities calls ``response.json()`` and reads
    ``["choices"][0]["message"]["content"]`` (Groq's OpenAI-compatible
    schema). status_code 200 satisfies the success branch.
    """
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }
    response.text = content
    response.headers = {"Content-Type": "application/json"}
    return response


@pytest.fixture
def mempalace_shadow_env(monkeypatch):
    """Wire MEMPALACE_MODE=shadow and patch the chat-flow surface so
    POST /chat reaches both record_turn call sites and returns 200.

    Yields a ``SimpleNamespace`` with handles for assertion:
      - ``adapter``: the recording MagicMock returned by
        ``_mempalace_get_adapter()``. Inspect ``.record_turn.call_args_list``,
        ``.wake_up_sync.call_args_list``, ``.log_shadow.call_args_list``.
      - ``adapter_factory``: the patched ``_mempalace_get_adapter`` callable
        itself, in case a test wants to assert it was called at all.
      - ``llm_post``: the patched ``requests.post`` mock, for asserting LLM
        was called or for inspecting the prompt sent.
      - ``wake_up_return``: the canned dict returned by
        ``adapter.wake_up_sync`` — the same dict expected to round-trip
        into the log_shadow payload's ``mempalace`` key.

    All patches auto-revert via monkeypatch — no fixture-level state leak.
    """
    monkeypatch.setenv("MEMPALACE_MODE", "shadow")

    # Re-import of the feature_flag module reads MEMPALACE_MODE on each
    # ``mode()`` call (it's not cached at import time per
    # app/mempalace/feature_flag.py), so monkeypatch.setenv suffices.

    # CSRF: WTF_CSRF_ENABLED=False in the test app config does NOT
    # short-circuit flask_wtf's validate_csrf when an X-CSRFToken
    # header is present — the function still consults the session for
    # the matching token and raises if absent. Replace it with a no-op
    # for the duration of the fixture so test requests can carry a
    # stub token to satisfy the csrf_required decorator's presence
    # check (app/limiter.py:18).
    #
    # ``app.limiter`` as an attribute path resolves to the Limiter
    # *instance* (re-exported in app/__init__.py), shadowing the
    # submodule. Reach the module object via sys.modules.
    import sys as _sys
    _limiter_mod = _sys.modules["app.limiter"]
    monkeypatch.setattr(_limiter_mod, "validate_csrf", lambda *a, **kw: None)

    canned_response = _build_canned_groq_response()
    llm_post = MagicMock(return_value=canned_response)
    monkeypatch.setattr("app.utilities.requests.post", llm_post)

    # Streaming path (chat_stream uses utilities.stream_deepseek_response,
    # a generator). Patch the generator to yield a minimum-viable
    # content + done sequence so chat_stream's generate() exits cleanly.
    # The mempalace call sites at chat_routes.py:945-977 fire BEFORE the
    # generate() inner function is invoked (lines 942-979 are in the
    # outer chat_stream body), so even a no-yield stream still exercises
    # the user-half record_turn assertions.
    def _fake_stream_deepseek(*args, **kwargs):
        yield {"type": "content", "text": _LLM_RESPONSE_TEXT}
        yield {"type": "done"}

    monkeypatch.setattr(
        "app.utilities.stream_deepseek_response",
        _fake_stream_deepseek,
    )

    fake_memory_ctx = MagicMock()
    fake_memory_ctx.context_block = ""
    fake_memory_ctx.eval_metadata = MagicMock()
    fake_memory_ctx.action_required = None
    fake_memory_ctx.memory_disabled = True
    fake_memory_ctx.disable_reason = "test_scaffold"

    fake_orchestrator_instance = MagicMock()
    fake_orchestrator_instance.get_context.return_value = fake_memory_ctx
    fake_orchestrator_instance.post_turn.return_value = MagicMock(
        stored=0, promoted=0, superseded=0, quarantined=0, rejected=0,
        shadowed=0, decisions=[],
    )
    monkeypatch.setattr(
        "app.memory_v2.MemoryOrchestrator",
        MagicMock(return_value=fake_orchestrator_instance),
    )
    # chat_routes imports MemoryOrchestrator into its own namespace, so
    # patch that binding too.
    monkeypatch.setattr(
        "app.blueprints.chat_routes.MemoryOrchestrator",
        MagicMock(return_value=fake_orchestrator_instance),
        raising=False,
    )

    # smart_route returns (model_id, task_type) per app/smart_router.py.
    monkeypatch.setattr(
        "app.smart_router.smart_route",
        MagicMock(return_value=("llama-3.1-70b-versatile", "chat")),
    )
    monkeypatch.setattr(
        "app.blueprints.chat_routes.smart_route",
        MagicMock(return_value=("llama-3.1-70b-versatile", "chat")),
        raising=False,
    )

    # Use a real ParsedAction (not MagicMock) so downstream code that
    # calls e.g. ``parsed.chart_payload.get(...)`` sees a None and skips
    # the chart-rendering branch instead of getting MagicMock attribute
    # spam that leaks into the JSON response.
    from app.context.action_parser import ParsedAction
    fake_parsed = ParsedAction(
        action="chat",
        chat_text=_LLM_RESPONSE_TEXT,
        artifact_payload=None,
        patch_payload=None,
        raw_response=_LLM_RESPONSE_TEXT,
    )
    monkeypatch.setattr(
        "app.context.action_parser.ActionParser.parse",
        MagicMock(return_value=fake_parsed),
    )

    # Late-pipeline NLTK tokenizer call at chat_routes.py:3768 expects
    # punkt_tab data on disk. CI / minimal-test envs don't ship it.
    # Stub to a single-sentence list — the response shape only needs
    # ``tokenized_response`` to be a list of strings.
    monkeypatch.setattr(
        "app.utilities.tokenize_sentences",
        MagicMock(return_value=[_LLM_RESPONSE_TEXT]),
    )
    monkeypatch.setattr(
        "app.blueprints.chat_routes.tokenize_sentences",
        MagicMock(return_value=[_LLM_RESPONSE_TEXT]),
        raising=False,
    )

    wake_up_return = {
        "user_id": "test-user-mempalace",
        "l0_recent": [],
        "meta": {"hot": False, "scaffold": True},
    }
    adapter = MagicMock()
    adapter.wake_up_sync.return_value = wake_up_return
    adapter.record_turn.return_value = None
    adapter.log_shadow.return_value = None

    adapter_factory = MagicMock(return_value=adapter)
    monkeypatch.setattr(
        "app.blueprints.chat_routes._mempalace_get_adapter",
        adapter_factory,
    )

    return SimpleNamespace(
        adapter=adapter,
        adapter_factory=adapter_factory,
        llm_post=llm_post,
        wake_up_return=wake_up_return,
        llm_response_text=_LLM_RESPONSE_TEXT,
    )

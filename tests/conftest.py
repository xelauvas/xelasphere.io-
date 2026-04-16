"""
Test-specific fixtures for Flask app testing.

This file provides Flask test client fixtures and common test utilities.
The Firebase mocking happens in the root conftest.py BEFORE imports.
"""

import os
import pytest
from unittest.mock import MagicMock, patch
import numpy as np


# Strip v2 env vars before every test so a live-QA .env can't pollute
# unit tests that expect a pristine environment. Tests that need v2
# enabled monkeypatch explicitly.
_V2_ENV_VARS = ("SCAFFOLD_V2_ENABLED", "SCAFFOLD_V2_INTERNAL_EMAILS")


@pytest.fixture(autouse=True)
def _clear_v2_env():
    saved = {k: os.environ.pop(k, None) for k in _V2_ENV_VARS}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


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

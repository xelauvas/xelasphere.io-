"""
Root conftest.py - Firebase mocking MUST happen before any app imports.

This file mocks firebase_admin BEFORE importing the app, preventing
real Firebase initialization at module import time in config.py.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Set test environment variables BEFORE any imports
os.environ['FLASK_ENV'] = 'testing'
os.environ['FLASK_SECRET_KEY'] = 'test-secret-key-for-testing-only'
os.environ['FIREBASE_CRED_PATH'] = '/tmp/fake-firebase-creds.json'
os.environ['GROQ_API_KEY'] = 'test-groq-key'
os.environ['OPENROUTER_API_KEY'] = 'test-openrouter-key'
os.environ['BRAVE_SEARCH_API_KEY'] = 'test-brave-key'
os.environ['SESSION_TYPE'] = 'filesystem'

# Mock os.path.exists to return True for Firebase creds path
_original_exists = os.path.exists


def _mock_exists(path):
    """Mock os.path.exists to allow fake Firebase creds."""
    if path == '/tmp/fake-firebase-creds.json':
        return True
    return _original_exists(path)


os.path.exists = _mock_exists


def _create_mock_firestore():
    """Create a mock Firestore client with proper chaining."""
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_document = MagicMock()
    mock_snapshot = MagicMock()
    mock_query = MagicMock()

    # Chain the mock methods for Firestore operations
    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_document
    mock_collection.where.return_value = mock_query
    mock_collection.order_by.return_value = mock_query
    mock_collection.limit.return_value = mock_query
    mock_query.where.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.stream.return_value = []
    mock_query.get.return_value = []
    mock_document.get.return_value = mock_snapshot
    mock_document.set.return_value = None
    mock_document.update.return_value = None
    mock_document.delete.return_value = None
    mock_document.collection.return_value = mock_collection
    mock_snapshot.exists = False
    mock_snapshot.to_dict.return_value = {}
    mock_snapshot.id = 'mock_doc_id'

    # Transaction support
    mock_transaction = MagicMock()
    mock_db.transaction.return_value = mock_transaction

    # Batch support
    mock_batch = MagicMock()
    mock_db.batch.return_value = mock_batch

    return mock_db


def _create_mock_firebase_admin():
    """Create complete mock of firebase_admin module."""
    mock_firebase = MagicMock()
    mock_firebase._apps = {}
    mock_firebase.initialize_app = MagicMock()

    # Mock credentials
    mock_cred = MagicMock()
    mock_firebase.credentials = MagicMock()
    mock_firebase.credentials.Certificate = MagicMock(return_value=mock_cred)

    # Mock firestore
    mock_db = _create_mock_firestore()
    mock_firebase.firestore = MagicMock()
    mock_firebase.firestore.client = MagicMock(return_value=mock_db)

    # Mock auth
    mock_firebase.auth = MagicMock()
    mock_firebase.auth.create_user = MagicMock()
    mock_firebase.auth.get_user = MagicMock()
    mock_firebase.auth.get_user_by_email = MagicMock()
    mock_firebase.auth.verify_id_token = MagicMock()
    mock_firebase.auth.generate_password_reset_link = MagicMock(return_value='https://test-reset-link.com')
    mock_firebase.auth.verify_password_reset_code = MagicMock()
    mock_firebase.auth.confirm_password_reset = MagicMock()

    return mock_firebase, mock_db


# Create the mocks
_mock_firebase_admin, _mock_db = _create_mock_firebase_admin()

# Mock firebase_admin.exceptions — needed because app/auth.py:11 does
# `from firebase_admin.exceptions import FirebaseError`, and the bare
# MagicMock() isn't a "package" so Python can't resolve submodule
# imports through it. Providing the submodule explicitly in sys.modules
# bypasses the package-resolution check.
_mock_firebase_exceptions = MagicMock()
_mock_firebase_exceptions.FirebaseError = type("FirebaseError", (Exception,), {})
_mock_firebase_admin.exceptions = _mock_firebase_exceptions

# Patch sys.modules BEFORE any app imports can happen
sys.modules['firebase_admin'] = _mock_firebase_admin
sys.modules['firebase_admin.credentials'] = _mock_firebase_admin.credentials
sys.modules['firebase_admin.firestore'] = _mock_firebase_admin.firestore
sys.modules['firebase_admin.auth'] = _mock_firebase_admin.auth
sys.modules['firebase_admin.exceptions'] = _mock_firebase_exceptions


@pytest.fixture(scope="session")
def mock_firestore():
    """Provide the mocked Firestore client."""
    return _mock_db


@pytest.fixture(scope="session")
def mock_firebase_auth():
    """Provide the mocked Firebase Auth."""
    return _mock_firebase_admin.auth

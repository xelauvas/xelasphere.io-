"""Shared decorators, exceptions, helpers, and instances for all blueprints.

This module centralizes code that is used across multiple blueprints to avoid
circular imports and duplication.
"""

import atexit
import hashlib
import json
import logging
import os
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from typing import Any, Dict, Optional

from flask import jsonify, request, session, Response
from google.cloud.exceptions import NotFound

from app.config import Config, db
from app.grog_api import TaskGenerator
from app.artifacts import ArtifactRouter, ArtifactStore
from app.chat_session import ChatSession

# Set up logging
logger = logging.getLogger(__name__)

# ==================== Guest Mode Constants ====================
GUEST_MESSAGE_LIMIT = 5
GUEST_BANNER_THRESHOLD = 3


# ==================== Custom Exceptions ====================
class ModelProviderError(Exception):
    """Raised when an upstream AI provider fails and we want to surface a friendly message."""
    def __init__(self, message: str, error_code: Optional[str] = None, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class TokenLimitError(Exception):
    """Custom exception raised when the token limit is exceeded."""
    pass


# ==================== Guest Helpers ====================
def is_guest():
    """Check if current session is a guest (not logged in)."""
    return 'guest' in session and 'user' not in session


def get_guest_message_count():
    """Get the current guest message count."""
    if is_guest():
        return session['guest'].get('message_count', 0)
    return 0


# ==================== IP Helpers ====================
def get_client_ip():
    """Get the real client IP, handling proxies."""
    # Cloudflare header (most reliable when using Cloudflare)
    if request.headers.get('CF-Connecting-IP'):
        ip = request.headers.get('CF-Connecting-IP')
    # Check for forwarded headers (behind proxy/load balancer)
    elif request.headers.get('X-Forwarded-For'):
        # X-Forwarded-For can contain multiple IPs, first one is the client
        ip = request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        ip = request.headers.get('X-Real-IP')
    else:
        ip = request.remote_addr
    return ip or '0.0.0.0'


def hash_ip(ip: str) -> str:
    """Hash IP for privacy - we don't store raw IPs."""
    # Add a salt from config for extra security
    salt = Config.SECRET_KEY[:16] if hasattr(Config, 'SECRET_KEY') else 'xelasphere'
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


# ==================== Response Helpers ====================
def make_etag_response(data, status_code=200):
    """Create a JSON response with ETag support for cache validation.

    Returns 304 Not Modified if client's If-None-Match header matches the ETag.
    """
    json_data = json.dumps(data, separators=(',', ':'), sort_keys=True)
    etag = hashlib.md5(json_data.encode()).hexdigest()

    # Check if client has matching ETag
    if_none_match = request.headers.get('If-None-Match')
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status=304)

    response = Response(json_data, status=status_code, mimetype='application/json')
    response.headers['ETag'] = f'"{etag}"'
    response.headers['Cache-Control'] = 'private, must-revalidate'
    return response


# ==================== Decorators ====================
def login_required(f):
    """Decorator to ensure the user is logged in before accessing the route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'You need to be logged in to perform this action.'}), 403
        return f(*args, **kwargs)
    return decorated_function


def guest_allowed(f):
    """Allow guests with limited features, or full access for logged-in users."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session and 'guest' not in session:
            # Create guest session
            session['guest'] = {
                'id': f'guest_{uuid.uuid4().hex[:12]}',
                'message_count': 0,
                'created_at': datetime.utcnow().isoformat()
            }
            session.modified = True
        return f(*args, **kwargs)
    return decorated_function


def error_handler(f):
    """Decorator to handle exceptions and provide consistent error responses."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except NotFound as e:
            logger.error(f"Firestore document not found: {str(e)}")
            return jsonify({'error': 'Resource not found.'}), 404
        except ValueError as e:
            logger.error(f"Invalid argument: {str(e)}")
            return jsonify({'error': 'Invalid operation.'}), 400
        except ModelProviderError as e:
            logger.error(f"Model provider error ({e.error_code}): {e.message}")
            payload = {'error': e.message}
            if e.error_code:
                payload['error_code'] = e.error_code
            return jsonify(payload), e.status_code
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            logger.debug(traceback.format_exc())
            return jsonify({'error': 'An unexpected error occurred.'}), 500
    return wrapper


# ==================== Shared Instances ====================
# These are initialized once and imported by all blueprints

# TaskGenerator for AI model interactions
task_generator = TaskGenerator()

# Chat session handler
chat_session = ChatSession(db)

# Artifact handling
artifact_router = ArtifactRouter()
artifact_store = ArtifactStore(db)

# Thread pool for concurrent operations
executor = ThreadPoolExecutor(max_workers=3)


def cleanup_executor():
    """Cleanup function to shut down executor on app exit."""
    executor.shutdown(wait=True)


atexit.register(cleanup_executor)


# ==================== Guest IP Tracking (Firebase) ====================
def get_guest_ip_usage(ip_hash: str) -> dict:
    """Get guest usage data from Firebase by IP hash."""
    try:
        doc_ref = db.collection('guest_usage').document(ip_hash)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.warning(f"Failed to get guest IP usage: {e}")
        return None


def increment_guest_ip_usage(ip_hash: str) -> int:
    """Increment guest message count in Firebase. Returns new count."""
    from google.cloud import firestore as gc_firestore
    try:
        doc_ref = db.collection('guest_usage').document(ip_hash)
        doc = doc_ref.get()

        now = datetime.utcnow()
        today = now.strftime('%Y-%m-%d')

        if doc.exists:
            data = doc.to_dict()
            # Reset count if it's a new day
            if data.get('last_date') != today:
                new_count = 1
                doc_ref.update({
                    'message_count': new_count,
                    'last_date': today,
                    'last_used': now.isoformat(),
                    'total_messages': gc_firestore.Increment(1)
                })
            else:
                new_count = data.get('message_count', 0) + 1
                doc_ref.update({
                    'message_count': new_count,
                    'last_used': now.isoformat(),
                    'total_messages': gc_firestore.Increment(1)
                })
            return new_count
        else:
            # First time seeing this IP
            doc_ref.set({
                'message_count': 1,
                'last_date': today,
                'first_seen': now.isoformat(),
                'last_used': now.isoformat(),
                'total_messages': 1
            })
            return 1
    except Exception as e:
        logger.warning(f"Failed to increment guest IP usage: {e}")
        # Fallback to session-based tracking if Firebase fails
        return -1


def check_guest_ip_limit(ip_hash: str) -> tuple:
    """Check if guest IP has exceeded daily limit. Returns (allowed, current_count)."""
    try:
        data = get_guest_ip_usage(ip_hash)
        if not data:
            return True, 0

        today = datetime.utcnow().strftime('%Y-%m-%d')
        # Reset count if it's a new day
        if data.get('last_date') != today:
            return True, 0

        current_count = data.get('message_count', 0)
        return current_count < GUEST_MESSAGE_LIMIT, current_count
    except Exception as e:
        logger.warning(f"Failed to check guest IP limit: {e}")
        # Allow on error, fallback to session tracking
        return True, 0


# ==================== Firestore db reference ====================
# Re-export db for convenience
db = db


# ==================== Admin gating ====================
DEFAULT_ADMIN_EMAILS = ('a.savu.stefan@gmail.com',)


def is_admin_user(user):
    """Return True when the session user is an admin.

    Admin email allowlist is read from the ADMIN_EMAILS env var
    (comma-separated) and falls back to DEFAULT_ADMIN_EMAILS when unset.
    """
    if not isinstance(user, dict):
        return False
    email = user.get('email')
    if not email:
        return False
    env_emails = os.environ.get('ADMIN_EMAILS', '')
    allowed = tuple(e.strip() for e in env_emails.split(',') if e.strip()) or DEFAULT_ADMIN_EMAILS
    return email in allowed

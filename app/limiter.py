import os
import logging
from functools import wraps
from flask import session, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import validate_csrf

storage_uri = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')
logger = logging.getLogger(__name__)


def csrf_required(f):
    """Decorator to explicitly validate CSRF token for AJAX endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        csrf_token = request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token')
        if not csrf_token:
            return jsonify({'error': 'CSRF token missing.'}), 403
        try:
            validate_csrf(csrf_token)
        except Exception as e:
            logger.warning(f"CSRF validation failed: {str(e)}")
            return jsonify({'error': 'CSRF token invalid.'}), 403
        return f(*args, **kwargs)
    return decorated_function


def get_rate_limit_key():
    """
    Smart rate limit key function:
    - Authenticated users: limit per user ID (fair for users behind same IP)
    - Guests/unauthenticated: limit per IP address
    """
    # Check if user is authenticated
    if 'user' in session and session['user'].get('uid'):
        return f"user:{session['user']['uid']}"

    # Check if guest session exists
    if 'guest' in session and session['guest'].get('id'):
        return f"guest:{session['guest']['id']}"

    # Fall back to IP address for unauthenticated requests
    return f"ip:{get_remote_address()}"


def get_auth_rate_limit_key():
    """
    Rate limit key for auth endpoints (login/register).
    Uses IP + email to prevent brute force on specific accounts
    while allowing many users from same IP to authenticate.
    """
    ip = get_remote_address()

    # Try to get email from form data (POST) or query params
    email = None
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()

    if email:
        # Limit per IP + email combination (prevents brute force on specific account)
        return f"auth:{ip}:{email}"

    # Fallback to IP only for GET requests or missing email
    return f"auth:{ip}"


limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[],
    storage_uri=storage_uri
)

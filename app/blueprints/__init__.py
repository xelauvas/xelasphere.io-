"""Blueprint package for XelaSphere routes.

This package contains modular Flask blueprints split from the monolithic views.py.
Each blueprint handles a specific domain of functionality.
"""

from .weather_routes import weather_bp
from .email_routes import email_bp
from .account_routes import account_bp
from .memory_routes import memory_bp
from .session_routes import session_bp
from .pages_routes import pages_bp
from .artifacts_routes import artifacts_bp
from .media_routes import media_bp
from .upload_routes import upload_bp
from .chat_routes import chat_bp
from .reminders_routes import reminders_bp
from .push_routes import push_bp

__all__ = [
    'weather_bp',
    'email_bp',
    'account_bp',
    'memory_bp',
    'session_bp',
    'pages_bp',
    'artifacts_bp',
    'media_bp',
    'upload_bp',
    'chat_bp',
    'reminders_bp',
    'push_bp',
]

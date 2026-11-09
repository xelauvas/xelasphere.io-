from flask import Flask, render_template, Response, request, jsonify
from flask_session import Session
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_cors import CORS  # Import CORS
from markupsafe import Markup
import bleach
import gc
import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
import os
from .config import Config, db  # Import the db object
from .limiter import limiter
from .firestore_session import FirestoreSessionInterface
import traceback

# Allowed HTML tags and attributes for sanitization
ALLOWED_TAGS = [
    'a', 'abbr', 'acronym', 'b', 'blockquote', 'br', 'code', 'div', 'em',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p', 'pre',
    'span', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
    'img', 'figure', 'figcaption', 'details', 'summary', 'mark', 'del', 'ins', 'sup', 'sub'
]
ALLOWED_ATTRIBUTES = {
    '*': ['class', 'id', 'style'],
    'a': ['href', 'title', 'target', 'rel'],
    'img': ['src', 'alt', 'title', 'width', 'height'],
    'td': ['colspan', 'rowspan'],
    'th': ['colspan', 'rowspan', 'scope'],
}
# Only allow safe URL schemes
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# Make db accessible from the app package
# This way, other modules can import db from the app package
db = db

def create_app():
    app = Flask(__name__)
    
    try:
        # Load configuration
        app.config.from_object(Config)

        # M0.13: refuse to boot if MEMPALACE_MODE + LEGACY_MEMORY
        # combination would leave users with no memory subsystem.
        # Runs immediately after config load so the check fires
        # before any other init work (CSRF, limiter, CORS, session,
        # blueprints). SystemExit(2) from an unsafe flag combination
        # short-circuits the whole create_app — production deploy
        # fails loud rather than limping along with partial state.
        #
        # SystemExit is a BaseException (not Exception), so it
        # propagates past the outer ``except Exception`` wrapper
        # around this try-block by design. A future refactor that
        # narrows the wrapper (e.g., ``except (Exception,
        # SystemExit)``) would silently swallow the sanity check —
        # do not add SystemExit to the caught types without
        # reviewing this invariant.
        from app.mempalace.feature_flag import assert_startup_sane
        assert_startup_sane()

        # Log that the SECRET_KEY is properly set
        app.logger.info("Initializing with SECRET_KEY (first 4 chars): %s...", app.config['SECRET_KEY'][:4] if app.config['SECRET_KEY'] else "Not set!")
        
        # Check other critical config values
        if not app.config['SECRET_KEY']:
            app.logger.error("SECRET_KEY is not set!")
        
        # Setup CSRF protection
        csrf = CSRFProtect(app)

        # Rate limiter
        limiter.init_app(app)
    
        # Enable CORS for all routes
        CORS(app, resources={r"/*": {"origins": Config.ALLOWED_ORIGINS if hasattr(Config, 'ALLOWED_ORIGINS') else ["https://xelas.io", "http://localhost:5000"]}})
    
        # Configure session management
        try:
            session_type = app.config.get('SESSION_TYPE', 'filesystem')
            if session_type == 'firestore':
                # Use Firestore-backed sessions for Cloud Run persistence
                app.session_interface = FirestoreSessionInterface(
                    db=db,
                    collection='flask_sessions',
                    expire_seconds=86400 * 7  # 7 days
                )
                app.logger.info("Session configuration: Firestore backend")
            else:
                # Use Flask-Session (filesystem or other backends)
                app.config['SESSION_FILE_THRESHOLD'] = 200  # Limit cached session files
                Session(app)
                app.logger.info(f"Session configuration: {session_type} backend")
        except Exception as e:
            app.logger.error(f"Session configuration error: {str(e)}")
            # If there's an issue with the session, fall back to simpler configuration
            app.config['SESSION_USE_SIGNER'] = False
            app.logger.info("Attempting fallback session configuration without signing")
            Session(app)
    except Exception as e:
        app.logger.error(f"Error during app initialization: {str(e)}")
        app.logger.error(traceback.format_exc())
        # Still continue with minimal app setup so the error can be shown

    # Add security headers middleware
    @app.after_request
    def add_security_headers(response: Response) -> Response:
        # Content Security Policy (CSP)
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com 'unsafe-inline' 'unsafe-eval'",
            "style-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com 'unsafe-inline'",
            "img-src 'self' data: https:",
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net",
            "connect-src 'self' https://api.groq.com https://openrouter.ai https://cdn.jsdelivr.net https://unpkg.com",
            "media-src 'self' blob:",
            "frame-src 'self' data: blob: https://www.youtube.com https://www.youtube-nocookie.com",
            "frame-ancestors 'none'",
            "object-src 'none'",
            "form-action 'self'",
            "base-uri 'self'"
        ]
        response.headers['Content-Security-Policy'] = '; '.join(csp_directives)
        
        # Additional security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(self), camera=()'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        return response

    # Periodic garbage collection to reclaim memory from large request payloads
    _request_counter = {'count': 0}

    @app.teardown_request
    def trigger_gc(exc=None):
        _request_counter['count'] += 1
        if _request_counter['count'] % 50 == 0:
            gc.collect()

    # Register custom Jinja2 filters for XSS protection
    @app.template_filter('sanitize_html')
    def sanitize_html_filter(text):
        """Sanitize HTML to prevent XSS attacks while allowing safe formatting."""
        if text is None:
            return ''
        cleaned = bleach.clean(
            str(text),
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            protocols=ALLOWED_PROTOCOLS,
            strip=True
        )
        return Markup(cleaned)

    @app.template_filter('sanitize_flash')
    def sanitize_flash_filter(text):
        """Sanitize flash messages - only allow links and basic formatting."""
        if text is None:
            return ''
        # More restrictive for flash messages
        flash_tags = ['a', 'strong', 'em', 'b', 'i', 'br', 'span']
        flash_attrs = {
            '*': ['class'],
            'a': ['href', 'class'],
        }
        cleaned = bleach.clean(
            str(text),
            tags=flash_tags,
            attributes=flash_attrs,
            protocols=['http', 'https'],
            strip=True
        )
        return Markup(cleaned)

    # Configure file upload size and extensions
    app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH
    app.config['UPLOAD_EXTENSIONS'] = ['.jpg', '.png', '.pdf']  # Allowed extensions

    # Import blueprints after app is created to avoid circular imports
    from .auth import auth_routes
    from .crypto_api import crypto_bp

    # Import modular blueprints
    from .blueprints import (
        weather_bp, email_bp, account_bp, memory_bp, session_bp,
        pages_bp, artifacts_bp, media_bp, upload_bp, chat_bp,
        reminders_bp, push_bp,
    )

    # Register all blueprints
    app.register_blueprint(weather_bp)
    app.register_blueprint(email_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(memory_bp)
    app.register_blueprint(session_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reminders_bp)
    app.register_blueprint(push_bp)
    app.register_blueprint(auth_routes)
    app.register_blueprint(crypto_bp)
    from app.blueprints.project_routes import project_bp
    app.register_blueprint(project_bp)
    from app.blueprints.status_routes import status_bp
    app.register_blueprint(status_bp)

    from app.blueprints.sandbox_routes import sandbox_bp
    app.register_blueprint(sandbox_bp)

    @app.context_processor
    def _xela_model_menu():
        """Selectable models for the composer's model menu (templates/index.html)."""
        try:
            from app.model_catalog import MODEL_CATALOG
            models = [
                {
                    'id': key,
                    'label': spec.get('label', key),
                    'provider': spec.get('provider'),
                    'speed': spec.get('speed'),
                    'reasoning': bool(spec.get('reasoning')),
                }
                for key, spec in MODEL_CATALOG.items() if spec.get('selectable')
            ]
        except Exception:  # never break page rendering over the menu
            models = []
        return {'xela_models': models}
    from .blueprints.cli_routes import cli_bp, cli_api_bp  # Xela Code sign-in + API keys
    app.register_blueprint(cli_bp)
    app.register_blueprint(cli_api_bp)
    csrf.exempt(sandbox_bp)
    csrf.exempt(cli_api_bp)

    # Configure logging
    if not os.path.exists('logs'):
        os.mkdir('logs')

    log_file = 'logs/app.log'
    file_handler = ConcurrentRotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    
    # Avoid duplicate log entries by ensuring no multiple handlers
    if not app.logger.hasHandlers():
        app.logger.addHandler(file_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.info('Application startup')

    # Initialize model orchestrator (health checks, quota tracking, smart routing)
    from app.orchestrator import initialize as init_orchestrator
    init_orchestrator()

    # Error handlers
    @app.errorhandler(404)
    def not_found_error(e):
        app.logger.warning(f"404 error occurred: {request.path}")
        return render_template('404.html', requested_url=request.path), 404

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.error(f"500 error occurred: {str(e)}")
        # Log the stack trace but don't expose it to users
        app.logger.error(traceback.format_exc())
        return render_template('500.html'), 500
        
    @app.errorhandler(403)
    def forbidden_error(e):
        app.logger.warning(f"403 error occurred: {request.path}")
        return render_template('403.html'), 403
        
    @app.errorhandler(400)
    def bad_request_error(e):
        app.logger.warning(f"400 error occurred: {str(e)}")
        return render_template('400.html'), 400

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        app.logger.warning(f"CSRF error: {e.description}")
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': 'CSRF token invalid.'}), 403
        return render_template('csrf_error.html', reason=e.description), 400

    return app

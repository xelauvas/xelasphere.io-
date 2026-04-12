from dotenv import load_dotenv
import os

# Load environment variables from .env file before importing the app
load_dotenv()

# Runtime code coverage — flips on via env flag (COVERAGE_ENABLED=1).
# Data is written to /var/log/xelasphere/coverage/.coverage.<pid>.<uuid>
# per worker. Saved periodically (every 60s) AND on worker exit so we
# don't lose data if gunicorn force-kills the process.
# Must start BEFORE importing app so module-level code is traced.
_cov = None
if os.getenv('COVERAGE_ENABLED') == '1':
    try:
        import coverage as _coverage
        _cov = _coverage.Coverage(
            data_file='/var/log/xelasphere/coverage/.coverage',
            data_suffix=True,
            source=['app'],
            branch=True,
            concurrency=['thread'],
            omit=['*/tests/*', '*/migrations/*'],
        )
        _cov.start()

        # Periodic flush: save coverage every 60s so data persists
        # even if worker is force-killed by gunicorn.
        import threading
        def _periodic_save():
            try:
                _cov.save()
            except Exception:
                pass
            t = threading.Timer(60.0, _periodic_save)
            t.daemon = True
            t.start()
        _periodic_save()

        # atexit as secondary flush path
        import atexit
        atexit.register(lambda: (_cov.stop(), _cov.save()))
    except Exception as _e:
        print(f"[coverage] disabled due to error: {_e}")

from app import create_app

app = create_app()

# Note: Embedding model loads lazily on first request (~40s for jina-embeddings-v3)
# Subsequent requests are fast. Use min-instances=1 to avoid cold starts if needed.

if __name__ == "__main__":
    flask_env = os.getenv("FLASK_ENV", "production")
    default_port = 5000 if flask_env != "production" else 5001
    port = int(os.getenv("FLASK_RUN_PORT", os.getenv("PORT", default_port)))
    app.run(debug=(flask_env != "production"), port=port)

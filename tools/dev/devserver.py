"""Isolated XelaSphere dev instance for UI work. No production credentials, no shared data.

Firestore -> in-memory fake (tools/dev/fake_firestore.py), Redis -> fakeredis, Firebase Admin
-> stubs, sessions -> a temp dir, rate limits -> memory. External AI providers have no keys,
except the local model when OLLAMA_API_URL / OLLAMA_API_KEY are given (reverse tunnel).

    PYTHONPATH=<devdeps>:. <venv>/bin/python tools/dev/devserver.py [--port 5056]

Extra routes:  /__dev/login?name=Ștefan&next=/   /__dev/logout
"""
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 5056

_tmp = tempfile.mkdtemp(prefix="xela-dev-")
_cred = os.path.join(_tmp, "fake-service-account.json")
open(_cred, "w").write("{}")
for k, v in {
    "FLASK_ENV": "development",
    "FLASK_SECRET_KEY": "dev-only-secret",
    "APP_BASE_URL": f"http://127.0.0.1:{PORT}",
    "ALLOWED_ORIGINS": f"http://127.0.0.1:{PORT}",
    "SESSION_TYPE": "filesystem",
    "SESSION_FILE_DIR": os.path.join(_tmp, "sessions"),
    "SESSION_COOKIE_SECURE": "false",
    "RATELIMIT_STORAGE_URI": "memory://",
    "REDIS_URL": "redis://fake:6379/0",
    "FIREBASE_CRED_PATH": _cred,
    "FIREBASE_WEB_API_KEY": "dev",
    "GOOGLE_REDIRECT_URI": "https://dev.invalid/auth/google/callback",
    "MICROSOFT_REDIRECT_URI": "https://dev.invalid/auth/microsoft/callback",
    "DISCORD_REDIRECT_URI": "https://dev.invalid/auth/discord/callback",
    "OLLAMA_MODEL": "qwen3.6-27b",
    "MEMPALACE_MODE": "off",
    "REMINDERS_ENABLED": "false",
    "PUSH_ENABLED": "false",
}.items():
    os.environ.setdefault(k, v)

# ---- Firebase Admin + Firestore -> fakes (before the app imports them) ----
import fake_firestore  # noqa: E402
import firebase_admin  # noqa: E402
from firebase_admin import credentials, firestore  # noqa: E402

_client = fake_firestore.FakeClient()
credentials.Certificate = lambda *a, **k: types.SimpleNamespace(project_id="xelasphere-dev")


def _init_app(*a, **k):
    app = types.SimpleNamespace(name="[DEFAULT]", project_id="xelasphere-dev", options={})
    firebase_admin._apps["[DEFAULT]"] = app
    return app


firebase_admin.initialize_app = _init_app
firestore.client = lambda *a, **k: _client
firestore.transactional = fake_firestore.transactional
try:
    import google.cloud.firestore as _gcf
    _gcf.transactional = fake_firestore.transactional
except Exception:
    pass
try:
    from firebase_admin import storage as _storage
    from unittest import mock as _mock
    _storage.bucket = lambda *a, **k: _mock.MagicMock(name="fake-bucket")
except Exception:
    pass

# ---- Redis -> fakeredis ----
import fakeredis  # noqa: E402
import redis  # noqa: E402

_server = fakeredis.FakeServer()
redis.from_url = lambda url=None, **kw: fakeredis.FakeRedis(server=_server, **{
    k: v for k, v in kw.items() if k in ("decode_responses", "encoding")})
redis.Redis.from_url = staticmethod(redis.from_url)

# ---- the app ----
from flask import redirect, request, session  # noqa: E402
from app import create_app  # noqa: E402

app = create_app()
app.config.update(SESSION_COOKIE_SECURE=False, TEMPLATES_AUTO_RELOAD=True, SEND_FILE_MAX_AGE_DEFAULT=0)
app.jinja_env.auto_reload = True


@app.route("/__dev/login")
def _dev_login():
    session.clear()
    session["user"] = {
        "uid": "dev-user",
        "email": "stefan@xelasphere.dev",
        "name": request.args.get("name", "Ștefan Savu"),
        "picture": "/static/images/default-profile.png",
        "show_status": True,
    }
    session.modified = True
    return redirect(request.args.get("next", "/"))


@app.route("/__dev/seed")
def _dev_seed():
    import seed
    n = seed.seed(_client, "dev-user")
    return redirect(request.args.get("next", "/") + ("" if "?" in request.args.get("next", "/") else f"?seeded={n}"))


@app.route("/__dev/logout")
def _dev_logout():
    session.clear()
    return redirect(request.args.get("next", "/"))


if __name__ == "__main__":
    print(f"XelaSphere dev instance on http://127.0.0.1:{PORT} (fake data in {_tmp})", flush=True)
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True, use_reloader=False)

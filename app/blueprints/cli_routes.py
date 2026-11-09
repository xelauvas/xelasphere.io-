"""Xela Code link: device-code sign-in, per-user API keys and the nginx auth_request verifier.

Flow (same shape as `claude login` / the GitHub device flow):
  1. CLI   POST /api/cli/device                -> device_code, user_code, verification URL
  2. User  opens https://xelasphere.com/cli/auth?code=XXXX-XXXX, signs in, clicks Authorize
  3. CLI   POST /api/cli/token {device_code}   -> api_key (handed out once) after approval
  4. CLI   sends `Authorization: Bearer xsk_...` to https://api.xelasphere.com/v1/...;
           nginx calls GET /api/cli/verify (auth_request) before proxying to the model server.

Keys are stored hashed (sha256) in the Firestore collection `cli_api_keys`. Pending device
codes live in Redis for 15 minutes; verified keys are cached in Redis for 5 minutes.
On api.xelasphere.com nginx exposes /api/cli/* as /cli/* so the CLI needs a single base URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone

from flask import Blueprint, jsonify, make_response, redirect, render_template, request, session

from app.config import Config, db
from app.blueprints.shared import login_required
from app.limiter import limiter
from app.redis_client import cache_get, cache_set, get_redis

logger = logging.getLogger(__name__)

cli_bp = Blueprint("cli", __name__)          # browser pages (CSRF-protected form)
cli_api_bp = Blueprint("cli_api", __name__)  # machine endpoints (CSRF-exempt; bearer or none)

DEVICE_TTL = 15 * 60       # a sign-in attempt is valid for 15 minutes
APPROVED_TTL = 2 * 60      # the CLI must collect the key within 2 minutes of approval
POLL_INTERVAL = 3
KEY_CACHE_TTL = 5 * 60
KEYS_COLLECTION = "cli_api_keys"
USER_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXYZ23456789"  # no vowels, no 0/O/1/I


# ==================== helpers ====================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _new_user_code() -> str:
    s = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{s[:4]}-{s[4:]}"


def _normalize_user_code(raw: str) -> str:
    s = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    return f"{s[:4]}-{s[4:8]}" if len(s) >= 8 else s


def _api_base() -> str:
    return (getattr(Config, "XELA_API_BASE_URL", None) or "https://api.xelasphere.com").rstrip("/")


def _site_base() -> str:
    return (getattr(Config, "APP_BASE_URL", None) or "https://xelasphere.com").rstrip("/")


def _default_model() -> str:
    return getattr(Config, "OLLAMA_MODEL", None) or "qwen3.6-27b"


def _device_key(device_code: str) -> str:
    return f"cli:device:{device_code}"


def _user_code_key(user_code: str) -> str:
    return f"cli:user_code:{user_code}"


def _load_device(device_code: str):
    try:
        raw = get_redis().get(_device_key(device_code))
    except Exception as exc:  # Redis down → treat as unknown
        logger.warning("cli: redis unavailable: %s", exc)
        return None
    return json.loads(raw) if raw else None


def _save_device(device_code: str, rec: dict, ttl: int) -> None:
    get_redis().setex(_device_key(device_code), ttl, json.dumps(rec))


def _lookup_user_code(user_code: str):
    """Return (device_code, record) for a user code, or (None, None)."""
    if not user_code:
        return None, None
    try:
        device_code = get_redis().get(_user_code_key(user_code))
    except Exception as exc:
        logger.warning("cli: redis unavailable: %s", exc)
        return None, None
    if not device_code:
        return None, None
    rec = _load_device(device_code)
    return (device_code, rec) if rec else (None, None)


def _bearer_token() -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else ""


def verify_api_key(token: str):
    """Return {'uid', 'email', 'name'} for a valid key, or None."""
    if not token or len(token) > 200:
        return None
    legacy = getattr(Config, "XELA_API_LEGACY_KEY", None)
    if legacy and hmac.compare_digest(token, legacy):
        return {"uid": "legacy", "email": "legacy-api-key", "name": "Legacy API key"}
    if not token.startswith("xsk_"):
        return None
    key_hash = _hash_key(token)
    cache_key = f"cli:key:{key_hash}"
    cached = cache_get(cache_key)
    if isinstance(cached, dict):
        return cached.get("user") if cached.get("valid") else None
    try:
        snap = db.collection(KEYS_COLLECTION).document(key_hash).get()
    except Exception as exc:
        logger.error("cli: firestore lookup failed: %s", exc)
        return None
    if not snap.exists:
        cache_set(cache_key, {"valid": False}, 60)
        return None
    data = snap.to_dict() or {}
    if data.get("revoked"):
        cache_set(cache_key, {"valid": False}, 60)
        return None
    user = {"uid": data.get("uid"), "email": data.get("email"), "name": data.get("name")}
    cache_set(cache_key, {"valid": True, "user": user}, KEY_CACHE_TTL)
    try:  # at most once per cache TTL
        snap.reference.update({"last_used_at": _now_iso()})
    except Exception:
        pass
    return user


def _invalid_key_response():
    return jsonify(error={
        "message": "Incorrect API key provided. Run `xela-local --login` to sign in with your XelaSphere account.",
        "type": "invalid_request_error",
        "code": "invalid_api_key",
    }), 401


# ==================== machine endpoints (CSRF-exempt) ====================

@cli_api_bp.route("/api/cli/device", methods=["POST"])
@limiter.limit("10 per minute")
def device_start():
    data = request.get_json(silent=True) or {}
    client = str(data.get("client") or "xela-code")[:40]
    hostname = str(data.get("hostname") or "")[:80]
    try:
        r = get_redis()
        device_code = secrets.token_urlsafe(32)
        user_code = None
        for _ in range(6):
            candidate = _new_user_code()
            if r.set(_user_code_key(candidate), device_code, ex=DEVICE_TTL, nx=True):
                user_code = candidate
                break
        if not user_code:
            return jsonify(error="server_error", error_description="Could not allocate a code, try again."), 503
        rec = {"status": "pending", "user_code": user_code, "client": client,
               "hostname": hostname, "created_at": _now_iso()}
        _save_device(device_code, rec, DEVICE_TTL)
    except Exception as exc:
        logger.error("cli: device start failed: %s", exc)
        return jsonify(error="server_error", error_description="Sign-in service unavailable."), 503
    verification_uri = f"{_site_base()}/cli/auth"
    return jsonify(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=f"{verification_uri}?code={user_code}",
        expires_in=DEVICE_TTL,
        interval=POLL_INTERVAL,
    )


@cli_api_bp.route("/api/cli/token", methods=["POST"])
@limiter.limit("60 per minute")
def device_token():
    data = request.get_json(silent=True) or {}
    device_code = str(data.get("device_code") or "")
    if not device_code or len(device_code) > 128:
        return jsonify(error="invalid_request"), 400
    rec = _load_device(device_code)
    if not rec:
        return jsonify(error="expired_token",
                       error_description="This sign-in expired or is unknown. Run the login again."), 400
    status = rec.get("status")
    if status == "pending":
        return jsonify(error="authorization_pending"), 428
    try:
        get_redis().delete(_device_key(device_code))  # one-shot in every terminal state
    except Exception:
        pass
    if status == "denied":
        return jsonify(error="access_denied", error_description="The request was denied in the browser."), 403
    if status == "approved":
        return jsonify(api_key=rec["api_key"], email=rec.get("email"), name=rec.get("name"),
                       server=_api_base(), site=_site_base(), model=_default_model())
    return jsonify(error="invalid_request"), 400


@cli_api_bp.route("/api/cli/verify", methods=["GET"])
@limiter.exempt
def verify():
    """nginx auth_request target: 204 + X-Xela-User for a valid key, 401 otherwise."""
    user = verify_api_key(_bearer_token())
    if not user:
        return _invalid_key_response()
    resp = make_response("", 204)
    resp.headers["X-Xela-User"] = user.get("email") or ""
    return resp


@cli_api_bp.route("/api/cli/me", methods=["GET"])
@limiter.limit("60 per minute")
def me():
    user = verify_api_key(_bearer_token())
    if not user:
        return _invalid_key_response()
    return jsonify(user=user, server=_api_base(), site=_site_base(), model=_default_model())


@cli_api_bp.route("/api/cli/keys", methods=["GET"])
@login_required
def list_keys():
    uid = session["user"].get("uid")
    shown = ("key_prefix", "client", "hostname", "created_at", "last_used_at", "revoked")
    keys = []
    for doc in db.collection(KEYS_COLLECTION).where("uid", "==", uid).stream():
        data = doc.to_dict() or {}
        keys.append({"id": doc.id, **{k: data.get(k) for k in shown}})
    keys.sort(key=lambda k: k.get("created_at") or "", reverse=True)
    return jsonify(keys=keys)


@cli_api_bp.route("/api/cli/keys/<key_id>", methods=["DELETE"])
@login_required
def revoke_key(key_id: str):
    if not key_id or len(key_id) != 64:
        return jsonify(error="not_found"), 404
    ref = db.collection(KEYS_COLLECTION).document(key_id)
    snap = ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("uid") != session["user"].get("uid"):
        return jsonify(error="not_found"), 404
    ref.update({"revoked": True, "revoked_at": _now_iso()})
    try:
        get_redis().delete(f"cli:key:{key_id}")
    except Exception:
        pass
    return jsonify(ok=True)


# ==================== browser pages ====================

@cli_bp.route("/cli/auth", methods=["GET"])
def authorize_page():
    code = _normalize_user_code(request.args.get("code", ""))
    if "user" not in session:
        if code:
            session["cli_pending_code"] = code
            session.modified = True
        return render_template("cli_auth.html", state="login", code=code,
                               login_url="/auth/login", api_base=_api_base())
    rec, error = None, None
    if code:
        _, rec = _lookup_user_code(code)
        if not rec:
            error = "This code is not valid or has expired. Run the sign-in again in Xela Code."
        elif rec.get("status") != "pending":
            error, rec = "This code was already used.", None
    return render_template("cli_auth.html", state="confirm", code=code, rec=rec, error=error,
                           user=session["user"], api_base=_api_base())


@cli_bp.before_app_request
def _resume_cli_auth_after_login():
    """The login page has no `next=` support: a user who arrived from /cli/auth, signed in and
    landed on the home page is sent back to the authorization page once."""
    code = session.get("cli_pending_code")
    if code and "user" in session and request.method == "GET" and request.endpoint == "pages.index":
        session.pop("cli_pending_code", None)
        session.modified = True
        return redirect(f"/cli/auth?code={code}")
    return None


@cli_bp.route("/cli/auth", methods=["POST"])
@limiter.limit("10 per minute")
def authorize_submit():
    if "user" not in session:
        return redirect("/cli/auth")
    user = session["user"]
    code = _normalize_user_code(request.form.get("code", ""))
    action = request.form.get("action", "approve")
    device_code, rec = _lookup_user_code(code)
    if not rec or rec.get("status") != "pending":
        return render_template("cli_auth.html", state="confirm", code=code, rec=None, user=user,
                               api_base=_api_base(),
                               error="This code is not valid or has expired. Run the sign-in again in Xela Code.")
    r = get_redis()
    if action == "deny":
        rec["status"] = "denied"
        _save_device(device_code, rec, APPROVED_TTL)
        r.delete(_user_code_key(code))
        return render_template("cli_auth.html", state="denied", code=code, user=user, api_base=_api_base())

    api_key = "xsk_" + secrets.token_urlsafe(36)
    doc = {
        "uid": user.get("uid"),
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "client": rec.get("client"),
        "hostname": rec.get("hostname"),
        "created_at": _now_iso(),
        "last_used_at": None,
        "revoked": False,
        "key_prefix": api_key[:12],
    }
    db.collection(KEYS_COLLECTION).document(_hash_key(api_key)).set(doc)
    rec.update({"status": "approved", "api_key": api_key, "email": doc["email"],
                "name": doc["name"], "approved_at": _now_iso()})
    _save_device(device_code, rec, APPROVED_TTL)
    r.delete(_user_code_key(code))
    logger.info("cli: issued key %s to %s for %s@%s", doc["key_prefix"], doc["email"],
                rec.get("client"), rec.get("hostname"))
    return render_template("cli_auth.html", state="done", code=code, rec=rec, user=user, api_base=_api_base())

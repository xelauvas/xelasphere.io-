"""Signed-link infrastructure for reminders.

Tokens carry {rid, uid} and expire after 24 hours. Used by the email
snooze flow: each snooze link in the email body is signed at send-time
with a token, validated on click, no session required.

Two domain exceptions are raised so routes can branch HTTP statuses
cleanly. itsdangerous's SignatureExpired / BadSignature are caught at
this boundary and re-raised as the domain types."""
from __future__ import annotations

from typing import Any

from itsdangerous import URLSafeTimedSerializer
from itsdangerous.exc import SignatureExpired, BadSignature

from app.config import Config

_SALT = "reminder-snooze"
_MAX_AGE_SECONDS = 24 * 3600  # 24 hours


class SnoozeTokenExpired(Exception):
    """Token is well-formed and was signed by us, but is older than 24h."""


class SnoozeTokenInvalid(Exception):
    """Token is malformed, tampered, or signed by a different secret."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(Config.SECRET_KEY, salt=_SALT)


def make_snooze_token(rid: str, uid: str) -> str:
    """Sign a {rid, uid} payload and return the URL-safe token string.

    Caller is responsible for including the token in a snooze URL like
    /reminders/{rid}/snooze?dur=10m&token={token}."""
    return _serializer().dumps({"rid": rid, "uid": uid})


def verify_snooze_token(token: str) -> dict[str, Any]:
    """Verify a token and return its payload {rid, uid}.

    Raises SnoozeTokenExpired if the token is older than 24h.
    Raises SnoozeTokenInvalid for any other failure (malformed, tampered,
    wrong signature, empty input)."""
    if not token:
        raise SnoozeTokenInvalid("empty token")
    try:
        payload = _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except SignatureExpired as e:
        raise SnoozeTokenExpired(str(e)) from e
    except BadSignature as e:
        raise SnoozeTokenInvalid(str(e)) from e
    except Exception as e:
        raise SnoozeTokenInvalid(str(e)) from e
    if not isinstance(payload, dict) or "rid" not in payload or "uid" not in payload:
        raise SnoozeTokenInvalid("payload missing rid/uid")
    return payload

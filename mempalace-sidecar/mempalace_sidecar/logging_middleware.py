"""Structured-logging middleware (M0.11, spec §8.4).

Emits one JSON log record per non-exempt HTTP request with the
fixed schema:

    {ts, service, level, event, user_id, turn_id, route,
     latency_ms, err, request_id}

Where:
  - ``ts`` — ISO-8601 UTC timestamp at request-complete time.
  - ``service`` — ``"mempalace-sidecar"``.
  - ``level`` — ``"INFO"`` on 2xx/3xx, ``"WARNING"`` on 4xx,
    ``"ERROR"`` on 5xx or exception propagation.
  - ``event`` — stable name (e.g. ``"http_request"``).
  - ``user_id`` — **always** ``hash_uid(user_id, salt)``, never raw.
  - ``turn_id`` — from request body if present (ingest / wake-up
    routes), else ``None``.
  - ``route`` — FastAPI-style route template (``/palace/{uid}/ingest``)
    when resolvable, else raw path.
  - ``latency_ms`` — float wall-clock of the handler.
  - ``err`` — exception class name if handler raised, else ``None``.
  - ``request_id`` — propagated from ``X-Request-ID`` header if the
    caller sent one (future Flask-side adapter will), else a fresh
    ``uuid4().hex``. Set on ``request.state.request_id`` so route
    handlers can include it in their own log records.

**Exempt paths** (``/healthz``, ``/readyz``, ``/metrics``) bypass the
middleware entirely — health-check and scrape traffic would spam
journald and inflate ``latency_ms`` histograms we don't care about
for those surfaces.

**Why a middleware + not per-handler decorators?** Middleware
captures the latency bracket and error state uniformly across
routes; a decorator per handler would drift across 7+ route
functions. Uniform surface = auditable schema compliance.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from mempalace_sidecar.logging_utils import hash_uid


log = logging.getLogger(__name__)


EXEMPT_PATHS = ("/healthz", "/readyz", "/metrics")

_SERVICE = "mempalace-sidecar"


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS)


def _extract_user_id(path: str) -> str | None:
    """Pull ``user_id`` out of the URL path when the route template
    is ``/palace/{user_id}/...``.

    Route-level dependencies already parse this, but the middleware
    runs BEFORE route resolution — we read the path directly. A
    fancier approach (resolve via ``request.scope['route']``) depends
    on middleware-ordering assumptions the middleware itself
    shouldn't make.

    The raw path segment is URL-decoded via ``urllib.parse.unquote``
    so middleware-computed hashes match the Starlette-decoded
    user_id the route handler receives. Without the unquote, a
    user_id containing ``%20`` or ``%2F`` would hash differently
    at middleware-layer vs route-handler-layer and break
    cross-stream log correlation (spec §8.4 intent is one hash
    per request).
    """
    parts = path.split("/")
    # Expect ['', 'palace', '<uid>', ...]; tolerate anything else.
    if len(parts) >= 3 and parts[1] == "palace" and parts[2]:
        return urllib.parse.unquote(parts[2])
    return None


def _normalize_route(path: str) -> str:
    """Return the FastAPI-style route template (``/palace/{user_id}/...``)
    for log records per spec §8.4.

    Critical: raw user_id MUST NOT appear in the ``route`` field. The
    ``user_id`` field carries the hashed form; leaving the raw
    segment in ``route`` would leak it through a sibling field.
    """
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "palace" and parts[2]:
        parts[2] = "{user_id}"
    return "/".join(parts)


def _parse_turn_id(body_bytes: bytes) -> str | None:
    """Extract ``turn_id`` from a JSON body without re-reading the
    stream. Returns None on any failure — the logging middleware
    MUST NOT fail a request because the body was empty or malformed.
    """
    if not body_bytes:
        return None
    try:
        data = json.loads(body_bytes)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(data, dict):
        tid = data.get("turn_id")
        if isinstance(tid, str):
            return tid
    return None


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Emits one §8.4-schema JSON log record per non-exempt request.

    Schema fields (all ten, every record): ``ts, service, level,
    event, user_id, turn_id, route, latency_ms, err, request_id``.
    ``user_id`` is always hashed via ``hash_uid(user_id, salt)`` —
    raw user_id never lands in journald.

    Record shape is emitted as the log message itself (a single JSON
    string at INFO level). An operator's ``journalctl -u
    mempalace-sidecar`` can grep across request streams by
    ``request_id`` without a JSON parser in the terminal.
    """

    def __init__(self, app, *, salt: str) -> None:
        super().__init__(app)
        self._salt = salt

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_exempt(request.url.path):
            return await call_next(request)

        # request_id — propagate from header if the caller sent one
        # (future Flask-side adapter will); else generate one here.
        # Sidecar is the convention-setter for this header; any
        # downstream Flask adapter reads / writes ``X-Request-ID``.
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id

        path = request.url.path
        raw_uid = _extract_user_id(path)
        uid_hash = hash_uid(raw_uid, self._salt) if raw_uid else None

        # Read body once so we can surface ``turn_id`` without
        # breaking the downstream handler (Starlette caches the body
        # after the first read; we re-inject via a patched receive
        # so call_next's handler still sees it).
        body = await request.body()
        turn_id = _parse_turn_id(body)

        async def _replay_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _replay_receive  # type: ignore[attr-defined]

        t0 = time.monotonic()
        err_name: str | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            err_name = type(exc).__name__
            raise
        finally:
            latency_ms = (time.monotonic() - t0) * 1000.0
            # Level reflects outcome: 2xx/3xx → INFO, 4xx → WARNING,
            # 5xx or exception → ERROR. Operator alert rules can gate
            # on the level field without parsing status_code.
            if err_name is not None or status_code >= 500:
                level = "ERROR"
            elif status_code >= 400:
                level = "WARNING"
            else:
                level = "INFO"
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "service": _SERVICE,
                "level": level,
                "event": "http_request",
                "user_id": uid_hash,
                "turn_id": turn_id,
                "route": _normalize_route(path),
                "latency_ms": round(latency_ms, 3),
                "err": err_name,
                "request_id": request_id,
            }
            log.info(json.dumps(record))


__all__ = ["StructuredLoggingMiddleware"]

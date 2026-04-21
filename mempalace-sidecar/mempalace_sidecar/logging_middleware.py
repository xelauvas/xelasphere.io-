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

from typing import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


EXEMPT_PATHS = ("/healthz", "/readyz", "/metrics")


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS)


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Emits one §8.4-schema JSON log record per non-exempt request.

    Stage 1 RED HALF-IMPLEMENTATION: pass-through. No log record is
    emitted. ``test_structured_log_schema`` fails at "expected 1 log
    record, got 0." Stage 3 GREEN lands the full record-emission.
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
        # Stage 1 stub — pass-through without emitting a record.
        return await call_next(request)


__all__ = ["StructuredLoggingMiddleware"]

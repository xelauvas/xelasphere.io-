from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

EXEMPT_PREFIXES = ("/healthz", "/readyz", "/metrics")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, expected_token: str):
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)
        token = request.headers.get("X-Sidecar-Token")
        if not token or token != self._expected:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

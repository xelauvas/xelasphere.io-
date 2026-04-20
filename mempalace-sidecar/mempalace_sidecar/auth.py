import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

EXEMPT_PATHS = ("/healthz", "/readyz", "/metrics")


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, expected_token: str):
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)
        token = request.headers.get("X-Sidecar-Token")
        if not token or not hmac.compare_digest(token, self._expected):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

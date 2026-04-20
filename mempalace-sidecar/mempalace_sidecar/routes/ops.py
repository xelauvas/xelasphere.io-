import time
from fastapi import APIRouter
from mempalace_sidecar.version import __version__

router = APIRouter(tags=["ops"])
_started_at = time.monotonic()

SCHEMA_VERSION = 1  # bumped whenever the OpenAPI schema changes incompatibly

@router.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "uptime_s": time.monotonic() - _started_at,
        "schema_version": SCHEMA_VERSION,
    }

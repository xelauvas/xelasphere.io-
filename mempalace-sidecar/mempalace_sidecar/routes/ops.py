import time
from fastapi import APIRouter
from mempalace_sidecar.version import __version__

router = APIRouter(tags=["ops"])
_started_at = time.monotonic()

SCHEMA_VERSION = 2  # bumped whenever the OpenAPI schema changes incompatibly
# v1 → v2 (2026-04-20, M0.4.2): added POST /palace/{user_id}/ingest. The
# OpenAPI snapshot test in M0.12 will capture v2 as its first baseline.

@router.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "uptime_s": time.monotonic() - _started_at,
        "schema_version": SCHEMA_VERSION,
    }

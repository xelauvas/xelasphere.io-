import time
from fastapi import APIRouter, Request
from fastapi.responses import Response
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


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint (M0.11, spec §8.1).

    Stage 1 RED stub — returns 501. Stage 3 GREEN renders via
    ``mempalace_sidecar.metrics.render_metrics``.
    """
    return Response(
        content=b"metrics endpoint not yet implemented",
        status_code=501,
        media_type="text/plain",
    )


@router.get("/readyz")
async def readyz(request: Request) -> Response:
    """Readiness gate (M0.11, spec §8.1 alert row ``MempalaceReadyzFailing``).

    Stage 1 RED stub — returns 501. Stage 3 GREEN returns JSON with
    ``{ok, embedding_loaded, db_ok, audit_ok, schema_version}``,
    gated on ``app.state.embedding_ready``, ``app.state.registry``,
    and NOT (``app.state.accepting == False`` or
    ``app.state._shutdown_done``).
    """
    return Response(
        content=b'{"detail":"readyz not yet implemented"}',
        status_code=501,
        media_type="application/json",
    )

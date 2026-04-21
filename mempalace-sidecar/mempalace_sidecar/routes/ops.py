import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from mempalace_sidecar.metrics import readyz_gauge, render_metrics
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
    """Prometheus scrape endpoint (spec §8.1).

    Multiproc-aware: ``render_metrics`` branches on
    ``PROMETHEUS_MULTIPROC_DIR`` — production (env set) aggregates
    across uvicorn workers via ``MultiProcessCollector``; dev / unit
    tests (env unset) fall back to the default registry with a
    one-shot WARNING.
    """
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness gate (spec §8.1 alert ``MempalaceReadyzFailing``).

    Returns ``ok=True`` iff:
      - ``app.state.embedding_ready`` is True (lifespan step 1
        completed the ``prime_embedding_function`` warm-up).
      - ``app.state.registry`` is attached (lifespan step 2).
      - ``app.state.audit`` is attached (lifespan step 2b).
      - ``app.state.accepting`` is True (M0.10 shutdown gate not
        flipped).
      - ``app.state._shutdown_done`` is not True (shutdown_sequence
        has not run yet).

    The shutdown-gate checks deviate from the plan's ``shutting_down``
    attribute — plan predates M0.10; this route uses the actual
    attributes M0.10 ships (see deltas 2026-04-21 M0.11 deviation #1).

    ``readyz_gauge`` is set to 1/0 so the
    ``MempalaceReadyzFailing`` alert fires on the scrape output
    even during the window between a degradation and the next
    ``/readyz`` probe.
    """
    app = request.app
    ok_embed = bool(getattr(app.state, "embedding_ready", False))
    ok_registry = getattr(app.state, "registry", None) is not None
    ok_audit = getattr(app.state, "audit", None) is not None
    accepting = bool(getattr(app.state, "accepting", True))
    shutdown_done = bool(getattr(app.state, "_shutdown_done", False))
    ok = ok_embed and ok_registry and ok_audit and accepting and not shutdown_done

    readyz_gauge.set(1 if ok else 0)

    return JSONResponse(
        {
            "ok": ok,
            "embedding_loaded": ok_embed,
            "db_ok": ok_registry,
            "audit_ok": ok_audit,
            "schema_version": SCHEMA_VERSION,
        }
    )

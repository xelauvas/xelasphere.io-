from contextlib import asynccontextmanager

from fastapi import FastAPI
from mempalace_sidecar.auth import TokenAuthMiddleware
from mempalace_sidecar.config import Settings
from mempalace_sidecar.routes import ops
from mempalace_sidecar.routes import palace as palace_router


def _sd_notify(message: str) -> None:
    try:
        import systemd.daemon as sd  # type: ignore
    except ImportError:
        return
    try:
        sd.notify(message)
    except OSError:  # NOTIFY_SOCKET missing or dead
        pass


def _sd_notify_ready() -> None:
    _sd_notify("READY=1")


def _sd_notify_stopping() -> None:
    _sd_notify("STOPPING=1")


@asynccontextmanager
async def _lifespan(app):
    """App startup/shutdown — construct PalaceRegistry, drain on exit.

    Plan M0.4.2 Step 4: the registry lives for the lifetime of the
    process and is attached to ``app.state.registry`` so every route
    can pull it off ``request.app.state``. Embedding model pre-warm
    slots in here during M0.5 (single process-global
    sentence-transformers load, ~2-5 s cold) — spec §5.3
    "Embedding-model scope".

    Shutdown: ``registry.close_all()`` runs BEFORE ``STOPPING=1`` so
    systemd sees STOPPING only after every resident palace has
    flushed sqlite / popped its Chroma backend client. Two-phase
    eviction inside close_all handles any in-flight readers (spec
    §5.4).
    """
    # Deferred import: spec §5.4 and registry.py's module docstring both
    # call out that PalaceRegistry.__init__ creates the palaces dir and
    # opens no handles, so importing it at module top would be fine —
    # but keeping the import local mirrors the plan Step 4 pseudo-code
    # exactly and makes the lifespan's contract visually self-contained.
    from mempalace_sidecar.registry import PalaceRegistry

    settings: Settings = app.state.settings
    app.state.registry = PalaceRegistry(
        data_dir=settings.data_dir,
        max_bytes=settings.cache_max_bytes,
        idle_ttl_sec=settings.idle_ttl_sec,
    )
    _sd_notify_ready()
    try:
        yield
    finally:
        # Drain resident palaces before signalling STOPPING. If
        # close_all raises mid-loop, the registry logs + swallows the
        # per-handle error (see registry._close_handle_safely) — the
        # outer call still returns cleanly.
        try:
            await app.state.registry.close_all()
        finally:
            _sd_notify_stopping()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="mempalace-sidecar", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    app.add_middleware(TokenAuthMiddleware, expected_token=settings.token)
    app.include_router(ops.router)
    app.include_router(palace_router.router)
    return app


def run() -> None:  # entrypoint referenced by pyproject scripts
    import uvicorn
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        uds=settings.socket_path,
        log_level=settings.log_level.lower(),
    )

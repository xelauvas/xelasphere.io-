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
    """App startup/shutdown — pre-warm embeddings, construct
    PalaceRegistry, drain on exit.

    Startup order (spec §5.3 "Embedding-model scope — process-global,
    warmed once"):

    1. ``prime_embedding_function`` forces ChromaDB's default
       sentence-transformers model to resolve + load before any
       real wake-up can hit /wake-up. ~2-5 s cold; shifts the
       cost off the first user turn. Runs BEFORE registry
       construction so ``app.state.embedding_ready = True`` can
       gate /readyz (M0.11).

    2. ``PalaceRegistry`` is attached to ``app.state.registry`` for
       route handlers to pull via ``request.app.state``.

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
    from mempalace_sidecar.warmup import prime_embedding_function

    settings: Settings = app.state.settings

    # Step 1: embedding pre-warm. We do this in a worker thread so the
    # event-loop startup stays responsive — a 2-5s synchronous call in
    # the lifespan would block every other startup hook. Every
    # subsequent /wake-up hits a hot embedding model.
    import asyncio as _asyncio

    await _asyncio.to_thread(
        prime_embedding_function, settings.data_dir / "_warmup"
    )
    app.state.embedding_ready = True

    # Step 2: registry.
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

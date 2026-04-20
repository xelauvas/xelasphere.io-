from contextlib import asynccontextmanager

from fastapi import FastAPI
from mempalace_sidecar.auth import TokenAuthMiddleware
from mempalace_sidecar.config import Settings
from mempalace_sidecar.routes import ops


def _sd_notify_ready() -> None:
    try:
        import systemd.daemon as sd  # type: ignore
        sd.notify("READY=1")
    except Exception:  # systemd not available (tests, dev)
        pass


def _sd_notify_stopping() -> None:
    try:
        import systemd.daemon as sd  # type: ignore
        sd.notify("STOPPING=1")
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(app):
    # Embedding model warmup goes here once registry is wired (M0.5).
    _sd_notify_ready()
    try:
        yield
    finally:
        _sd_notify_stopping()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="mempalace-sidecar", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    app.add_middleware(TokenAuthMiddleware, expected_token=settings.token)
    app.include_router(ops.router)
    return app


def run() -> None:  # entrypoint referenced by pyproject scripts
    import uvicorn
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        uds=settings.socket_path,
        log_level=settings.log_level.lower(),
    )

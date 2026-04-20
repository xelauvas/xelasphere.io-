from contextlib import asynccontextmanager

from fastapi import FastAPI
from mempalace_sidecar.auth import TokenAuthMiddleware
from mempalace_sidecar.config import Settings
from mempalace_sidecar.routes import ops


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

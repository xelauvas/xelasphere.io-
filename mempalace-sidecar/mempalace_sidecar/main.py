from fastapi import FastAPI
from mempalace_sidecar.auth import TokenAuthMiddleware
from mempalace_sidecar.config import Settings
from mempalace_sidecar.routes import ops


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="mempalace-sidecar", version="0.1.0")
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

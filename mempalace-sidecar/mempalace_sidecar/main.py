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
    PalaceRegistry, launch KG worker, drain on exit.

    Startup order (spec §5.3 + §5.5):

    1. ``prime_embedding_function`` forces ChromaDB's default
       sentence-transformers model to resolve + load before any
       real wake-up can hit /wake-up. ~2-5 s cold; shifts the
       cost off the first user turn. Runs BEFORE registry
       construction so ``app.state.embedding_ready = True`` can
       gate /readyz (M0.11).

    2. ``PalaceRegistry`` is attached to ``app.state.registry``
       for route handlers to pull via ``request.app.state``.

    3. ``KGQueue`` is initialised at
       ``settings.data_dir / "kg_pending.sqlite"`` (M0.7,
       deviation #4). ``run_kg_worker`` starts as a background
       task on ``app.state.kg_worker_task``. Extractor is
       ``NullExtractor`` for now (deviation #2: real LLM provider
       deferred; wiring driven by ``settings.kg_llm_provider``
       lands in a later milestone).

    Shutdown order (spec §5.7):

    1. Cancel the KG worker task and await its completion. This
       MUST run BEFORE ``registry.close_all`` — the worker holds
       palace handles via ``registry.open``, and closing the
       registry first would yank handles out from under an
       in-flight triple write. Integration test
       ``test_worker_lifecycle_cancels_cleanly_on_shutdown``
       enforces this ordering (deviation #3).

    2. ``registry.close_all`` drains resident palaces (two-phase
       eviction per §5.4).

    3. ``STOPPING=1`` notifies systemd. systemd sees STOPPING only
       after every resident palace has flushed sqlite / popped its
       Chroma backend client.
    """
    # Deferred imports: spec §5.4 and registry.py's module docstring
    # both call out that PalaceRegistry.__init__ creates the palaces
    # dir and opens no handles, so importing it at module top would
    # be fine — but keeping the imports local mirrors the plan
    # pseudo-code and makes the lifespan's contract visually
    # self-contained.
    from mempalace_sidecar.kg_queue import KGQueue
    from mempalace_sidecar.kg_worker import NullExtractor, run_kg_worker
    from mempalace_sidecar.registry import PalaceRegistry
    from mempalace_sidecar.warmup import prime_embedding_function

    settings: Settings = app.state.settings

    # Step 1: embedding pre-warm. We do this in a worker thread so
    # the event-loop startup stays responsive — a 2-5s synchronous
    # call in the lifespan would block every other startup hook.
    # Every subsequent /wake-up hits a hot embedding model.
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

    # Step 3: KG queue + worker (plan M0.7).
    # TODO(M0.16-ish): swap NullExtractor for a real LLM-backed
    # extractor driven by ``settings.kg_llm_provider`` (ollama /
    # groq / anthropic). The worker body and queue schema are
    # provider-agnostic; plugging a real extractor in is a
    # drop-in replacement per the KGExtractor Protocol.
    kg_queue = KGQueue(settings.data_dir / "kg_pending.sqlite")
    await kg_queue.ensure()
    app.state.kg_queue = kg_queue
    app.state.kg_worker_task = _asyncio.create_task(
        run_kg_worker(
            queue=kg_queue,
            registry=app.state.registry,
            extractor=NullExtractor(),
            cap_usd=settings.kg_daily_usd_cap,
        )
    )

    _sd_notify_ready()
    try:
        yield
    finally:
        # Shutdown deviation #3 — worker cancel BEFORE close_all.
        # Reversing this order would close palace handles while
        # the worker is mid-write, corrupting per-palace kg.sqlite
        # transactions.
        worker_task = app.state.kg_worker_task
        if not worker_task.done():
            worker_task.cancel()
        try:
            # Budget 10s per spec §5.7 "wait up to 10s for
            # in-flight handlers." If the worker is stuck (e.g.,
            # a misbehaving extractor swallowing CancelledError),
            # wait_for will re-raise TimeoutError and we proceed to
            # close_all anyway — better to leave the worker task
            # hanging than to hang shutdown forever.
            await _asyncio.wait_for(worker_task, timeout=10.0)
        except (_asyncio.CancelledError, _asyncio.TimeoutError):
            pass
        except Exception:
            # Any non-cancellation exception from the worker task
            # is logged and swallowed — shutdown continues.
            pass

        # Now drain resident palaces. If close_all raises mid-loop,
        # the registry logs + swallows the per-handle error (see
        # registry._close_handle_safely) — the outer call still
        # returns cleanly.
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

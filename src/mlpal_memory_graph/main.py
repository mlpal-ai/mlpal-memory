"""FastAPI application factory (mirrors the platform create_app() convention)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api.v1 import documents, episodes, governance, memory, ontology, ops, telemetry
from .core.config import get_settings
from .core.logging import get_logger, setup_logging
from .db import get_engine
from .db.models import Base

log = get_logger(__name__)


def _run_alembic_upgrade() -> None:
    """Best-effort `alembic upgrade head` (locate alembic.ini up the tree)."""
    from alembic.config import Config

    from alembic import command

    for parent in Path(__file__).resolve().parents:
        ini = parent / "alembic.ini"
        if ini.exists():
            cfg = Config(str(ini))
            cfg.set_main_option("sqlalchemy.url", get_settings().database_url_sync)
            command.upgrade(cfg, "head")
            return


async def _init_db() -> None:
    settings = get_settings()
    if settings.is_postgres:
        # migrate on boot, in-process — same pattern as storage/skills/mcp services.
        try:
            await asyncio.to_thread(_run_alembic_upgrade)
            log.info("db.migrated")
        except Exception as exc:  # noqa: BLE001
            log.error("db.migrate_failed", error=str(exc))
    else:
        # local sqlite/dev: create tables directly
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("db.created_all")


async def _warm_read_path() -> None:
    """Warm everything the first read would otherwise pay for, off the request
    path: the lazy ONNX embedder (measured 3–19s), then one throwaway retrieval
    against an empty tenant to prime the DB pool, statement compiles, and query
    plans (a further ~1.1s measured after embedder-only warmup). Background
    task: readiness is not delayed; failures are logged, never fatal."""
    try:
        from .services.embeddings_client import get_embedder

        await get_embedder().embed_one("warmup")
        get_logger(__name__).info("embedder.warm")
        from .api.deps import AuthIdentity, get_retrieval
        from .api.v1.memory import _context
        from .db import get_session_factory

        ident = AuthIdentity(
            user_id="warmup", org_id="warmup-nonexistent-tenant", permissions=[]
        )
        async with get_session_factory()() as session:
            await get_retrieval().resolve(
                session, _context(ident), query="warmup", limit=1, expand=False
            )
        get_logger(__name__).info("read_path.warm")
    except Exception as exc:  # noqa: BLE001 — warmup is best-effort, never fatal
        get_logger(__name__).warning("warmup_failed", error=str(exc)[:200])


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await _init_db()
    warmup = asyncio.create_task(_warm_read_path())
    worker = None
    if settings.updater_enabled:
        from .services.worker import MemoryUpdateWorker

        worker = MemoryUpdateWorker()
        await worker.start()
    try:
        yield
    finally:
        warmup.cancel()
        if worker is not None:
            await worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    # Fail-fast guard: refuse to build an app that would serve with dev auth, default
    # keys, or a missing auth SDK outside local/test. Crashing at startup is visible
    # (CrashLoopBackOff + log line); failing open is not.
    from .api.deps import _HAS_MLPAL_AUTH

    config_errors = settings.production_config_errors(has_auth_sdk=_HAS_MLPAL_AUTH)
    if config_errors:
        for err in config_errors:
            log.error("config.fatal", error=err, environment=settings.environment)
        raise RuntimeError(f"unsafe production configuration: {'; '.join(config_errors)}")

    app = FastAPI(
        title="mlpal-memory-graph",
        description="Institutional memory graph for enterprises using MLPal.",
        version=__version__,
        lifespan=lifespan,
    )
    # Bearer/header auth only — no cookies, so credentials mode stays off (wildcard
    # origins + credentials is an invalid CORS combination anyway).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        log.error("unhandled_error", error=str(exc), exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "service": "mlpal-memory-graph", "version": __version__}

    app.include_router(episodes.router, prefix="/api/v1")
    app.include_router(memory.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(governance.router, prefix="/api/v1")
    app.include_router(ontology.router, prefix="/api/v1")
    app.include_router(ops.router, prefix="/api/v1")
    app.include_router(telemetry.router, prefix="/api/v1")

    # Memory explorer UI (static, read-only; ships with the service). /ui → index.html.
    # Prefer the built Vite app (ui-app/dist); fall back to the legacy static ui/ so
    # nothing breaks before the first `npm run build`.
    ui_dir = next(
        (
            d
            for p in Path(__file__).resolve().parents
            for d in (p / "ui-app" / "dist", p / "ui")
            if (d / "index.html").exists()
        ),
        None,
    )
    if ui_dir is not None:
        from fastapi.staticfiles import StaticFiles

        class SPAStaticFiles(StaticFiles):
            """Unknown /ui/<path> serves index.html (the app hash-routes) instead of a
            bare 404 — a pasted deep link should land in the app, not on an error."""

            async def get_response(self, path: str, scope):
                from starlette.exceptions import HTTPException as _StarletteHTTPException

                try:
                    response = await super().get_response(path, scope)
                except _StarletteHTTPException as exc:  # starlette RAISES 404s
                    if exc.status_code != 404:
                        raise
                    return await super().get_response("index.html", scope)
                if response.status_code == 404:
                    response = await super().get_response("index.html", scope)
                return response

        app.mount("/ui", SPAStaticFiles(directory=str(ui_dir), html=True), name="ui")

        favicon = ui_dir / "favicon.svg"
        if not favicon.exists():
            favicon = ui_dir / "favicon.ico"
        if favicon.exists():
            from fastapi.responses import FileResponse

            @app.get("/favicon.ico", include_in_schema=False)
            async def _favicon():  # browsers request it at the root regardless of mounts
                return FileResponse(str(favicon))
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("mlpal_memory_graph.main:app", host=s.host, port=s.port, reload=s.debug)

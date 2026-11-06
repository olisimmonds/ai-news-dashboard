"""FastAPI application factory and static frontend serving."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import register_exception_handlers, router
from .browser.api import register_browser_exception_handlers
from .browser.api import router as browser_router
from .browser.auth import BrowserAuth
from .browser.sessions import BrowserRuntime, BrowserSessionManager, DockerRuntime
from .browser.store import BrowserStore
from .config import Settings, get_settings
from .db import initialise
from .ingestion import IngestionService, RefreshScheduler
from .repository import Repository
from .sources import SourceConfig, load_sources
from .weather import WeatherService

logger = logging.getLogger(__name__)


def _spa_response(static_dir: Path, requested: str) -> Response:
    index = static_dir / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<html><body style='font-family: system-ui; padding: 3rem; max-width: 40rem'>"
            "<h1>Meridian backend is running</h1>"
            "<p>The frontend has not been built yet. Run <code>npm run build</code> in the "
            "<code>frontend</code> directory, then restart the server.</p>"
            "<p>API documentation is available at <a href='/docs'>/docs</a>.</p>"
            "</body></html>",
            status_code=200,
        )

    if requested:
        candidate = (static_dir / requested).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            candidate = index
        if candidate.is_file():
            return FileResponse(candidate)
    return FileResponse(index)


def create_app(
    settings: Settings | None = None,
    sources: Iterable[SourceConfig] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    weather_transport: httpx.AsyncBaseTransport | None = None,
    browser_runtime: BrowserRuntime | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    all_sources = list(sources) if sources is not None else load_sources(settings.sources_file)
    repository = Repository(settings.database_path)
    ingestion = IngestionService(repository, settings, all_sources, transport=transport)
    scheduler = RefreshScheduler(ingestion, settings.refresh_interval_minutes)
    weather = WeatherService(settings, transport=weather_transport)
    browser_auth = BrowserAuth(settings)
    browser_store = BrowserStore(settings.browser_state_file)
    browser_manager = BrowserSessionManager(settings, browser_runtime or DockerRuntime(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_directories()
        initialise(settings.database_path)
        repository.sync_sources(all_sources)
        if not repository.is_empty():
            try:
                repository.rebuild_stories()
            except Exception:
                logger.exception("Could not rebuild story clusters at startup")
        logger.info(
            "Meridian ready with %s sources (scheduler %s)",
            len([source for source in all_sources if source.enabled]),
            "enabled" if settings.scheduler_enabled else "disabled",
        )
        if settings.scheduler_enabled:
            await scheduler.start()
        await browser_manager.start()
        try:
            yield
        finally:
            await browser_manager.stop()
            await scheduler.stop()
            pending = list(app.state.background_tasks)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await ingestion.close()

    app = FastAPI(
        title="Meridian API",
        version="1.0.0",
        description="Personal AI, markets and world news dashboard.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repository = repository
    app.state.ingestion = ingestion
    app.state.scheduler = scheduler
    app.state.weather = weather
    app.state.browser_auth = browser_auth
    app.state.browser_store = browser_store
    app.state.browser_manager = browser_manager
    app.state.background_tasks = set()

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    register_browser_exception_handlers(app)
    app.include_router(router)
    app.include_router(browser_router)

    static_dir = settings.static_dir
    if static_dir.exists() and (static_dir / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return _spa_response(static_dir, "")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        if full_path.startswith(("api/", "docs", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not found")
        return _spa_response(static_dir, full_path)

    return app


app = create_app()

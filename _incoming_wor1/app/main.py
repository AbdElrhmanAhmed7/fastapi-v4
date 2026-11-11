"""NOVA – Beyond the Mission | FastAPI application factory.

Run locally::

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

The factory owns process-wide singletons (settings, content, database, NASA
client, gameplay services) and exposes them through ``app.state`` so request
handlers stay thin. Everything is created in the lifespan, which means tests
can build isolated apps with their own temporary database.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.legacy import _LegacyConflict
from app.api.legacy import router as legacy_router
from app.api.v1 import api_router
from app.config import Settings, get_settings
from app.core.errors import NovaError
from app.core.logging import configure_logging, get_logger, request_id_var
from app.core.ratelimit import SlidingWindowRateLimiter
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from app.db.session import Database
from app.integrations.nasa_client import NasaClient
from app.missions.registry import GameContent, load_content
from app.services.game_service import GameService
from app.services.simulation_service import SimulationService

logger = get_logger("nova.app")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAINTENANCE_INTERVAL_SECONDS = 900.0

DESCRIPTION = """
**NOVA – Beyond the Mission** · *Explore. Experience. Execute.*

Authoritative gameplay backend for the NOVA 3D web game. The React Three Fiber
client owns movement, camera and animation; this service owns **mission rules,
resources, inventory, physics simulations, continuity and persistence**.

### What is modelled
* **8 stages / 15 missions** – Training Facility → Earth Analog → Mars Arrival →
  Crew Health → Flame in Freefall → Martian Guide → Lunar Landing → Earth Trends.
* **Move → interact → consequence** – every action validates its preconditions.
  Repairing a pressurised pipe fails, wastes a kit and costs water.
* **Mission Continuity System** – preparation quality, maintenance and resource
  discipline carry over into later stages as explainable modifiers.
* **Server-authoritative simulations** – lunar descent, microgravity fire, crew
  vitals and rover traverses are integrated on the server.
* **Scientific honesty** – every payload labels its provenance (`live`, `cache`,
  `bundled`, `simulated`) and educational simplifications are stated explicitly.

### Conventions
* All game state lives under `/api/v1/games/{game_id}`.
* Errors use one envelope: `{"error": {"code", "message", "details"}, "request_id"}`.
* Writes accept `Idempotency-Key`; enable `NOVA_AUTH_ENABLED` to require `X-Session-Token`.
* Legacy Prototype-01 URLs (`/games`, `/games/{id}/missions/water-leak`) still work.
"""

TAGS_METADATA = [
    {"name": "Meta", "description": "Liveness, readiness and version information."},
    {"name": "Content", "description": "Static catalogues: items, resources, mission blueprints, controls."},
    {"name": "Games", "description": "Session lifecycle, HUD ticks, events, continuity and reports."},
    {"name": "Missions", "description": "Mission lifecycle and world interactions (the E key)."},
    {"name": "Loadout", "description": "Resources, inventory (Tab), tools (F) and the EVA suit."},
    {"name": "Simulations", "description": "Server-authoritative physics: lander, fire, vitals, rover."},
    {"name": "Earth Trends", "description": "Earth Observation Station: change detection and reports."},
    {"name": "Science", "description": "Martian Guide: samples, spectrometry and the science database."},
    {"name": "NASA open data", "description": "Cached, provenance-labelled NASA open data."},
    {"name": "Stream", "description": "WebSocket tail of the authoritative event log."},
    {"name": "Legacy (Prototype-01 compatible)", "description": "Deprecated unversioned endpoints."},
]


# ---------------------------------------------------------------------------
# Error handling – one envelope for every failure
# ---------------------------------------------------------------------------
def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    payload["request_id"] = request_id_var.get()
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NovaError)
    async def _nova_error(_: Request, exc: NovaError) -> JSONResponse:
        body = exc.to_payload()["error"]
        return _error_response(
            exc.status_code, body["code"], body["message"], body.get("details")
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            422,
            "validation_error",
            "Request validation failed.",
            {"errors": [{k: v for k, v in err.items() if k != "ctx"} for err in exc.errors()]},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(
            exc.status_code,
            f"http_{exc.status_code}",
            detail,
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(_LegacyConflict)
    async def _legacy_conflict(_: Request, exc: _LegacyConflict) -> JSONResponse:
        # Prototype-01 clients parse `detail`, not the v1 envelope.
        return JSONResponse(status_code=409, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"path": request.url.path})
        return _error_response(500, "internal_error", "Unexpected server error.")


# ---------------------------------------------------------------------------
# Background maintenance
# ---------------------------------------------------------------------------
async def _maintenance_loop(games: GameService, limiter: Any) -> None:
    """Purge expired saves and idle rate-limit buckets. Never fatal."""
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
        try:
            removed = await games.purge_expired()
            if removed:
                logger.info("maintenance.purged_sessions", extra={"count": removed})
            if limiter is not None:
                limiter.prune()
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("maintenance.failed")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_app(
    settings: Settings | None = None,
    *,
    content: GameContent | None = None,
    nasa: NasaClient | None = None,
) -> FastAPI:
    """Build a fully wired application.

    ``settings``, ``content`` and ``nasa`` can be injected by tests; production
    uses the environment-driven defaults.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level, resolved.log_json)
        app.state.settings = resolved
        app.state.started_at = time.monotonic()

        game_content = content or load_content(resolved.content_dir)
        database = Database(resolved)
        await database.connect()

        nasa_client = nasa or NasaClient(
            resolved, fallback=game_content.read_json("nasa/fallback.json")
        )
        games = GameService(database, game_content, resolved, nasa=nasa_client)

        app.state.content = game_content
        app.state.database = database
        app.state.nasa = nasa_client
        app.state.games = games
        app.state.simulations = SimulationService(games)

        limiter = getattr(app.state, "rate_limiter", None)
        task = asyncio.create_task(_maintenance_loop(games, limiter))

        logger.info(
            "nova.started",
            extra={
                "version": __version__,
                "environment": resolved.environment,
                "missions": len(game_content.missions.missions),
                "auth_enabled": resolved.auth_enabled,
                "database": resolved.database_url.split("://")[0],
            },
        )
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await nasa_client.close()
            await database.disconnect()
            logger.info("nova.stopped")

    app = FastAPI(
        title="NOVA – Beyond the Mission API",
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "NOVA Team", "url": "https://github.com/AbdElrhmanAhmed7/FASTAPI_v3"},
        license_info={"name": "MIT"},
    )
    app.state.settings = resolved

    # Middleware: last added runs first (outermost).
    limiter = SlidingWindowRateLimiter(
        limit=resolved.rate_limit_requests,
        window_seconds=resolved.rate_limit_window_seconds,
    )
    app.state.rate_limiter = limiter
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=resolved.max_request_bytes)
    app.add_middleware(RateLimitMiddleware, settings=resolved, limiter=limiter)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_origin_regex=resolved.cors_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Session-Token",
            "X-API-Key",
            "X-Request-ID",
            "Idempotency-Key",
        ],
        expose_headers=["X-Request-ID", "X-Process-Time-ms", "X-RateLimit-Remaining"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(api_router, prefix=resolved.api_v1_prefix)
    app.include_router(legacy_router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        """Mission Control dev console (a static, dependency-free API client)."""
        page = STATIC_DIR / "index.html"
        if page.is_file():
            return FileResponse(page, media_type="text/html")
        return JSONResponse(
            {
                "service": "NOVA – Beyond the Mission API",
                "version": __version__,
                "docs": "/docs",
                "api": resolved.api_v1_prefix,
                "health": f"{resolved.api_v1_prefix}/health",
            }
        )

    @app.get("/health", include_in_schema=False)
    async def health_alias() -> dict[str, str]:
        """Unversioned alias kept for the prototype's health check."""
        return {"status": "ok", "service": "NOVA Gameplay Backend", "version": __version__}

    return app


app = create_app()

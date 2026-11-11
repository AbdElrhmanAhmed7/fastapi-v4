"""Liveness, readiness and build information."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response

from app import __version__
from app.core.logging import get_logger
from app.dependencies import ContentDep, GamesDep, NasaDep, SettingsDep
from app.domain.enums import SimulationKind
from app.schemas.common import HealthResponse, ReadinessResponse, VersionResponse

logger = get_logger("nova.meta")
router = APIRouter(tags=["Meta"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(request: Request, settings: SettingsDep) -> HealthResponse:
    """Cheap, dependency-free probe used by load balancers and the frontend."""
    started: float = getattr(request.app.state, "started_at", time.monotonic())
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - started, 2),
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(
    response: Response, games: GamesDep, content: ContentDep, nasa: NasaDep
) -> ReadinessResponse:
    """Deep check: database reachable and bundled content loaded."""
    database_ok = await games.db.healthcheck()
    missions_loaded = len(content.missions.missions)
    items_loaded = len(content.catalog.items)
    content_ok = missions_loaded > 0 and items_loaded > 0
    if not (database_ok and content_ok):
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if database_ok and content_ok else "degraded",
        database=database_ok,
        content=content_ok,
        missions_loaded=missions_loaded,
        items_loaded=items_loaded,
        nasa_cache=nasa.cache_stats(),
    )


@router.get("/version", response_model=VersionResponse, summary="Build and content versions")
async def version(settings: SettingsDep, content: ContentDep) -> VersionResponse:
    from app.domain.state import STATE_SCHEMA_VERSION

    stages = {definition.stage for definition in content.missions.missions.values()}
    return VersionResponse(
        name=settings.app_name,
        version=__version__,
        api_version="v1",
        state_schema_version=STATE_SCHEMA_VERSION,
        missions=len(content.missions.missions),
        stages=len(stages),
        simulations=[kind.value for kind in SimulationKind],
    )

"""NASA open data proxy (cached, timeout-bounded, always provenance-labelled).

These endpoints power the flavour screens of the Earth Observation Station and
the Mars briefing. Gameplay never depends on them being reachable.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, Query

from app.dependencies import NasaDep, SettingsDep

router = APIRouter(prefix="/nasa", tags=["NASA open data"])


@router.get("/status", summary="Integration status and cache statistics")
async def status(nasa: NasaDep, settings: SettingsDep) -> dict[str, Any]:
    return {
        "enabled": settings.nasa_enabled,
        "api_key_configured": settings.nasa_api_key not in {"", "DEMO_KEY"},
        "cache": nasa.cache_stats(),
        "note": "DEMO_KEY is heavily rate limited; set NOVA_NASA_API_KEY for live data.",
    }


@router.get("/apod", summary="Astronomy Picture of the Day")
async def apod(
    nasa: NasaDep,
    date: date_type | None = Query(default=None, description="YYYY-MM-DD, defaults to today."),
) -> dict[str, Any]:
    return await nasa.apod(date.isoformat() if date else None)


@router.get("/mars-photos", summary="Mars rover photography")
async def mars_photos(
    nasa: NasaDep,
    sol: int = Query(default=1000, ge=0, le=6000),
    camera: str | None = Query(default=None, max_length=8, examples=["FHAZ", "NAVCAM"]),
    page: int = Query(default=1, ge=1, le=50),
) -> dict[str, Any]:
    return await nasa.mars_photos(sol=sol, camera=camera, page=page)


@router.get("/donki", summary="Space weather events (DONKI)")
async def donki(
    nasa: NasaDep,
    start_date: date_type = Query(default_factory=lambda: date_type.today()),
    end_date: date_type = Query(default_factory=lambda: date_type.today()),
    kind: str = Query(default="GST", max_length=8),
) -> dict[str, Any]:
    return await nasa.donki(start_date=start_date.isoformat(), end_date=end_date.isoformat(), kind=kind)


@router.get("/neo", summary="Near-Earth object feed")
async def neo(
    nasa: NasaDep,
    start_date: date_type = Query(default_factory=lambda: date_type.today()),
    end_date: date_type = Query(default_factory=lambda: date_type.today()),
) -> dict[str, Any]:
    return await nasa.neo_feed(start_date=start_date.isoformat(), end_date=end_date.isoformat())


@router.get("/epic", summary="EPIC full-disk Earth imagery")
async def epic(nasa: NasaDep) -> dict[str, Any]:
    return await nasa.epic()

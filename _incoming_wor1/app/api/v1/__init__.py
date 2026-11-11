"""NOVA API v1 – routers aggregated into a single mountable router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    content,
    earth,
    games,
    loadout,
    meta,
    missions,
    nasa,
    science,
    simulations,
    stream,
)

api_router = APIRouter()
api_router.include_router(meta.router)
api_router.include_router(content.router)
api_router.include_router(games.router)
api_router.include_router(games.leaderboard_router)
api_router.include_router(missions.router)
api_router.include_router(loadout.router)
api_router.include_router(simulations.router)
api_router.include_router(simulations.catalogue_router)
api_router.include_router(earth.router)
api_router.include_router(science.router)
api_router.include_router(nasa.router)
api_router.include_router(stream.router)

__all__ = ["api_router"]

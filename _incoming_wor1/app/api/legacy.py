"""Prototype-01 compatibility surface (unversioned URLs).

The original in-memory prototype exposed ``POST /games`` and
``/games/{id}/missions/water-leak``. Those URLs keep working – now backed by the
persistent mission engine – so an older frontend build runs unchanged while the
team migrates to ``/api/v1``.

Deprecated: will be removed once the 3D client is fully on v1.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from app.core.errors import GameRuleError
from app.core.logging import get_logger
from app.dependencies import GamesDep
from app.domain.state import GameState
from app.schemas.legacy import (
    LegacyActionRequest,
    LegacyActionResponse,
    LegacyGameCreateRequest,
    LegacyGameState,
    LegacyMissionState,
)

logger = get_logger("nova.api.legacy")
router = APIRouter(tags=["Legacy (Prototype-01 compatible)"], deprecated=True)

LEGACY_MISSION_ID = "analog_water_leak"


def _mission_view(state: GameState) -> dict[str, Any]:
    progress = state.mission(LEGACY_MISSION_ID)
    world = state.world
    return {
        "id": "water_leak",
        "status": "completed" if progress.status.value == "completed" else "active",
        "current_objective": progress.current_objective,
        "leak_detected": bool(state.flags.get("leak.detected", False)),
        "valve_closed": bool(world.get("leak.valve_closed", False)),
        "pipe_repaired": bool(world.get("leak.repaired", False)),
        "leak_active": bool(world.get("leak_active", False)),
    }


def _state_view(state: GameState) -> dict[str, Any]:
    return {
        "game_id": state.game_id,
        "player_name": state.player_name,
        "resources": dict(state.resources),
        "inventory": dict(state.inventory),
        "missions": {"water_leak": _mission_view(state)},
    }


@router.post(
    "/games",
    response_model=LegacyGameState,
    status_code=status.HTTP_201_CREATED,
    summary="[Legacy] Create a prototype-style session",
)
async def create_game(payload: LegacyGameCreateRequest, games: GamesDep) -> dict[str, Any]:
    created = await games.create_legacy_game(payload.player_name)
    state = await games.read_state(created["game_id"])
    return _state_view(state)


@router.get("/games/{game_id}/state", response_model=LegacyGameState, summary="[Legacy] Game state")
async def get_state(game_id: str, games: GamesDep) -> dict[str, Any]:
    return _state_view(await games.read_state(game_id))


@router.get("/games/{game_id}/resources", response_model=dict[str, int], summary="[Legacy] Resources")
async def get_resources(game_id: str, games: GamesDep) -> dict[str, int]:
    return dict((await games.read_state(game_id)).resources)


@router.get("/games/{game_id}/inventory", response_model=dict[str, int], summary="[Legacy] Inventory")
async def get_inventory(game_id: str, games: GamesDep) -> dict[str, int]:
    return dict((await games.read_state(game_id)).inventory)


@router.get(
    "/games/{game_id}/missions/water-leak",
    response_model=LegacyMissionState,
    summary="[Legacy] Water-leak mission state",
)
async def get_mission(game_id: str, games: GamesDep) -> dict[str, Any]:
    return _mission_view(await games.read_state(game_id))


@router.post(
    "/games/{game_id}/missions/water-leak/actions",
    response_model=LegacyActionResponse,
    summary="[Legacy] Execute a water-leak action",
    responses={409: {"description": "Action conflicts with the mission state"}},
)
async def execute_action(
    game_id: str, payload: LegacyActionRequest, games: GamesDep
) -> dict[str, Any]:
    try:
        result = await games.execute_action(game_id, LEGACY_MISSION_ID, payload.action)
    except GameRuleError as exc:
        # The prototype answered with FastAPI's `detail` envelope; keep it.
        raise _LegacyConflict(str(exc)) from exc
    state = await games.read_state(game_id)
    mission = _mission_view(state)
    return {
        "success": True,
        "event": result["event"],
        "message": result["message"],
        "mission_status": mission["status"],
        "next_objective": mission["current_objective"],
        "resources": dict(state.resources),
        "inventory": dict(state.inventory),
        "mission": mission,
    }


class _LegacyConflict(Exception):
    """Internal marker converted to a 409 `detail` response by the app handler."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

"""Mission lifecycle and world interactions (the E key)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from app.core.logging import get_logger
from app.dependencies import GameId, GamesDep, IdempotencyHeader, MissionId, WriteGuard
from app.schemas.mission import (
    InteractionMap,
    MissionActionRequest,
    MissionActionResponse,
    MissionCatalogueEntry,
    MissionReport,
    MissionStartResponse,
    MissionView,
)

logger = get_logger("nova.api.missions")

router = APIRouter(prefix="/games/{game_id}/missions", tags=["Missions"])


@router.get(
    "",
    response_model=list[MissionCatalogueEntry],
    summary="Mission catalogue with this player's progress",
)
async def catalogue(game_id: GameId, games: GamesDep) -> list[dict[str, Any]]:
    return await games.mission_catalogue(game_id)


@router.get(
    "/{mission_id}",
    response_model=MissionView,
    summary="Mission state: objectives, availability and unmet preconditions",
)
async def mission(game_id: GameId, mission_id: MissionId, games: GamesDep) -> dict[str, Any]:
    return await games.mission_view(game_id, mission_id)


@router.post(
    "/{mission_id}/start",
    response_model=MissionStartResponse,
    summary="Enter a mission (applies pending continuity modifiers)",
    dependencies=[WriteGuard],
    responses={409: {"description": "Mission locked or already completed"}},
)
async def start(game_id: GameId, mission_id: MissionId, games: GamesDep) -> dict[str, Any]:
    return await games.start_mission(game_id, mission_id)


@router.post(
    "/{mission_id}/actions",
    response_model=MissionActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Perform one interaction (scan, close valve, repair, collect…)",
    dependencies=[WriteGuard],
    responses={
        409: {"description": "Precondition failed – wrong order, missing item or worn tool"},
        422: {"description": "Unknown action or invalid payload"},
    },
)
async def action(
    game_id: GameId,
    mission_id: MissionId,
    payload: MissionActionRequest,
    games: GamesDep,
    idempotency_key: IdempotencyHeader = None,
) -> dict[str, Any]:
    """The heart of NOVA: *move → interact → consequence*.

    The server validates every precondition. Repairing a pipe before closing
    the valve fails, wastes a repair kit and costs water – exactly like the
    real procedure. The response carries the animation to play, the resource
    delta and the refreshed mission state so the HUD needs no extra requests.
    """
    return await games.execute_action(
        game_id,
        mission_id,
        payload.action,
        payload=payload.payload,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/{mission_id}/interactions",
    response_model=InteractionMap,
    summary="World objects + currently possible interactions (for 3D prompts)",
)
async def interactions(
    game_id: GameId, mission_id: MissionId, games: GamesDep
) -> dict[str, Any]:
    """Positions, labels and live availability for every interactable object.

    The frontend places these in the scene and shows the "Press E" prompt only
    when ``available`` is true, with ``unmet`` explaining why it is not.
    """
    return await games.interaction_map(game_id, mission_id)


@router.get(
    "/{mission_id}/report",
    response_model=MissionReport,
    summary="Debrief for a completed mission",
)
async def report(game_id: GameId, mission_id: MissionId, games: GamesDep) -> dict[str, Any]:
    return await games.mission_report(game_id, mission_id)

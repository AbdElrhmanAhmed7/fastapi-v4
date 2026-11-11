from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_game_service
from app.schemas.game import MissionStateResponse
from app.schemas.mission import MissionActionRequest, MissionActionResponse
from app.services.game_service import GameService

router = APIRouter(
    prefix="/games/{game_id}/missions/water-leak",
    tags=["Missions"],
)


@router.get("", response_model=MissionStateResponse, summary="Get water-leak mission state")
def get_water_leak_mission(
    game_id: str,
    service: Annotated[GameService, Depends(get_game_service)],
) -> dict:
    session = service.get_session(game_id)
    return session.get_mission_state()


@router.post(
    "/actions",
    response_model=MissionActionResponse,
    summary="Execute a water-leak mission action",
    responses={409: {"description": "Action is not valid in the current mission state"}},
)
def execute_water_leak_action(
    game_id: str,
    payload: MissionActionRequest,
    service: Annotated[GameService, Depends(get_game_service)],
) -> dict:
    session = service.get_session(game_id)
    try:
        return session.execute_mission_action(payload.action)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

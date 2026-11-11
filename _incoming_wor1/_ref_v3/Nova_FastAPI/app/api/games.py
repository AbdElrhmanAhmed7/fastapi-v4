from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.dependencies import get_game_service
from app.schemas.game import GameCreateRequest, GameStateResponse
from app.services.game_service import GameService

router = APIRouter(prefix="/games", tags=["Games"])


@router.post(
    "",
    response_model=GameStateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new game session",
)
def create_game(
    payload: GameCreateRequest,
    service: Annotated[GameService, Depends(get_game_service)],
) -> dict:
    session = service.create_session(payload.player_name)
    return session.get_state()


@router.get(
    "/{game_id}/state",
    response_model=GameStateResponse,
    summary="Get the complete game state",
)
def get_game_state(
    game_id: str,
    service: Annotated[GameService, Depends(get_game_service)],
) -> dict:
    return service.get_state(game_id)

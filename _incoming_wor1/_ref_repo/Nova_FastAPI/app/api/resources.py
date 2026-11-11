from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_game_service
from app.services.game_service import GameService

router = APIRouter(prefix="/games/{game_id}/resources", tags=["Resources"])


@router.get("", response_model=dict[str, int], summary="Get player resources")
def get_resources(
    game_id: str,
    service: Annotated[GameService, Depends(get_game_service)],
) -> dict[str, int]:
    session = service.get_session(game_id)
    return session.get_resources()

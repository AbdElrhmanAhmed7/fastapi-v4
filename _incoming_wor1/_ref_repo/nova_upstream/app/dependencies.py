from fastapi import Request

from app.services.game_service import GameService


def get_game_service(request: Request) -> GameService:
    """Return the single GameService instance owned by this app."""
    return request.app.state.game_service

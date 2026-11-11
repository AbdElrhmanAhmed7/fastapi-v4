from threading import RLock
from uuid import uuid4

from app.services.exceptions import GameNotFoundError
from app.services.game_session import GameSession


class GameService:
    """Create and look up in-memory game sessions."""

    def __init__(self):
        self._sessions: dict[str, GameSession] = {}
        self._lock = RLock()

    def create_session(self, player_name: str) -> GameSession:
        game_id = uuid4().hex
        session = GameSession(game_id, player_name)
        with self._lock:
            self._sessions[game_id] = session
        return session

    def get_session(self, game_id: str) -> GameSession:
        if not isinstance(game_id, str) or not game_id.strip():
            raise ValueError("Invalid game ID")

        with self._lock:
            session = self._sessions.get(game_id)

        if session is None:
            raise GameNotFoundError(f"Game session not found: {game_id}")
        return session

    def get_state(self, game_id: str) -> dict:
        return self.get_session(game_id).get_state()

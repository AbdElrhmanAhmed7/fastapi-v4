"""FastAPI dependency wiring.

Everything expensive (settings, content, database, services) is created once in
the application lifespan and stored on ``app.state``. Request-scoped code only
ever *reads* those singletons, which keeps handlers thin and testable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Path, Request

from app.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.security import constant_time_equals, verify_session_token
from app.domain.enums import DataProvenance
from app.integrations.nasa_client import NasaClient
from app.missions.registry import GameContent
from app.services.earth_service import EarthService
from app.services.game_service import GameService
from app.services.science_service import ScienceService
from app.services.simulation_service import SimulationService

# ---------------------------------------------------------------------------
# Application singletons
# ---------------------------------------------------------------------------


def get_game_service(request: Request) -> GameService:
    """The authoritative gameplay service owned by this app process."""
    return request.app.state.games  # type: ignore[no-any-return]


def get_simulation_service(request: Request) -> SimulationService:
    return request.app.state.simulations  # type: ignore[no-any-return]


def get_earth_service(request: Request) -> EarthService:
    return request.app.state.games.earth  # type: ignore[no-any-return]


def get_science_service(request: Request) -> ScienceService:
    return request.app.state.games.science  # type: ignore[no-any-return]


def get_content(request: Request) -> GameContent:
    return request.app.state.content  # type: ignore[no-any-return]


def get_nasa_client(request: Request) -> NasaClient:
    return request.app.state.nasa  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings)]
GamesDep = Annotated[GameService, Depends(get_game_service)]
SimulationsDep = Annotated[SimulationService, Depends(get_simulation_service)]
EarthDep = Annotated[EarthService, Depends(get_earth_service)]
ScienceDep = Annotated[ScienceService, Depends(get_science_service)]
ContentDep = Annotated[GameContent, Depends(get_content)]
NasaDep = Annotated[NasaClient, Depends(get_nasa_client)]

GameId = Annotated[
    str,
    Path(
        min_length=4,
        max_length=64,
        description="Game session id returned by POST /api/v1/games.",
        examples=["nova_7f3c9a2b"],
    ),
]
MissionId = Annotated[
    str,
    Path(
        min_length=2,
        max_length=64,
        description="Mission id from GET /api/v1/games/{game_id}/missions.",
        examples=["analog_water_leak"],
    ),
]

SessionTokenHeader = Annotated[
    str | None,
    Header(
        alias="X-Session-Token",
        description="Token returned by POST /games. Required for writes when NOVA_AUTH_ENABLED=true.",
    ),
]
ApiKeyHeader = Annotated[
    str | None,
    Header(alias="X-API-Key", description="Optional trusted server-to-server key."),
]
IdempotencyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=64,
        description="Retry-safe key: repeating a request with the same key replays the first result.",
    ),
]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


async def require_game_write(
    game_id: str,
    settings: SettingsDep,
    session_token: SessionTokenHeader = None,
    api_key: ApiKeyHeader = None,
) -> None:
    """Authorise a mutation on one game session.

    Disabled by default so the 3D client can be prototyped without auth. When
    ``NOVA_AUTH_ENABLED=true`` the caller must present either the HMAC session
    token issued for *this* game or the global API key.
    """
    if not settings.auth_enabled:
        return
    if settings.api_key and api_key and constant_time_equals(api_key, settings.api_key):
        return
    if not session_token:
        raise AuthenticationError(
            "Missing X-Session-Token. Create or resume the game to obtain one."
        )
    verify_session_token(session_token, settings.secret_key, game_id)


WriteGuard = Depends(require_game_write)


def provenance_label(value: str) -> str:
    """Normalise a provenance string for responses (defensive helper)."""
    try:
        return DataProvenance(value).value
    except ValueError:
        return DataProvenance.simulated.value

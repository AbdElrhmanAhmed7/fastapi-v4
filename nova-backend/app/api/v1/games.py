"""Game session lifecycle, HUD ticks, events, continuity and reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, status

from app.core.logging import get_logger
from app.dependencies import GameId, GamesDep, WriteGuard
from app.schemas.common import MessageResponse
from app.schemas.events import EventPage, LeaderboardEntry
from app.schemas.game import (
    ContinuityResponse,
    GameCreatedResponse,
    GameCreateRequest,
    GameListItem,
    GameStatePayload,
    TickRequest,
    TickResponse,
)
from app.schemas.mission import CampaignReport, MissionReport

logger = get_logger("nova.api.games")

router = APIRouter(prefix="/games", tags=["Games"])
leaderboard_router = APIRouter(tags=["Games"])


@router.post(
    "",
    response_model=GameCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a game session",
    responses={409: {"description": "Server session limit reached"}},
)
async def create_game(payload: GameCreateRequest, games: GamesDep) -> dict[str, Any]:
    """Start a new campaign at the Training Facility.

    Store ``game_id`` (and ``session_token`` when auth is enabled) in the
    frontend; every other endpoint is scoped to that id.
    """
    return await games.create_game(
        player_name=payload.player_name, difficulty=payload.difficulty, seed=payload.seed
    )


@router.get("", response_model=list[GameListItem], summary="List saved sessions")
async def list_games(
    games: GamesDep,
    player_name: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return await games.list_games(player_name=player_name, limit=limit, offset=offset)


@router.get(
    "/{game_id}",
    response_model=GameStatePayload,
    summary="Full authoritative snapshot (HUD bootstrap)",
)
async def get_game(
    game_id: GameId,
    games: GamesDep,
    event_limit: int = Query(default=25, ge=0, le=200),
) -> dict[str, Any]:
    return await games.snapshot(game_id, event_limit=event_limit)


@router.post(
    "/{game_id}/resume",
    response_model=GameCreatedResponse,
    summary="Resume a save and re-issue its session token",
)
async def resume_game(game_id: GameId, games: GamesDep) -> dict[str, Any]:
    return await games.resume(game_id)


@router.delete(
    "/{game_id}",
    response_model=MessageResponse,
    summary="Delete a save (and its events, reports and samples)",
    dependencies=[WriteGuard],
)
async def delete_game(game_id: GameId, games: GamesDep) -> MessageResponse:
    await games.delete_game(game_id)
    return MessageResponse(ok=True, message=f"Game {game_id} deleted.")


@router.post(
    "/{game_id}/tick",
    response_model=TickResponse,
    summary="Advance life support (call from the client game loop)",
    dependencies=[WriteGuard],
)
async def tick(game_id: GameId, payload: TickRequest, games: GamesDep) -> dict[str, Any]:
    """Drain oxygen for the reported activity and advance mission timers.

    Call this ~1×/second from the frontend loop, not every frame. ``dt_seconds``
    is clamped server-side so a paused tab cannot fast-forward the simulation.
    """
    return await games.tick(game_id, dt_seconds=payload.dt_seconds, activity=payload.activity.value)


@router.get(
    "/{game_id}/events",
    response_model=EventPage,
    summary="Poll the event log (cursor based)",
)
async def events(
    game_id: GameId,
    games: GamesDep,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    types: list[str] | None = Query(default=None, description="Repeat to filter, e.g. ?types=action.succeeded"),
) -> dict[str, Any]:
    return await games.events(game_id, after_seq=after_seq, limit=limit, types=types)


@router.get(
    "/{game_id}/continuity",
    response_model=ContinuityResponse,
    summary="Mission Continuity System: readiness and carry-over modifiers",
)
async def continuity(game_id: GameId, games: GamesDep) -> dict[str, Any]:
    return await games.continuity(game_id)


@router.get(
    "/{game_id}/reports",
    response_model=CampaignReport,
    summary="Campaign report (all completed missions)",
)
async def campaign_report(game_id: GameId, games: GamesDep) -> dict[str, Any]:
    return await games.all_reports(game_id)


@router.get(
    "/{game_id}/reports/{mission_id}",
    response_model=MissionReport,
    summary="One mission report",
    responses={404: {"description": "Mission not completed yet"}},
)
async def mission_report(game_id: GameId, mission_id: str, games: GamesDep) -> dict[str, Any]:
    return await games.mission_report(game_id, mission_id)


@leaderboard_router.get(
    "/leaderboard", response_model=list[LeaderboardEntry], summary="Top campaigns by readiness"
)
async def leaderboard(
    games: GamesDep, limit: int = Query(default=10, ge=1, le=100)
) -> list[dict[str, Any]]:
    return await games.leaderboard(limit)

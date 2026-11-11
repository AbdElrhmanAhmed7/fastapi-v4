"""Event log / websocket contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GameEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: int
    type: str
    mission_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: Any = None


class EventPage(BaseModel):
    game_id: str
    after_seq: int
    events: list[GameEvent] = Field(default_factory=list)
    next_seq: int = 0
    has_more: bool = False


class LeaderboardEntry(BaseModel):
    rank: int
    game_id: str
    player_name: str
    missions_completed: int
    readiness: float
    average_score_pct: float

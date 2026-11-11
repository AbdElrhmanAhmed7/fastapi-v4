"""Game session request/response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import Activity, Difficulty
from app.schemas.common import Localised


class GameCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_name: str = Field(min_length=1, max_length=50, examples=["Nova Commander"])
    difficulty: Difficulty | None = Field(
        default=None, description="Defaults to NOVA_DEFAULT_DIFFICULTY (standard)."
    )
    seed: int | None = Field(
        default=None, ge=0, le=2**31 - 1, description="Optional deterministic seed for reproducible runs."
    )

    @field_validator("player_name")
    @classmethod
    def strip_player_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Player name cannot contain only whitespace")
        return value


class HudSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    critical_resources: list[str] = Field(default_factory=list)
    degraded_tools: list[dict[str, Any]] = Field(default_factory=list)
    oxygen_alarm: str | None = None
    readiness: float = 50.0
    readiness_grade: str = "C"
    active_mission: str | None = None


class GameStatePayload(BaseModel):
    """The authoritative save document plus HUD conveniences."""

    model_config = ConfigDict(extra="allow")

    game_id: str
    player_name: str
    difficulty: Difficulty
    environment: str
    resources: dict[str, int]
    resource_caps: dict[str, int] = Field(default_factory=dict)
    inventory: dict[str, int]
    suit: dict[str, Any] = Field(default_factory=dict)
    equipment: dict[str, Any] = Field(default_factory=dict)
    missions: dict[str, Any] = Field(default_factory=dict)
    missions_catalogue: list[dict[str, Any]] = Field(default_factory=list)
    continuity: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    world: dict[str, Any] = Field(default_factory=dict)
    flags: dict[str, bool] = Field(default_factory=dict)
    hud: HudSummary = Field(default_factory=HudSummary)
    version: int = 1
    finished: bool = False


class GameCreatedResponse(BaseModel):
    game_id: str
    session_token: str = Field(description="HMAC token bound to this game; send as X-Session-Token.")
    state: GameStatePayload


class GameListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    game_id: str
    player_name: str
    difficulty: str
    environment: str
    readiness: float
    missions_completed: int
    finished: bool
    created_at: Any
    updated_at: Any
    last_seen_at: Any


class TickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dt_seconds: float = Field(default=1.0, gt=0, le=60, description="Wall-clock delta from the client loop.")
    activity: Activity = Field(default=Activity.idle, description="Movement state: idle/walk/run/eva/rover.")


class TickResponse(BaseModel):
    game_id: str
    telemetry: dict[str, Any]
    resources: dict[str, int]


class EvaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = True


class ContinuityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    readiness: float
    readiness_grade: str
    modifiers: list[dict[str, Any]] = Field(default_factory=list)
    pending: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    note: Localised = Field(default_factory=Localised)

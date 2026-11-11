"""Prototype-01 response shapes, kept for backwards compatibility.

The original in-memory prototype exposed ``/games`` and
``/games/{id}/missions/water-leak``. Those URLs still work, now backed by the
persistent engine, so an older frontend build keeps running.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LegacyGameCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_name: str = Field(min_length=1, max_length=50)

    @field_validator("player_name")
    @classmethod
    def strip_player_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Player name cannot contain only whitespace")
        return value


class LegacyMissionState(BaseModel):
    id: str
    status: str
    current_objective: str | None = None
    leak_detected: bool = False
    valve_closed: bool = False
    pipe_repaired: bool = False
    leak_active: bool = False


class LegacyGameState(BaseModel):
    game_id: str
    player_name: str
    resources: dict[str, int]
    inventory: dict[str, int]
    missions: dict[str, LegacyMissionState]


class LegacyActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["scan_leak", "close_valve", "repair_pipe", "verify_pressure"]


class LegacyActionResponse(BaseModel):
    success: bool
    event: str
    message: str
    mission_status: str
    next_objective: str | None = None
    resources: dict[str, int]
    inventory: dict[str, int]
    mission: LegacyMissionState

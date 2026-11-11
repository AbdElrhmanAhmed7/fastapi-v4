"""Mission request/response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Localised


class MissionActionRequest(BaseModel):
    """One interaction (E key) against a world object.

    ``payload`` carries action parameters declared by the mission definition
    (for example ``{"site": "delta_flats"}``). Unknown fields are rejected by
    the engine with a 422 so typos never silently pass.
    """

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=64, examples=["close_valve"])
    payload: dict[str, Any] = Field(default_factory=dict)


class ObjectiveView(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: Localised
    hint: Localised
    target: str
    animation: str
    duration_ms: int
    optional: bool = False
    completed: bool = False
    available: bool = False
    unmet: list[dict[str, Any]] = Field(default_factory=list)
    points: float = 0.0


class MissionView(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    stage: int
    title: Localised
    summary: Localised
    environment: str
    status: str
    current_objective: str | None = None
    completed_objectives: list[str] = Field(default_factory=list)
    objectives: list[ObjectiveView] = Field(default_factory=list)
    side_actions: list[str] = Field(default_factory=list)
    attempts: int = 0
    mistakes: int = 0
    score: float | None = None
    grade: str | None = None
    elapsed_seconds: float = 0.0
    requires_missions: list[str] = Field(default_factory=list)
    simulation: str | None = None
    simulation_state: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str | None = None


class MissionActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool
    mission_id: str
    action: str
    event: str
    message: str
    animation: str = "interact"
    duration_ms: int = 900
    target: str = "generic"
    changes: dict[str, Any] = Field(default_factory=dict)
    mission: MissionView
    resources: dict[str, int] = Field(default_factory=dict)
    inventory: dict[str, int] = Field(default_factory=dict)
    completed: bool = False
    report: dict[str, Any] | None = None
    side_effects: dict[str, Any] = Field(default_factory=dict)
    replayed: bool = False


class MissionStartResponse(BaseModel):
    mission: MissionView
    resources: dict[str, int] = Field(default_factory=dict)


class MissionCatalogueEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    stage: int
    title: Localised
    summary: Localised
    environment: str
    status: str
    requires_missions: list[str] = Field(default_factory=list)
    objectives_total: int
    objectives_done: int
    score: float | None = None
    grade: str | None = None
    simulation: str | None = None
    estimated_minutes: int = 8


class InteractionView(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str
    title: Localised
    animation: str
    duration_ms: int
    available: bool
    completed: bool
    unmet: list[dict[str, Any]] = Field(default_factory=list)
    payload: list[dict[str, Any]] = Field(default_factory=list)


class WorldObjectView(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    label: Localised
    kind: str
    position: list[float]
    prompt: Localised | None = None
    interactions: list[InteractionView] = Field(default_factory=list)


class InteractionMap(BaseModel):
    mission_id: str
    objects: list[WorldObjectView] = Field(default_factory=list)


class MissionReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    mission_id: str
    title: Localised
    stage: int
    environment: str
    status: str
    score_pct: float
    grade: str
    passed: bool
    elapsed_seconds: float
    mistakes: int
    objectives: list[dict[str, Any]] = Field(default_factory=list)
    continuity_modifiers: list[dict[str, Any]] = Field(default_factory=list)
    readiness: float
    readiness_grade: str
    unlocked_missions: list[str] = Field(default_factory=list)
    science_notes: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str | None = None


class CampaignReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    game_id: str
    player_name: str
    missions_completed: int
    missions_total: int
    average_score_pct: float
    readiness: float
    readiness_grade: str
    total_playtime_seconds: float
    reports: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

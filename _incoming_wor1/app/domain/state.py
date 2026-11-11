"""The authoritative game state document.

One :class:`GameState` per player session. It is validated by pydantic on every
load and persisted as JSON (plus normalised side tables for events, results and
science samples). Keeping the mutable gameplay state in one document makes
save/load, snapshots and rollback trivial while the domain stays pure Python.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import utcnow
from app.domain.enums import Difficulty, Environment, MissionStatus

STATE_SCHEMA_VERSION = 1


class EquipmentState(BaseModel):
    """Wear & tear of a durable tool. Feeds the Mission Continuity System."""

    condition: int = Field(default=100, ge=0, le=100)
    maintenance_count: int = 0
    failure_count: int = 0
    calibrated: bool = True


class SuitState(BaseModel):
    integrity: int = Field(default=100, ge=0, le=100)
    eva_active: bool = False
    co2_filter: int = Field(default=100, ge=0, le=100)
    oxygen_alarm: str | None = None
    last_activity: str = "idle"


class MissionProgress(BaseModel):
    mission_id: str
    status: MissionStatus = MissionStatus.locked
    completed_objectives: list[str] = Field(default_factory=list)
    current_objective: str | None = None
    attempts: int = 0
    mistakes: int = 0
    score: float | None = None
    grade: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float = 0.0
    action_counts: dict[str, int] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class ContinuityModifier(BaseModel):
    """A transparent, explainable carry-over effect between missions."""

    key: str
    label: str
    value: float
    source_mission: str
    reason: str
    kind: str = "resource"
    applied: bool = False


class ContinuityState(BaseModel):
    readiness: float = Field(default=50.0, ge=0, le=100)
    readiness_grade: str = "C"
    modifiers: list[ContinuityModifier] = Field(default_factory=list)
    applied_missions: list[str] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)


class PlayerStats(BaseModel):
    actions: int = 0
    successful_actions: int = 0
    mistakes: int = 0
    missions_completed: int = 0
    missions_failed: int = 0
    samples_collected: int = 0
    distance_metres: float = 0.0
    eva_seconds: float = 0.0
    playtime_seconds: float = 0.0
    resources_consumed: dict[str, int] = Field(default_factory=dict)
    resources_recovered: dict[str, int] = Field(default_factory=dict)


class GameState(BaseModel):
    """Complete, serialisable save game."""

    model_config = ConfigDict(extra="ignore", validate_assignment=False)

    schema_version: int = STATE_SCHEMA_VERSION
    game_id: str
    player_name: str
    difficulty: Difficulty = Difficulty.standard
    seed: int = 0

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_tick_at: datetime | None = None

    environment: Environment = Environment.training_facility
    resources: dict[str, int] = Field(default_factory=dict)
    resource_caps: dict[str, int] = Field(default_factory=dict)
    inventory: dict[str, int] = Field(default_factory=dict)
    equipment: dict[str, EquipmentState] = Field(default_factory=dict)
    suit: SuitState = Field(default_factory=SuitState)

    flags: dict[str, bool] = Field(default_factory=dict)
    world: dict[str, Any] = Field(default_factory=dict)

    missions: dict[str, MissionProgress] = Field(default_factory=dict)
    simulations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    continuity: ContinuityState = Field(default_factory=ContinuityState)
    stats: PlayerStats = Field(default_factory=PlayerStats)

    sample_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    event_seq: int = 0
    version: int = 1
    finished: bool = False

    # -- helpers ------------------------------------------------------------
    def mission(self, mission_id: str) -> MissionProgress:
        progress = self.missions.get(mission_id)
        if progress is None:
            progress = MissionProgress(mission_id=mission_id)
            self.missions[mission_id] = progress
        return progress

    def has_flag(self, name: str) -> bool:
        return bool(self.flags.get(name, False))

    def set_flag(self, name: str, value: bool = True) -> None:
        self.flags[name] = value

    def touch(self) -> None:
        self.updated_at = utcnow()

    def public_snapshot(self) -> dict[str, Any]:
        """State shaped for the HUD (no internal bookkeeping)."""
        data = self.model_dump(mode="json")
        data.pop("schema_version", None)
        return data

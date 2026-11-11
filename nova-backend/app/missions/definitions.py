"""Typed schema for data-driven mission definitions.

Missions are *content*, not code: every stage of NOVA is described by a JSON
file in ``content/missions``. The engine interprets these definitions, which
means designers can add objectives, rebalance costs or localise text without
touching Python – and every mission automatically gets the same validation,
scoring, event and continuity behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.errors import ContentError
from app.domain.catalog import LocalisedText
from app.domain.enums import Environment, SimulationKind

# ---------------------------------------------------------------------------
# Conditions (preconditions an action must satisfy)
# ---------------------------------------------------------------------------


class _ConditionBase(BaseModel):
    hint: LocalisedText | None = None


class ItemCondition(_ConditionBase):
    type: Literal["item"] = "item"
    id: str
    min: int = 1


class ResourceCondition(_ConditionBase):
    type: Literal["resource"] = "resource"
    id: str
    min: int = 0


class FlagCondition(_ConditionBase):
    type: Literal["flag"] = "flag"
    id: str
    value: bool = True


class ObjectiveCondition(_ConditionBase):
    type: Literal["objective"] = "objective"
    id: str


class MissionCondition(_ConditionBase):
    type: Literal["mission"] = "mission"
    id: str
    status: Literal["completed", "active"] = "completed"


class WorldCondition(_ConditionBase):
    type: Literal["world"] = "world"
    path: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"] = "eq"
    value: Any = True


class SimulationCondition(_ConditionBase):
    type: Literal["sim"] = "sim"
    path: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"] = "eq"
    value: Any = True


class EquipmentCondition(_ConditionBase):
    type: Literal["equipment"] = "equipment"
    id: str
    min_condition: int = 40


Condition = Annotated[
    ItemCondition
    | ResourceCondition
    | FlagCondition
    | ObjectiveCondition
    | MissionCondition
    | WorldCondition
    | SimulationCondition
    | EquipmentCondition,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Effects (atomic state changes)
# ---------------------------------------------------------------------------
class Effects(BaseModel):
    resources: dict[str, int] = Field(default_factory=dict)
    items: dict[str, int] = Field(default_factory=dict)
    flags_set: list[str] = Field(default_factory=list)
    flags_clear: list[str] = Field(default_factory=list)
    world: dict[str, Any] = Field(default_factory=dict)
    sim: dict[str, Any] = Field(default_factory=dict)
    equipment_wear: dict[str, int] = Field(default_factory=dict)
    equipment_restore: dict[str, int] = Field(default_factory=dict)
    readiness: float = 0.0
    stats: dict[str, float] = Field(default_factory=dict)
    unlock: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [
                self.resources,
                self.items,
                self.flags_set,
                self.flags_clear,
                self.world,
                self.sim,
                self.equipment_wear,
                self.equipment_restore,
                self.readiness,
                self.stats,
                self.unlock,
            ]
        )


# ---------------------------------------------------------------------------
# Objectives / actions
# ---------------------------------------------------------------------------
class PayloadField(BaseModel):
    """Minimal description of an action payload parameter."""

    name: str
    type: Literal["string", "number", "integer", "boolean"] = "string"
    required: bool = True
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None
    label: LocalisedText | None = None


class Objective(BaseModel):
    id: str
    title: LocalisedText
    hint: LocalisedText
    target: str = "generic"
    animation: str = "interact"
    duration_ms: int = 900
    requires: list[Condition] = Field(default_factory=list)
    require_previous: bool = True
    effects: Effects = Field(default_factory=Effects)
    score: float = 10.0
    optional: bool = False
    repeatable: bool = False
    payload: list[PayloadField] = Field(default_factory=list)
    success_message: LocalisedText | None = None
    science_note: str | None = None

    @property
    def action(self) -> str:
        return self.id


class HazardRule(BaseModel):
    """Consequence of attempting an action before its prerequisites are met."""

    action: str
    missing_any: list[str] = Field(default_factory=list)
    effects: Effects = Field(default_factory=Effects)
    message: LocalisedText
    mistake_penalty: float = 4.0


class ContinuityBand(BaseModel):
    min: float = 0.0
    value: float = 0.0
    reason: LocalisedText


class ContinuityRule(BaseModel):
    key: str
    label: LocalisedText
    kind: Literal["resource", "equipment", "readiness", "hazard", "unlock"] = "resource"
    target: str | None = None
    metric: str = "score_pct"
    bands: list[ContinuityBand] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_bands(self) -> ContinuityRule:
        self.bands.sort(key=lambda band: band.min, reverse=True)
        return self


class ScoringConfig(BaseModel):
    mistake_penalty: float = 4.0
    time_target_seconds: float = 420.0
    time_bonus_max: float = 10.0
    pass_score_pct: float = 50.0
    efficiency_bonus: float = 5.0


class CompletionConfig(BaseModel):
    effects: Effects = Field(default_factory=Effects)
    unlocks: list[str] = Field(default_factory=list)
    continuity: list[ContinuityRule] = Field(default_factory=list)
    summary: LocalisedText | None = None


class ScienceNote(BaseModel):
    topic: LocalisedText
    detail: LocalisedText
    source: str = "NASA (curated summary)"
    url: str | None = None
    simulated: bool = False


class WorldObject(BaseModel):
    """Interactable object description consumed by the 3D frontend."""

    id: str
    label: LocalisedText
    kind: str = "device"
    position: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    interactions: list[str] = Field(default_factory=list)
    prompt: LocalisedText | None = None


class MissionDefinition(BaseModel):
    id: str
    stage: int
    title: LocalisedText
    summary: LocalisedText
    environment: Environment
    briefing: list[LocalisedText] = Field(default_factory=list)
    requires_missions: list[str] = Field(default_factory=list)
    simulation: SimulationKind | None = None
    objectives: list[Objective] = Field(default_factory=list)
    side_actions: list[Objective] = Field(default_factory=list)
    hazards: list[HazardRule] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    completion: CompletionConfig = Field(default_factory=CompletionConfig)
    science_notes: list[ScienceNote] = Field(default_factory=list)
    world_objects: list[WorldObject] = Field(default_factory=list)
    disclaimer: str | None = None
    estimated_minutes: int = 8

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> MissionDefinition:
        ids = [o.id for o in self.objectives] + [s.id for s in self.side_actions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Mission '{self.id}' has duplicate objective/action ids")
        if not self.objectives:
            raise ValueError(f"Mission '{self.id}' has no objectives")
        return self

    # -- lookups ------------------------------------------------------------
    def find_action(self, action_id: str) -> Objective | None:
        for objective in (*self.objectives, *self.side_actions):
            if objective.id == action_id:
                return objective
        return None

    def required_objectives(self) -> list[Objective]:
        return [o for o in self.objectives if not o.optional]

    @property
    def max_score(self) -> float:
        return sum(o.score for o in self.objectives)

    def world_object(self, object_id: str) -> WorldObject | None:
        return next((obj for obj in self.world_objects if obj.id == object_id), None)


class MissionLibrary(BaseModel):
    missions: dict[str, MissionDefinition]

    @classmethod
    def load(cls, content_dir: Path) -> MissionLibrary:
        directory = content_dir / "missions"
        if not directory.is_dir():
            raise ContentError(f"Mission content directory missing: {directory}")

        missions: dict[str, MissionDefinition] = {}
        for path in sorted(directory.glob("*.json")):
            try:
                definition = MissionDefinition(**json.loads(path.read_text("utf-8")))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ContentError(f"Invalid mission file {path.name}: {exc}") from exc
            if definition.id in missions:
                raise ContentError(f"Duplicate mission id: {definition.id}")
            missions[definition.id] = definition

        if not missions:
            raise ContentError("No mission definitions found.")

        for definition in missions.values():
            for dependency in definition.requires_missions:
                if dependency not in missions:
                    raise ContentError(
                        f"Mission '{definition.id}' requires unknown mission '{dependency}'"
                    )
        return cls(missions=missions)

    def ordered(self) -> list[MissionDefinition]:
        return sorted(self.missions.values(), key=lambda m: (m.stage, m.id))

    def get(self, mission_id: str) -> MissionDefinition | None:
        return self.missions.get(mission_id)

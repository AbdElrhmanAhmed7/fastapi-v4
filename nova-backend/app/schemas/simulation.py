"""Simulation (server-authoritative physics) contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SimulationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional scenario overrides, e.g. {'altitude': 1200, 'co2_charges': 4}.",
    )


class SimulationTickRequest(BaseModel):
    """Client sends *intent*, never positions or scores."""

    model_config = ConfigDict(extra="forbid")

    dt_seconds: float = Field(default=0.5, gt=0, le=1.0)
    command: dict[str, Any] = Field(
        default_factory=dict,
        examples=[
            {"throttle": 0.62, "attitude": -0.2},
            {"co2": 0.8, "ventilation": False, "scrubber": True},
            {"target": "sample_site", "throttle": 0.7},
            {"oxygen_support": True, "resting": True},
        ],
    )


class SimulationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    game_id: str
    mission_id: str
    run_id: str | None = None
    telemetry: dict[str, Any]
    finished: bool = False
    result: dict[str, Any] | None = None


class SimulationDescriptor(BaseModel):
    kind: str
    max_duration_seconds: float
    start_cost: dict[str, int] = Field(default_factory=dict)
    telemetry_fields: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)


class SimulationRunRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    mission_id: str
    kind: str
    status: str
    score_pct: float
    result: dict[str, Any] = Field(default_factory=dict)

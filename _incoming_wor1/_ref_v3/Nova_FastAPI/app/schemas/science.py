"""Martian Guide science contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SampleRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    sample_id: str
    game_id: str
    mission_id: str
    site: str
    rock_type: str
    composition: dict[str, float] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class SampleAnalyseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class ScienceDatabaseResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    game_id: str
    samples_total: int
    samples_analysed: int
    by_rock_type: dict[str, int] = Field(default_factory=dict)
    samples: list[SampleRecord] = Field(default_factory=list)
    mineral_reference: list[dict[str, Any]] = Field(default_factory=list)
    sites: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str = ""

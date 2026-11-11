"""Earth Trends contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EarthCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: str = Field(default="nile_delta", examples=["nile_delta", "amazon_basin"])
    layer_id: str = Field(default="ndvi", examples=["ndvi", "lst", "precipitation"])
    period_before: str = Field(default="2001-2005", pattern=r"^\d{4}-\d{4}$")
    period_after: str = Field(default="2021-2025", pattern=r"^\d{4}-\d{4}$")
    live: bool = Field(default=False, description="Try NASA POWER live data first (falls back to bundled).")
    grid_size: int = Field(default=8, ge=4, le=16)


class EarthReportRequest(EarthCompareRequest):
    title: str = Field(default="", max_length=120)


class EarthAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    region: str
    layer: str
    unit: str
    period_before: str
    period_after: str
    provenance: str
    mean_delta: float
    mean_delta_pct: float
    direction: str
    verdict: str
    cells_changed: int
    cells_improved: int
    cells_degraded: int
    hotspots: list[dict[str, Any]] = Field(default_factory=list)
    narrative: dict[str, str] = Field(default_factory=dict)
    nasa_product: str = ""
    nasa_url: str = ""


class EarthReportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_id: str
    game_id: str
    region_id: str
    layer_id: str
    period_before: str
    period_after: str
    provenance: str
    verdict: str
    mean_delta: float
    analysis: dict[str, Any] = Field(default_factory=dict)

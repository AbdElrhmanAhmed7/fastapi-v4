"""Shared API schema primitives."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Localised(BaseModel):
    """Every player-facing string ships in English and Arabic."""

    en: str = ""
    ar: str = ""


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code, e.g. 'precondition_failed'.")
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str | None = None


class MessageResponse(BaseModel):
    ok: bool = True
    message: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    environment: str
    uptime_seconds: float = 0.0


class ReadinessResponse(BaseModel):
    status: str
    database: bool
    content: bool
    missions_loaded: int
    items_loaded: int
    nasa_cache: dict[str, Any] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    name: str
    version: str
    api_version: str
    state_schema_version: int
    missions: int
    stages: int
    simulations: list[str]
    tagline: str = "Explore. Experience. Execute."


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

"""Inventory, resources and suit contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UseItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=48, examples=["oxygen_tank"])
    target: str | None = Field(default=None, description="Optional target, e.g. the tool to service.")


class MaintainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=48, examples=["scanner"])


class ItemUseResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    item: str
    message: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    inventory: dict[str, int] = Field(default_factory=dict)
    resources: dict[str, int] = Field(default_factory=dict)
    equipment: dict[str, Any] = Field(default_factory=dict)


class MaintenanceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    item: str
    condition: int
    method: str
    inventory: dict[str, int] = Field(default_factory=dict)
    resources: dict[str, int] = Field(default_factory=dict)


class InventoryResponse(BaseModel):
    game_id: str
    inventory: dict[str, int]
    equipment: dict[str, Any] = Field(default_factory=dict)
    degraded_tools: list[dict[str, Any]] = Field(default_factory=list)
    capacity: dict[str, Any] = Field(default_factory=dict)


class ResourcesResponse(BaseModel):
    game_id: str
    resources: dict[str, int]
    caps: dict[str, int] = Field(default_factory=dict)
    ratios: dict[str, float] = Field(default_factory=dict)
    critical: list[str] = Field(default_factory=list)

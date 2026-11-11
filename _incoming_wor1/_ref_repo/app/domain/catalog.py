"""Content catalogue: resources and items.

Definitions live in ``content/*.json`` so designers can balance the game
without touching Python. Everything is validated at startup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import ContentError
from app.domain.enums import ItemCategory


class LocalisedText(BaseModel):
    en: str
    ar: str = ""

    def render(self, lang: str = "en") -> str:
        return self.ar if lang == "ar" and self.ar else self.en


class ResourceDefinition(BaseModel):
    id: str
    name: LocalisedText
    unit: str
    start: int = Field(ge=0)
    cap: int = Field(gt=0)
    description: LocalisedText
    critical_below: int = 15


class ItemDefinition(BaseModel):
    id: str
    name: LocalisedText
    category: ItemCategory
    description: LocalisedText
    stackable: bool = True
    max_stack: int = 9
    durable: bool = False
    wear_per_use: int = Field(default=0, ge=0, le=100)
    mass_kg: float = 0.0
    tags: list[str] = Field(default_factory=list)


class Catalog(BaseModel):
    resources: dict[str, ResourceDefinition]
    items: dict[str, ItemDefinition]

    @classmethod
    def load(cls, content_dir: Path) -> Catalog:
        try:
            resources_raw = json.loads((content_dir / "resources.json").read_text("utf-8"))
            items_raw = json.loads((content_dir / "items.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContentError(f"Cannot read catalogue content: {exc}") from exc

        resources = {r["id"]: ResourceDefinition(**r) for r in resources_raw}
        items = {i["id"]: ItemDefinition(**i) for i in items_raw}
        if not resources or not items:
            raise ContentError("Catalogue content is empty.")
        return cls(resources=resources, items=items)

    def item(self, item_id: str) -> ItemDefinition | None:
        return self.items.get(item_id)

    def starting_resources(self) -> dict[str, int]:
        return {rid: definition.start for rid, definition in self.resources.items()}

    def resource_caps(self) -> dict[str, int]:
        return {rid: definition.cap for rid, definition in self.resources.items()}

    def describe_items(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.items.values()]

    def describe_resources(self) -> list[dict[str, Any]]:
        return [res.model_dump(mode="json") for res in self.resources.values()]

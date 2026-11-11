"""Loads and caches all bundled game content once per process."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import ContentError
from app.domain.catalog import Catalog
from app.missions.definitions import MissionLibrary


class GameContent:
    """Immutable container for catalogue + mission definitions."""

    def __init__(self, catalog: Catalog, missions: MissionLibrary, content_dir: Path) -> None:
        self.catalog = catalog
        self.missions = missions
        self.content_dir = content_dir

    @classmethod
    def load(cls, content_dir: Path) -> GameContent:
        return cls(
            catalog=Catalog.load(content_dir),
            missions=MissionLibrary.load(content_dir),
            content_dir=content_dir,
        )

    def read_json(self, relative_path: str) -> Any:
        path = self.content_dir / relative_path
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContentError(f"Cannot read content '{relative_path}': {exc}") from exc


@lru_cache(maxsize=4)
def load_content(content_dir: Path) -> GameContent:
    return GameContent.load(content_dir)

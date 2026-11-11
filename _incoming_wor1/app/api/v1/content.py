"""Static game content: catalogue, mission blueprints and control bindings.

The 3D client fetches these once at boot so item names, mission briefings and
key bindings never have to be hard-coded in React.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.errors import MissionNotFoundError
from app.dependencies import ContentDep
from app.domain.enums import Environment, SimulationKind
from app.schemas.common import Localised

router = APIRouter(prefix="/content", tags=["Content"])

#: The control scheme agreed for the 3D client. The backend owns the *meaning*
#: of each interaction; the frontend owns movement, camera and animation.
CONTROLS: list[dict[str, Any]] = [
    {"keys": ["W", "A", "S", "D"], "action": "move", "api": "POST /games/{id}/tick (activity=walk)"},
    {"keys": ["Mouse"], "action": "look", "api": None},
    {"keys": ["Shift"], "action": "sprint", "api": "POST /games/{id}/tick (activity=run)"},
    {"keys": ["Space"], "action": "jump", "api": None},
    {"keys": ["E"], "action": "interact", "api": "POST /games/{id}/missions/{mission_id}/actions"},
    {"keys": ["F"], "action": "use_tool", "api": "POST /games/{id}/inventory/use"},
    {"keys": ["V"], "action": "toggle_camera", "api": None},
    {"keys": ["Tab"], "action": "inventory", "api": "GET /games/{id}/inventory"},
]


@router.get("/catalogue", summary="Items, resources, environments and controls")
async def catalogue(content: ContentDep) -> dict[str, Any]:
    return {
        "items": content.catalog.describe_items(),
        "resources": content.catalog.describe_resources(),
        "environments": [environment.value for environment in Environment],
        "simulations": [kind.value for kind in SimulationKind],
        "controls": CONTROLS,
        "languages": ["en", "ar"],
    }


@router.get("/items", summary="Item catalogue")
async def items(content: ContentDep) -> list[dict[str, Any]]:
    return content.catalog.describe_items()


@router.get("/resources", summary="Resource definitions (units, caps, critical levels)")
async def resources(content: ContentDep) -> list[dict[str, Any]]:
    return content.catalog.describe_resources()


@router.get("/missions", summary="Every mission blueprint (designer-facing, no player state)")
async def missions(
    content: ContentDep,
    stage: int | None = Query(default=None, ge=1, le=8, description="Filter by stage 1-8."),
    environment: Environment | None = Query(default=None),
) -> list[dict[str, Any]]:
    definitions = content.missions.ordered()
    if stage is not None:
        definitions = [d for d in definitions if d.stage == stage]
    if environment is not None:
        definitions = [d for d in definitions if d.environment == environment]
    return [
        {
            "id": d.id,
            "stage": d.stage,
            "title": d.title.model_dump(),
            "summary": d.summary.model_dump(),
            "environment": d.environment.value,
            "briefing": [line.model_dump() for line in d.briefing],
            "requires_missions": d.requires_missions,
            "simulation": d.simulation.value if d.simulation else None,
            "objectives": [o.id for o in d.objectives],
            "side_actions": [o.id for o in d.side_actions],
            "world_objects": [obj.model_dump(mode="json") for obj in d.world_objects],
            "science_notes": [note.model_dump(mode="json") for note in d.science_notes],
            "estimated_minutes": d.estimated_minutes,
            "disclaimer": d.disclaimer,
        }
        for d in definitions
    ]


@router.get("/missions/{mission_id}", summary="One mission blueprint in full detail")
async def mission_blueprint(mission_id: str, content: ContentDep) -> dict[str, Any]:
    definition = content.missions.get(mission_id)
    if definition is None:
        raise MissionNotFoundError(
            f"Unknown mission '{mission_id}'.",
            available=[m.id for m in content.missions.ordered()],
        )
    return definition.model_dump(mode="json")


@router.get("/controls", summary="Keyboard/mouse bindings expected by the game client")
async def controls() -> dict[str, Any]:
    return {
        "controls": CONTROLS,
        "note": Localised(
            en="Movement, camera and animation are client-side. Every world interaction is validated by the server.",
            ar="الحركة والكاميرا والأنيميشن على العميل. كل تفاعل مع العالم بيتحقق منه السيرفر.",
        ).model_dump(),
    }

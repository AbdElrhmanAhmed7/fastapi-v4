"""Resources, inventory (Tab), tools (F) and the EVA suit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.dependencies import GameId, GamesDep, WriteGuard
from app.schemas.game import EvaRequest
from app.schemas.loadout import (
    InventoryResponse,
    ItemUseResponse,
    MaintainRequest,
    MaintenanceResponse,
    ResourcesResponse,
    UseItemRequest,
)

router = APIRouter(prefix="/games/{game_id}", tags=["Loadout"])


@router.get("/resources", response_model=ResourcesResponse, summary="Water / oxygen / power / fuel")
async def resources(game_id: GameId, games: GamesDep) -> dict[str, Any]:
    return await games.resources_view(game_id)


@router.get("/inventory", response_model=InventoryResponse, summary="Inventory + tool condition")
async def inventory(game_id: GameId, games: GamesDep) -> dict[str, Any]:
    return await games.inventory_view(game_id)


@router.post(
    "/inventory/use",
    response_model=ItemUseResponse,
    summary="Use a consumable (F key): oxygen tank, power cell, maintenance kit…",
    dependencies=[WriteGuard],
    responses={409: {"description": "Item not carried"}, 422: {"description": "Item is not directly usable"}},
)
async def use_item(
    game_id: GameId, payload: UseItemRequest, games: GamesDep
) -> dict[str, Any]:
    return await games.use_item(game_id, payload.item_id, target=payload.target)


@router.post(
    "/inventory/maintain",
    response_model=MaintenanceResponse,
    summary="Service a worn tool (uses a maintenance kit, else habitat power)",
    dependencies=[WriteGuard],
)
async def maintain(
    game_id: GameId, payload: MaintainRequest, games: GamesDep
) -> dict[str, Any]:
    """Maintenance is not cosmetic: worn tools fail missions and reduce the
    confidence of lab analysis later in the campaign."""
    return await games.maintain_equipment(game_id, payload.item_id)


@router.get("/suit", summary="Suit integrity, EVA state and oxygen drain rates")
async def suit(game_id: GameId, games: GamesDep) -> dict[str, Any]:
    return await games.suit_view(game_id)


@router.post(
    "/suit/eva",
    summary="Toggle EVA mode (leaving the pressurised habitat)",
    dependencies=[WriteGuard],
)
async def set_eva(game_id: GameId, payload: EvaRequest, games: GamesDep) -> dict[str, Any]:
    return await games.set_eva(game_id, payload.active)

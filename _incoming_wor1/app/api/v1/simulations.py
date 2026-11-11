"""Server-authoritative simulations (lunar lander, microgravity fire, vitals, rover).

The client sends *intent* – throttle, attitude, CO₂ discharge – plus a wall
clock delta. The server integrates the physics and returns telemetry. A client
can never write altitude, fuel, body temperature or a score.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.dependencies import GameId, GamesDep, MissionId, SettingsDep, SimulationsDep, WriteGuard
from app.schemas.simulation import (
    SimulationDescriptor,
    SimulationResponse,
    SimulationRunRecord,
    SimulationStartRequest,
    SimulationTickRequest,
)
from app.simulations import factory

router = APIRouter(prefix="/games/{game_id}/missions/{mission_id}/simulation", tags=["Simulations"])
catalogue_router = APIRouter(tags=["Simulations"])

#: Human-readable control contract per simulator, mirrored in the docs.
CONTROLS: dict[str, list[str]] = {
    "lunar_lander": ["throttle (0..1)", "attitude (-1..1)"],
    "microgravity_fire": ["co2 (0..1)", "ventilation (bool)", "scrubber (bool)"],
    "crew_vitals": ["oxygen_support (bool)", "resting (bool)", "cooling (bool)"],
    "rover_traverse": ["throttle (0..1)", "steering (-1..1)", "target (waypoint id)"],
}


@catalogue_router.get(
    "/simulations", response_model=list[SimulationDescriptor], summary="Available simulators"
)
async def descriptors() -> list[dict[str, Any]]:
    return [
        {**descriptor, "controls": CONTROLS.get(descriptor["kind"], [])}
        for descriptor in factory.describe()
    ]


@router.post(
    "/start",
    response_model=SimulationResponse,
    summary="Start a run (draws habitat power)",
    dependencies=[WriteGuard],
)
async def start(
    game_id: GameId,
    mission_id: MissionId,
    payload: SimulationStartRequest,
    simulations: SimulationsDep,
) -> dict[str, Any]:
    return await simulations.start(game_id, mission_id, payload.params)


@router.post(
    "/tick",
    response_model=SimulationResponse,
    summary="Advance the model with player intent",
    dependencies=[WriteGuard],
)
async def tick(
    game_id: GameId,
    mission_id: MissionId,
    payload: SimulationTickRequest,
    simulations: SimulationsDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Call at ~10 Hz while the player is inside a simulator.

    ``dt_seconds`` is capped at 1 s and integrated with fixed substeps, so the
    outcome is identical regardless of the client's frame rate.
    """
    return await simulations.tick(
        game_id, mission_id, command=payload.command, dt_seconds=payload.dt_seconds
    )


@router.get("", response_model=SimulationResponse, summary="Current telemetry")
async def status(
    game_id: GameId, mission_id: MissionId, simulations: SimulationsDep
) -> dict[str, Any]:
    return await simulations.status(game_id, mission_id)


@router.post(
    "/reset",
    response_model=SimulationResponse,
    summary="Abort the current run and start a fresh one",
    dependencies=[WriteGuard],
)
async def reset(
    game_id: GameId, mission_id: MissionId, simulations: SimulationsDep
) -> dict[str, Any]:
    return await simulations.reset(game_id, mission_id)


@router.get("/scene", summary="Static geometry: terrain profile, waypoints, hazards")
async def scene(
    game_id: GameId, mission_id: MissionId, simulations: SimulationsDep
) -> dict[str, Any]:
    """Fetch once when the simulator loads to build the 3D terrain mesh."""
    return await simulations.extra(game_id, mission_id)


@catalogue_router.get(
    "/games/{game_id}/simulation-runs",
    response_model=list[SimulationRunRecord],
    summary="History of simulation attempts",
)
async def runs(
    game_id: GameId,
    simulations: SimulationsDep,
    mission_id: str | None = None,
) -> list[dict[str, Any]]:
    return await simulations.runs(game_id, mission_id)

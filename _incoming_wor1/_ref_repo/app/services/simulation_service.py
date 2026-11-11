"""Simulation service: start / tick / inspect physics runs.

The client sends *intent* (throttle, attitude, CO2 discharge…) and a wall-clock
delta; the server integrates the model with fixed substeps and returns
authoritative telemetry. Clients can never set positions or scores directly.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import (
    InvalidInputError,
    MissionNotFoundError,
    SimulationNotFoundError,
    SimulationStateError,
)
from app.core.utils import clamp, new_id, round_to
from app.domain.enums import MissionStatus, SimulationStatus
from app.services.game_service import GameService
from app.simulations import factory
from app.systems.resource_manager import ResourceManager


class SimulationService:
    def __init__(self, games: GameService) -> None:
        self.games = games
        self.content = games.content

    # ------------------------------------------------------------------
    async def start(
        self, game_id: str, mission_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        definition = self.content.missions.get(mission_id)
        if definition is None:
            raise MissionNotFoundError(f"Unknown mission '{mission_id}'.")
        if definition.simulation is None:
            raise InvalidInputError(f"Mission '{mission_id}' has no simulation.")

        async with self.games.transaction(game_id) as tx:
            progress = tx.state.mission(mission_id)
            if progress.status == MissionStatus.locked:
                raise SimulationStateError(
                    f"Mission '{mission_id}' is locked.", mission_id=mission_id
                )

            existing = tx.state.simulations.get(mission_id)
            if existing and existing.get("status") == SimulationStatus.running.value:
                raise SimulationStateError(
                    "A simulation run is already active. Tick it or reset it first.",
                    mission_id=mission_id,
                )

            cost = factory.START_COST.get(definition.simulation, {})
            if cost:
                ResourceManager(tx.state, self.content.catalog).apply(cost)

            merged_params = {"difficulty": tx.state.difficulty.value, **(params or {})}
            simulation = factory.create(
                definition.simulation, mission_id, tx.state.seed, merged_params
            )
            if existing:
                factory.preserve_controls(definition.simulation, existing, simulation)
            tx.state.simulations[mission_id] = factory.dump(simulation)
            run_id = new_id("run")
            tx.state.world[f"run_id:{mission_id}"] = run_id

            await tx.runs.record(
                run_id=run_id,
                game_id=game_id,
                mission_id=mission_id,
                kind=definition.simulation.value,
                status=simulation.status.value,
                score_pct=0.0,
                result={},
            )
            tx.recorder.emit(
                "simulation.started",
                {"mission_id": mission_id, "kind": definition.simulation.value, "run_id": run_id},
                mission_id=mission_id,
            )
            return {
                "game_id": game_id,
                "mission_id": mission_id,
                "run_id": run_id,
                "telemetry": factory.telemetry(simulation),
            }

    async def tick(
        self,
        game_id: str,
        mission_id: str,
        *,
        command: dict[str, Any] | None = None,
        dt_seconds: float = 0.5,
    ) -> dict[str, Any]:
        dt = clamp(float(dt_seconds), 0.0, 1.0)
        if dt <= 0:
            raise InvalidInputError("dt_seconds must be greater than zero.")

        async with self.games.transaction(game_id) as tx:
            raw = tx.state.simulations.get(mission_id)
            if raw is None:
                raise SimulationNotFoundError(
                    f"No simulation for mission '{mission_id}'. Start it first.",
                    mission_id=mission_id,
                )
            simulation = factory.load(raw["kind"], raw)
            if simulation.status != SimulationStatus.running:
                raise SimulationStateError(
                    f"Simulation is {simulation.status.value}; start a new run to continue.",
                    mission_id=mission_id,
                    status=simulation.status.value,
                )

            implementation = factory.get_simulation(simulation.kind)
            before_status = simulation.status
            simulation = implementation.advance(simulation, command or {}, dt)
            tx.state.simulations[mission_id] = factory.dump(simulation)
            tx.state.stats.playtime_seconds = round_to(tx.state.stats.playtime_seconds + dt, 2)

            progress = tx.state.mission(mission_id)
            progress.elapsed_seconds = round_to(progress.elapsed_seconds + dt, 2)

            payload: dict[str, Any] = {
                "game_id": game_id,
                "mission_id": mission_id,
                "telemetry": factory.telemetry(simulation),
                "finished": simulation.status != SimulationStatus.running,
            }

            if simulation.status != before_status and simulation.result is not None:
                progress.data["simulation_result"] = simulation.result
                run_id = str(tx.state.world.get(f"run_id:{mission_id}", new_id("run")))
                await tx.runs.record(
                    run_id=run_id,
                    game_id=game_id,
                    mission_id=mission_id,
                    kind=simulation.kind.value,
                    status=simulation.status.value,
                    score_pct=float(simulation.result.get("score_pct", 0.0)),
                    result=simulation.result,
                )
                tx.recorder.emit(
                    "simulation.finished",
                    {
                        "mission_id": mission_id,
                        "kind": simulation.kind.value,
                        "status": simulation.status.value,
                        "result": simulation.result,
                    },
                    mission_id=mission_id,
                )
                payload["result"] = simulation.result
            return payload

    async def status(self, game_id: str, mission_id: str) -> dict[str, Any]:
        state = await self.games.read_state(game_id)
        raw = state.simulations.get(mission_id)
        if raw is None:
            raise SimulationNotFoundError(
                f"No simulation for mission '{mission_id}'.", mission_id=mission_id
            )
        simulation = factory.load(raw["kind"], raw)
        return {
            "game_id": game_id,
            "mission_id": mission_id,
            "telemetry": factory.telemetry(simulation),
        }

    async def reset(self, game_id: str, mission_id: str) -> dict[str, Any]:
        async with self.games.transaction(game_id) as tx:
            raw = tx.state.simulations.pop(mission_id, None)
            if raw is None:
                raise SimulationNotFoundError(
                    f"No simulation for mission '{mission_id}'.", mission_id=mission_id
                )
            tx.recorder.emit("simulation.reset", {"mission_id": mission_id}, mission_id=mission_id)
        return await self.start(game_id, mission_id)

    async def runs(self, game_id: str, mission_id: str | None = None) -> list[dict[str, Any]]:
        await self.games.read_state(game_id)
        async with self.games.db.session() as session:
            from app.db.repositories import SimulationRunRepository

            return await SimulationRunRepository(session).list(game_id, mission_id)

    async def extra(self, game_id: str, mission_id: str) -> dict[str, Any]:
        """Static geometry a 3D client needs once (terrain profile, waypoints…)."""
        state = await self.games.read_state(game_id)
        raw = state.simulations.get(mission_id)
        if raw is None:
            raise SimulationNotFoundError(
                f"No simulation for mission '{mission_id}'.", mission_id=mission_id
            )
        simulation = factory.load(raw["kind"], raw)
        implementation = factory.get_simulation(simulation.kind)
        payload: dict[str, Any] = {"kind": simulation.kind.value}
        if hasattr(implementation, "terrain_profile"):
            payload["terrain"] = implementation.terrain_profile(simulation)  # type: ignore[attr-defined]
        if "waypoints" in raw:
            payload["waypoints"] = raw["waypoints"]
            payload["hazards"] = raw.get("hazards", [])
        return payload

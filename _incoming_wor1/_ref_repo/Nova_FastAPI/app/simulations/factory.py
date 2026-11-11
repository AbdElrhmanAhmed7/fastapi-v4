"""Simulation registry / factory (keeps services free of circular imports)."""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidInputError
from app.domain.enums import SimulationKind
from app.simulations.base import Simulation, SimulationState
from app.simulations.crew_vitals import CrewVitalsSimulation
from app.simulations.lunar_lander import LunarLanderSimulation
from app.simulations.microgravity_fire import MicrogravityFireSimulation
from app.simulations.rover_traverse import RoverTraverseSimulation

REGISTRY: dict[SimulationKind, type[Simulation]] = {
    SimulationKind.lunar_lander: LunarLanderSimulation,
    SimulationKind.microgravity_fire: MicrogravityFireSimulation,
    SimulationKind.crew_vitals: CrewVitalsSimulation,
    SimulationKind.rover_traverse: RoverTraverseSimulation,
}

#: Power drawn from the habitat bus when a simulator is started.
START_COST: dict[SimulationKind, dict[str, int]] = {
    SimulationKind.lunar_lander: {"power": -4},
    SimulationKind.microgravity_fire: {},
    SimulationKind.crew_vitals: {"power": -1},
    SimulationKind.rover_traverse: {"power": -2},
}


#: Control inputs a mission action may pre-set before the run is started.
CONTROL_FIELDS: dict[SimulationKind, set[str]] = {
    SimulationKind.lunar_lander: set(),
    SimulationKind.microgravity_fire: {
        "zone_isolated",
        "ventilation_on",
        "scrubber_on",
        "extinguisher_equipped",
        "gloves_equipped",
        "co2_charges",
        "isolated_at",
    },
    SimulationKind.crew_vitals: {
        "monitored",
        "oxygen_support",
        "salts_administered",
        "resting",
        "cooling_applied",
    },
    SimulationKind.rover_traverse: {"target", "autopilot"},
}


def preserve_controls(kind: SimulationKind | str, previous: dict[str, Any], state: SimulationState) -> None:
    """Carry mission-set control inputs into a freshly started run."""
    for field in CONTROL_FIELDS.get(SimulationKind(kind), set()):
        if field in previous and field in state.model_fields:
            setattr(state, field, previous[field])


def get_simulation(kind: SimulationKind | str) -> type[Simulation]:
    try:
        return REGISTRY[SimulationKind(kind)]
    except (KeyError, ValueError) as exc:
        raise InvalidInputError(
            f"Unknown simulation '{kind}'.", available=[k.value for k in REGISTRY]
        ) from exc


def create(
    kind: SimulationKind | str, mission_id: str, seed: int, params: dict[str, Any] | None = None
) -> SimulationState:
    return get_simulation(kind).start(mission_id, seed, params)


def load(kind: SimulationKind | str, raw: dict[str, Any]) -> SimulationState:
    return get_simulation(kind).load(raw)


def dump(state: SimulationState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def telemetry(state: SimulationState) -> dict[str, Any]:
    implementation = get_simulation(state.kind)
    telemetry_fn = getattr(implementation, "telemetry", None)
    if telemetry_fn is None:  # pragma: no cover - all sims implement it
        return state.model_dump(mode="json")
    return telemetry_fn(state)


def describe() -> list[dict[str, Any]]:
    return [
        {
            "kind": kind.value,
            "max_duration_seconds": implementation.max_duration,
            "start_cost": START_COST.get(kind, {}),
            "telemetry_fields": sorted(implementation.model.model_fields),
        }
        for kind, implementation in REGISTRY.items()
    ]

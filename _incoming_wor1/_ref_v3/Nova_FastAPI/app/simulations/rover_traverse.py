"""Rover traverse simulation (Mars surface navigation).

Used by *Mars Arrival* and *Martian Guide*. The player can drive manually
(throttle + heading) or engage the waypoint autopilot; the backend integrates
motion, battery, terrain hazards and odometer.

Speeds are game-scaled: real Mars rovers crawl at centimetres per second.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.core.utils import clamp, round_to, seeded_rng
from app.domain.enums import SimulationKind, SimulationStatus
from app.simulations.base import Simulation, SimulationState
from app.systems.scoring import grade_for

MAX_SPEED = 4.0            # m/s (game-scaled)
ACCELERATION = 1.6         # m/s^2
TURN_RATE = 45.0           # deg/s
ARRIVAL_RADIUS = 12.0      # m


class Waypoint(BaseModel):
    id: str
    label: str
    x: float
    y: float
    required: bool = True
    reached: bool = False


class Hazard(BaseModel):
    id: str
    kind: str
    x: float
    y: float
    radius: float
    severity: float = 1.0
    hit: bool = False


class RoverState(SimulationState):
    kind: SimulationKind = SimulationKind.rover_traverse
    x: float = 0.0
    y: float = 0.0
    heading_deg: float = 0.0
    speed: float = 0.0
    throttle: float = 0.0
    battery_pct: float = 100.0
    odometer_m: float = 0.0
    damage: float = 0.0
    target: str | None = None
    autopilot: bool = False
    waypoints: list[Waypoint] = Field(default_factory=list)
    hazards: list[Hazard] = Field(default_factory=list)
    stranded: bool = False
    arrived: list[str] = Field(default_factory=list)


class RoverTraverseSimulation(Simulation):
    kind: ClassVar[SimulationKind] = SimulationKind.rover_traverse
    model: ClassVar[type[SimulationState]] = RoverState
    max_duration: ClassVar[float] = 1_200.0

    @classmethod
    def initialise(cls, mission_id: str, seed: int, params: dict[str, Any]) -> SimulationState:
        rng = seeded_rng(seed, mission_id, "rover")
        raw_waypoints = params.get("waypoints") or [
            {"id": "survey_ridge", "label": "Survey Ridge", "x": 180.0, "y": 120.0},
            {"id": "sample_site", "label": "Sample Site Alpha", "x": 420.0, "y": -60.0},
            {"id": "base_camp", "label": "Base Camp", "x": 0.0, "y": 0.0, "required": False},
        ]
        waypoints = [Waypoint(**wp) for wp in raw_waypoints]

        hazards = [
            Hazard(
                id=f"hazard_{index}",
                kind=kind,
                x=round_to(rng.uniform(-120, 520), 1),
                y=round_to(rng.uniform(-180, 220), 1),
                radius=round_to(rng.uniform(9.0, 20.0), 1),
                severity=round_to(rng.uniform(0.6, 1.4), 2),
            )
            for index, kind in enumerate(("boulder_field", "soft_sand", "crater_rim", "steep_slope"))
        ]

        return RoverState(
            kind=cls.kind,
            mission_id=mission_id,
            seed=seed,
            status=SimulationStatus.ready,
            battery_pct=float(params.get("battery_pct", 100.0)),
            waypoints=waypoints,
            hazards=hazards,
        )

    @classmethod
    def advance(cls, state: SimulationState, command: dict[str, Any], dt: float) -> SimulationState:
        assert isinstance(state, RoverState)
        if state.status != SimulationStatus.running:
            return state

        if command.get("target"):
            target_id = str(command["target"])
            if any(wp.id == target_id for wp in state.waypoints):
                state.target = target_id
                state.autopilot = True
                state.note("autopilot.engaged", target=target_id)
        if command.get("autopilot") is False:
            state.autopilot = False
            state.target = None

        throttle = clamp(float(command.get("throttle", 0.6 if state.autopilot else state.throttle)), 0.0, 1.0)
        manual_heading = command.get("heading")

        steps, h = cls.substeps(dt)
        for _ in range(steps):
            if state.status != SimulationStatus.running:
                break

            desired_heading = state.heading_deg
            if state.autopilot and state.target:
                waypoint = next((wp for wp in state.waypoints if wp.id == state.target), None)
                if waypoint is not None:
                    desired_heading = math.degrees(
                        math.atan2(waypoint.y - state.y, waypoint.x - state.x)
                    )
            elif manual_heading is not None:
                desired_heading = float(manual_heading)

            delta = (desired_heading - state.heading_deg + 180.0) % 360.0 - 180.0
            state.heading_deg = (
                state.heading_deg + clamp(delta, -TURN_RATE * h, TURN_RATE * h)
            ) % 360.0

            damage_factor = clamp(1.0 - state.damage / 150.0, 0.35, 1.0)
            target_speed = throttle * MAX_SPEED * damage_factor
            state.throttle = throttle
            state.speed += clamp(target_speed - state.speed, -ACCELERATION * h, ACCELERATION * h)

            radians = math.radians(state.heading_deg)
            dx = math.cos(radians) * state.speed * h
            dy = math.sin(radians) * state.speed * h
            state.x = round_to(state.x + dx, 3)
            state.y = round_to(state.y + dy, 3)
            travelled = math.hypot(dx, dy)
            state.odometer_m = round_to(state.odometer_m + travelled, 2)

            drain = (0.020 + 0.085 * throttle) * h
            if state.speed < 0.05:
                drain -= 0.012 * h  # solar trickle charge while parked
            state.battery_pct = clamp(state.battery_pct - drain, 0.0, 100.0)

            cls._check_hazards(state)
            cls._check_waypoints(state)

            state.elapsed += h
            state.ticks += 1

            if state.battery_pct <= 0.0:
                state.stranded = True
                cls.finish(state, False, cls._result(state, False, "battery_depleted"))
                break
            if all(wp.reached for wp in state.waypoints if wp.required):
                cls.finish(state, True, cls._result(state, True, "all_waypoints_reached"))
                break
            if state.elapsed >= cls.max_duration:
                cls.finish(state, False, cls._result(state, False, "timeout"))
                break
        return state

    @classmethod
    def _check_hazards(cls, state: RoverState) -> None:
        for hazard in state.hazards:
            if hazard.hit:
                continue
            if math.hypot(state.x - hazard.x, state.y - hazard.y) <= hazard.radius:
                hazard.hit = True
                state.damage = round_to(state.damage + 18.0 * hazard.severity, 2)
                state.battery_pct = clamp(state.battery_pct - 3.0 * hazard.severity, 0.0, 100.0)
                state.note("hazard.hit", hazard=hazard.id, kind=hazard.kind, damage=state.damage)

    @classmethod
    def _check_waypoints(cls, state: RoverState) -> None:
        for waypoint in state.waypoints:
            if waypoint.reached:
                continue
            if math.hypot(state.x - waypoint.x, state.y - waypoint.y) <= ARRIVAL_RADIUS:
                waypoint.reached = True
                state.arrived.append(waypoint.id)
                state.note("waypoint.reached", waypoint=waypoint.id, label=waypoint.label)
                if state.target == waypoint.id:
                    state.autopilot = False
                    state.target = None
                    state.throttle = 0.0

    @classmethod
    def _result(cls, state: RoverState, success: bool, reason: str) -> dict[str, Any]:
        energy = state.battery_pct / 100.0
        integrity = clamp(1.0 - state.damage / 100.0, 0.0, 1.0)
        speed_score = clamp(1.0 - state.elapsed / 600.0, 0.0, 1.0)
        coverage = len(state.arrived) / max(1, len(state.waypoints))
        score_pct = round_to(
            100 * (0.30 * energy + 0.25 * integrity + 0.20 * speed_score + 0.25 * coverage), 2
        )
        if not success:
            score_pct = min(score_pct, 45.0)
        return {
            "success": success,
            "reason": reason,
            "score_pct": score_pct,
            "grade": grade_for(score_pct),
            "battery_pct": round_to(state.battery_pct, 2),
            "odometer_m": state.odometer_m,
            "damage": state.damage,
            "waypoints_reached": list(state.arrived),
            "hazards_hit": [h.id for h in state.hazards if h.hit],
            "elapsed_seconds": round_to(state.elapsed, 2),
        }

    @classmethod
    def telemetry(cls, state: SimulationState) -> dict[str, Any]:
        assert isinstance(state, RoverState)
        target = next((wp for wp in state.waypoints if wp.id == state.target), None)
        return {
            "kind": cls.kind.value,
            "status": state.status.value,
            "elapsed": round_to(state.elapsed, 2),
            "position": {"x": round_to(state.x, 2), "y": round_to(state.y, 2)},
            "heading_deg": round_to(state.heading_deg, 1),
            "speed_mps": round_to(state.speed, 2),
            "throttle": round_to(state.throttle, 2),
            "battery_pct": round_to(state.battery_pct, 2),
            "odometer_m": state.odometer_m,
            "damage": state.damage,
            "autopilot": state.autopilot,
            "target": state.target,
            "distance_to_target": (
                round_to(math.hypot(state.x - target.x, state.y - target.y), 1) if target else None
            ),
            "waypoints": [wp.model_dump(mode="json") for wp in state.waypoints],
            "hazards": [h.model_dump(mode="json") for h in state.hazards],
            "arrived": list(state.arrived),
            "stranded": state.stranded,
            "result": state.result,
            "note": "Rover speeds are game-scaled; real Mars rovers drive far slower.",
        }

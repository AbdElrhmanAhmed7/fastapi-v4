"""Lunar Landing Navigator – a real (if simplified) powered-descent simulation.

Physics
-------
* Moon surface gravity: 1.62 m/s² (NASA Moon Fact Sheet).
* Constant specific impulse descent engine, thrust throttled 0–100 %.
* Thrust vector follows the attitude angle, so tilting trades vertical lift
  for horizontal translation – exactly the trade a real lander pilot makes.
* Fixed 0.1 s integration substeps keep results identical for the same inputs.

Scoring rewards a soft touchdown inside the marked pad with fuel to spare.
This is an educational game simulation, not a flight dynamics model.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from pydantic import Field

from app.core.utils import clamp, round_to, seeded_rng
from app.domain.enums import SimulationKind, SimulationStatus
from app.simulations.base import Simulation, SimulationState
from app.systems.scoring import grade_for

MOON_GRAVITY = 1.62          # m/s^2
DRY_MASS = 1_050.0           # kg
MAX_THRUST = 9_000.0         # N
SPECIFIC_IMPULSE = 311.0     # s
G0 = 9.80665                 # m/s^2
ATTITUDE_RATE = 18.0         # deg/s
MAX_ATTITUDE = 45.0          # deg
PAD_HALF_WIDTH = 30.0        # m
SAFE_VERTICAL_SPEED = 3.0    # m/s
SAFE_HORIZONTAL_SPEED = 1.5  # m/s
SAFE_ATTITUDE = 8.0          # deg


class LanderState(SimulationState):
    kind: SimulationKind = SimulationKind.lunar_lander
    altitude: float = 1_500.0
    vertical_speed: float = -35.0
    position_x: float = 0.0
    horizontal_speed: float = 0.0
    attitude: float = 0.0
    fuel: float = 500.0
    initial_fuel: float = 500.0
    throttle: float = 0.0
    terrain_seed: int = 0
    pad_x: float = 0.0
    touchdown: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def mass(self) -> float:
        return DRY_MASS + max(self.fuel, 0.0)


class LunarLanderSimulation(Simulation):
    kind: ClassVar[SimulationKind] = SimulationKind.lunar_lander
    model: ClassVar[type[SimulationState]] = LanderState
    max_duration: ClassVar[float] = 600.0

    @classmethod
    def initialise(cls, mission_id: str, seed: int, params: dict[str, Any]) -> SimulationState:
        rng = seeded_rng(seed, mission_id, "lander")
        difficulty = str(params.get("difficulty", "standard"))
        spread = {"cadet": 160.0, "standard": 300.0, "veteran": 480.0}.get(difficulty, 300.0)
        descent = {"cadet": -22.0, "standard": -35.0, "veteran": -48.0}.get(difficulty, -35.0)
        fuel = {"cadet": 620.0, "standard": 500.0, "veteran": 410.0}.get(difficulty, 500.0)

        return LanderState(
            kind=cls.kind,
            mission_id=mission_id,
            seed=seed,
            status=SimulationStatus.ready,
            altitude=float(params.get("altitude", 1_500.0)),
            vertical_speed=descent,
            position_x=round_to(rng.uniform(-spread, spread), 2),
            horizontal_speed=round_to(rng.uniform(-8.0, 8.0), 2),
            fuel=fuel,
            initial_fuel=fuel,
            terrain_seed=rng.randint(0, 10_000),
            pad_x=0.0,
        )

    # -- terrain ---------------------------------------------------------
    @classmethod
    def terrain_height(cls, state: LanderState, x: float) -> float:
        """Smooth pseudo-random terrain, flattened around the landing pad."""
        if abs(x - state.pad_x) <= PAD_HALF_WIDTH:
            return 0.0
        s = state.terrain_seed
        rolling = (
            18.0 * math.sin((x + s) / 120.0)
            + 9.0 * math.sin((x - s) / 47.0)
            + 4.0 * math.sin((x + 2 * s) / 19.0)
        )
        return round_to(max(0.0, rolling + 12.0), 3)

    @classmethod
    def terrain_profile(cls, state: LanderState, span: float = 600.0, samples: int = 61) -> list[dict[str, float]]:
        step = (2 * span) / (samples - 1)
        return [
            {"x": round_to(-span + i * step, 2), "height": cls.terrain_height(state, -span + i * step)}
            for i in range(samples)
        ]

    # -- integration -----------------------------------------------------
    @classmethod
    def advance(cls, state: SimulationState, command: dict[str, Any], dt: float) -> SimulationState:
        assert isinstance(state, LanderState)
        if state.status != SimulationStatus.running:
            return state

        throttle = clamp(float(command.get("throttle", state.throttle)), 0.0, 1.0)
        attitude_input = clamp(float(command.get("attitude", 0.0)), -1.0, 1.0)
        state.throttle = throttle

        steps, h = cls.substeps(dt)
        for _ in range(steps):
            if state.status != SimulationStatus.running:
                break
            state.attitude = clamp(
                state.attitude + attitude_input * ATTITUDE_RATE * h, -MAX_ATTITUDE, MAX_ATTITUDE
            )

            thrust = MAX_THRUST * throttle if state.fuel > 0 else 0.0
            if thrust > 0:
                burn = thrust * h / (SPECIFIC_IMPULSE * G0)
                if burn > state.fuel:
                    thrust *= state.fuel / burn
                    burn = state.fuel
                state.fuel = max(0.0, state.fuel - burn)

            radians = math.radians(state.attitude)
            accel_y = thrust * math.cos(radians) / state.mass - MOON_GRAVITY
            accel_x = thrust * math.sin(radians) / state.mass

            state.vertical_speed += accel_y * h
            state.horizontal_speed += accel_x * h
            state.altitude += state.vertical_speed * h
            state.position_x += state.horizontal_speed * h
            state.elapsed += h
            state.ticks += 1

            ground = cls.terrain_height(state, state.position_x)
            if state.altitude <= ground:
                state.altitude = ground
                cls._touchdown(state)
                break
            if state.elapsed >= cls.max_duration:
                cls.finish(state, False, {"reason": "timeout", "score_pct": 0.0, "grade": "D"})
                break

        state.warnings = cls._warnings(state)
        return state

    @classmethod
    def _touchdown(cls, state: LanderState) -> None:
        vertical = abs(state.vertical_speed)
        horizontal = abs(state.horizontal_speed)
        tilt = abs(state.attitude)
        offset = abs(state.position_x - state.pad_x)
        on_pad = offset <= PAD_HALF_WIDTH
        soft = vertical <= SAFE_VERTICAL_SPEED and horizontal <= SAFE_HORIZONTAL_SPEED
        upright = tilt <= SAFE_ATTITUDE
        success = on_pad and soft and upright

        softness = clamp(1.0 - vertical / (SAFE_VERTICAL_SPEED * 3), 0.0, 1.0)
        precision = clamp(1.0 - offset / (PAD_HALF_WIDTH * 4), 0.0, 1.0)
        drift = clamp(1.0 - horizontal / (SAFE_HORIZONTAL_SPEED * 4), 0.0, 1.0)
        posture = clamp(1.0 - tilt / (SAFE_ATTITUDE * 3), 0.0, 1.0)
        fuel_ratio = state.fuel / state.initial_fuel if state.initial_fuel else 0.0

        score_pct = round_to(
            100.0
            * (0.34 * softness + 0.26 * precision + 0.14 * drift + 0.11 * posture + 0.15 * fuel_ratio),
            2,
        )
        if not success:
            score_pct = round_to(min(score_pct, 45.0), 2)

        result = {
            "success": success,
            "reason": (
                "nominal_landing"
                if success
                else "off_pad"
                if not on_pad
                else "hard_touchdown"
                if not soft
                else "excessive_tilt"
            ),
            "vertical_speed": round_to(state.vertical_speed, 3),
            "horizontal_speed": round_to(state.horizontal_speed, 3),
            "attitude": round_to(state.attitude, 2),
            "pad_offset_m": round_to(offset, 2),
            "fuel_remaining": round_to(state.fuel, 2),
            "fuel_used": round_to(state.initial_fuel - state.fuel, 2),
            "fuel_efficiency_pct": round_to(100 * fuel_ratio, 2),
            "elapsed_seconds": round_to(state.elapsed, 2),
            "score_pct": score_pct,
            "grade": grade_for(score_pct),
            "components": {
                "softness": round_to(softness, 3),
                "precision": round_to(precision, 3),
                "drift": round_to(drift, 3),
                "posture": round_to(posture, 3),
                "fuel": round_to(fuel_ratio, 3),
            },
        }
        state.touchdown = result
        cls.finish(state, success, result)

    @classmethod
    def _warnings(cls, state: LanderState) -> list[str]:
        warnings: list[str] = []
        if state.fuel <= 0:
            warnings.append("fuel_depleted")
        elif state.fuel < 0.15 * state.initial_fuel:
            warnings.append("fuel_low")
        if state.vertical_speed < -60:
            warnings.append("descent_too_fast")
        if abs(state.attitude) > 30:
            warnings.append("attitude_extreme")
        if abs(state.position_x - state.pad_x) > PAD_HALF_WIDTH * 3:
            warnings.append("off_course")
        return warnings

    # -- telemetry -------------------------------------------------------
    @classmethod
    def telemetry(cls, state: SimulationState) -> dict[str, Any]:
        assert isinstance(state, LanderState)
        ground = cls.terrain_height(state, state.position_x)
        return {
            "kind": cls.kind.value,
            "status": state.status.value,
            "elapsed": round_to(state.elapsed, 2),
            "altitude_agl": round_to(state.altitude - ground, 2),
            "altitude_msl": round_to(state.altitude, 2),
            "vertical_speed": round_to(state.vertical_speed, 2),
            "horizontal_speed": round_to(state.horizontal_speed, 2),
            "position_x": round_to(state.position_x, 2),
            "attitude": round_to(state.attitude, 2),
            "throttle": round_to(state.throttle, 3),
            "fuel": round_to(state.fuel, 2),
            "fuel_pct": round_to(100 * state.fuel / state.initial_fuel, 1) if state.initial_fuel else 0.0,
            "pad_x": state.pad_x,
            "pad_half_width": PAD_HALF_WIDTH,
            "distance_to_pad": round_to(abs(state.position_x - state.pad_x), 2),
            "warnings": state.warnings,
            "result": state.result,
            "limits": {
                "safe_vertical_speed": SAFE_VERTICAL_SPEED,
                "safe_horizontal_speed": SAFE_HORIZONTAL_SPEED,
                "safe_attitude": SAFE_ATTITUDE,
                "gravity": MOON_GRAVITY,
            },
        }

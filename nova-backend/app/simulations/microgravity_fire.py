"""Flame in Freefall – microgravity fire behaviour.

Why microgravity fire is different (NASA Saffire / FLEX experiments):
* without buoyancy there is no upward convection plume, so flames become
  roughly **spherical** and burn much cooler and slower than on Earth,
* they are diffusion-limited: airflow (ventilation) matters more than "up",
* they can survive at lower oxygen concentrations than a terrestrial flame,
* CO2 flooding suppresses the flame but also displaces cabin oxygen, so the
  crew's atmosphere must be scrubbed afterwards.

The numbers below are a tuned educational model, not CFD.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core.utils import clamp, round_to
from app.domain.enums import SimulationKind, SimulationStatus
from app.simulations.base import Simulation, SimulationState
from app.systems.scoring import grade_for

NORMAL_O2 = 20.9
MIN_SAFE_O2 = 12.0
MAX_SAFE_TEMP = 60.0
MAX_SAFE_CO2 = 8_000.0


class FireState(SimulationState):
    kind: SimulationKind = SimulationKind.microgravity_fire
    # cabin atmosphere
    oxygen_pct: float = NORMAL_O2
    co2_ppm: float = 450.0
    smoke: float = 0.0
    cabin_temp_c: float = 22.0
    # fire
    intensity: float = 0.35
    flame_radius_m: float = 0.12
    flame_temp_k: float = 720.0
    suppressed: bool = False
    # crew actions / hardware
    zone_isolated: bool = False
    ventilation_on: bool = True
    scrubber_on: bool = False
    extinguisher_equipped: bool = False
    gloves_equipped: bool = False
    co2_charges: float = 3.0
    co2_used: float = 0.0
    blanket_deployed: bool = False
    time_to_suppress: float | None = None
    atmosphere_safe: bool = False
    isolated_at: float | None = None


class MicrogravityFireSimulation(Simulation):
    kind: ClassVar[SimulationKind] = SimulationKind.microgravity_fire
    model: ClassVar[type[SimulationState]] = FireState
    max_duration: ClassVar[float] = 480.0

    @classmethod
    def initialise(cls, mission_id: str, seed: int, params: dict[str, Any]) -> SimulationState:
        intensity = {"cadet": 0.25, "standard": 0.35, "veteran": 0.5}.get(
            str(params.get("difficulty", "standard")), 0.35
        )
        return FireState(
            kind=cls.kind,
            mission_id=mission_id,
            seed=seed,
            status=SimulationStatus.ready,
            intensity=intensity,
            co2_charges=float(params.get("co2_charges", 3.0)),
        )

    @classmethod
    def advance(cls, state: SimulationState, command: dict[str, Any], dt: float) -> SimulationState:
        assert isinstance(state, FireState)
        if state.status != SimulationStatus.running:
            return state

        discharge = clamp(float(command.get("co2", 0.0)), 0.0, 1.0)
        if "ventilation" in command:
            state.ventilation_on = bool(command["ventilation"])
        if "scrubber" in command:
            state.scrubber_on = bool(command["scrubber"])
        if command.get("blanket") and not state.blanket_deployed:
            state.blanket_deployed = True
            state.note("blanket.deployed", intensity=round_to(state.intensity, 3))

        steps, h = cls.substeps(dt)
        for _ in range(steps):
            if state.status != SimulationStatus.running:
                break
            cls._step(state, discharge, h)
            state.elapsed += h
            state.ticks += 1
            if state.elapsed >= cls.max_duration:
                cls.finish(state, False, cls._result(state, success=False, reason="timeout"))
                break
        return state

    @classmethod
    def _step(cls, state: FireState, discharge: float, h: float) -> None:
        # --- suppression -------------------------------------------------
        if discharge > 0 and state.co2_charges > 0 and state.extinguisher_equipped:
            used = min(state.co2_charges, discharge * h)
            state.co2_charges = round_to(state.co2_charges - used, 4)
            state.co2_used = round_to(state.co2_used + used, 4)
            state.intensity -= 1.15 * used
            state.oxygen_pct -= 1.9 * used          # CO2 displaces breathable O2
            state.co2_ppm += 9_000 * used
            state.cabin_temp_c -= 2.5 * used

        if state.blanket_deployed and state.intensity < 0.35:
            state.intensity -= 0.10 * h

        # --- combustion --------------------------------------------------
        if state.intensity > 0:
            oxygen_factor = clamp((state.oxygen_pct - 13.0) / (NORMAL_O2 - 13.0), 0.0, 1.4)
            airflow = 1.45 if (state.ventilation_on and not state.zone_isolated) else 0.55
            growth = 0.020 * oxygen_factor * airflow
            decay = 0.012 if state.zone_isolated else 0.004
            state.intensity = clamp(state.intensity + (growth - decay) * h, 0.0, 1.0)

            state.oxygen_pct -= 0.055 * state.intensity * h
            state.co2_ppm += 850 * state.intensity * h
            state.smoke = clamp(state.smoke + 0.020 * state.intensity * h, 0.0, 1.0)
            state.cabin_temp_c += 0.42 * state.intensity * h
            # Spherical, diffusion-limited flame: cooler than an Earth flame.
            state.flame_radius_m = round_to(0.08 + 0.22 * state.intensity, 3)
            state.flame_temp_k = round_to(620 + 340 * state.intensity, 1)
        else:
            state.flame_radius_m = 0.0
            state.flame_temp_k = round_to(max(295.0, state.flame_temp_k - 25 * h), 1)

        # --- life support ------------------------------------------------
        if state.scrubber_on:
            state.co2_ppm = max(400.0, state.co2_ppm - 420 * h)
            state.smoke = clamp(state.smoke - 0.055 * h, 0.0, 1.0)
            state.oxygen_pct = min(NORMAL_O2, state.oxygen_pct + 0.10 * h)
            state.cabin_temp_c = max(21.0, state.cabin_temp_c - 0.30 * h)
        elif not state.ventilation_on:
            state.co2_ppm += 25 * h  # crew breathing with no airflow

        state.oxygen_pct = clamp(state.oxygen_pct, 0.0, NORMAL_O2)
        state.cabin_temp_c = max(15.0, state.cabin_temp_c)

        # --- outcomes ----------------------------------------------------
        if not state.suppressed and state.intensity <= 0.02:
            state.suppressed = True
            state.intensity = 0.0
            state.time_to_suppress = round_to(state.elapsed, 2)
            state.note("fire.suppressed", elapsed=state.time_to_suppress)

        state.atmosphere_safe = (
            state.suppressed
            and state.smoke <= 0.15
            and state.co2_ppm <= 5_000
            and state.oxygen_pct >= 18.5
        )

        if state.oxygen_pct < MIN_SAFE_O2:
            cls.finish(state, False, cls._result(state, success=False, reason="cabin_oxygen_critical"))
        elif state.cabin_temp_c > MAX_SAFE_TEMP:
            cls.finish(state, False, cls._result(state, success=False, reason="cabin_overheat"))
        elif state.atmosphere_safe:
            cls.finish(state, True, cls._result(state, success=True, reason="fire_out_atmosphere_safe"))

    @classmethod
    def _result(cls, state: FireState, *, success: bool, reason: str) -> dict[str, Any]:
        speed = clamp(1.0 - (state.time_to_suppress or cls.max_duration) / 240.0, 0.0, 1.0)
        atmosphere = clamp((state.oxygen_pct - MIN_SAFE_O2) / (NORMAL_O2 - MIN_SAFE_O2), 0.0, 1.0)
        economy = clamp(1.0 - state.co2_used / 3.0, 0.0, 1.0)
        isolation = 1.0 if state.isolated_at is not None and state.isolated_at < 45 else 0.4
        cleanliness = clamp(1.0 - state.smoke, 0.0, 1.0)

        score_pct = round_to(
            100 * (0.30 * speed + 0.25 * atmosphere + 0.15 * economy + 0.15 * isolation + 0.15 * cleanliness),
            2,
        )
        if not success:
            score_pct = min(score_pct, 40.0)
        return {
            "success": success,
            "reason": reason,
            "score_pct": score_pct,
            "grade": grade_for(score_pct),
            "time_to_suppress": state.time_to_suppress,
            "oxygen_pct": round_to(state.oxygen_pct, 2),
            "co2_ppm": round_to(state.co2_ppm, 1),
            "smoke": round_to(state.smoke, 3),
            "co2_used": round_to(state.co2_used, 3),
            "elapsed_seconds": round_to(state.elapsed, 2),
        }

    @classmethod
    def telemetry(cls, state: SimulationState) -> dict[str, Any]:
        assert isinstance(state, FireState)
        return {
            "kind": cls.kind.value,
            "status": state.status.value,
            "elapsed": round_to(state.elapsed, 2),
            "fire": {
                "intensity": round_to(state.intensity, 3),
                "radius_m": state.flame_radius_m,
                "temperature_k": state.flame_temp_k,
                "shape": "spherical (microgravity, diffusion limited)",
                "suppressed": state.suppressed,
            },
            "cabin": {
                "oxygen_pct": round_to(state.oxygen_pct, 2),
                "co2_ppm": round_to(state.co2_ppm, 1),
                "smoke": round_to(state.smoke, 3),
                "temperature_c": round_to(state.cabin_temp_c, 2),
                "atmosphere_safe": state.atmosphere_safe,
            },
            "systems": {
                "zone_isolated": state.zone_isolated,
                "ventilation_on": state.ventilation_on,
                "scrubber_on": state.scrubber_on,
                "extinguisher_equipped": state.extinguisher_equipped,
                "gloves_equipped": state.gloves_equipped,
                "co2_charges": round_to(state.co2_charges, 2),
            },
            "warnings": cls._warnings(state),
            "result": state.result,
        }

    @classmethod
    def _warnings(cls, state: FireState) -> list[str]:
        warnings: list[str] = []
        if state.oxygen_pct < 16:
            warnings.append("oxygen_low")
        if state.co2_ppm > 5_000:
            warnings.append("co2_high")
        if state.cabin_temp_c > 45:
            warnings.append("cabin_hot")
        if state.co2_charges <= 0.2:
            warnings.append("extinguisher_empty")
        if state.ventilation_on and not state.zone_isolated:
            warnings.append("airflow_feeding_flame")
        return warnings

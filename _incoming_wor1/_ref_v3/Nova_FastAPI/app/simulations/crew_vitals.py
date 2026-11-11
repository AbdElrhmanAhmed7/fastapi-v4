"""Health Monitoring – a *fictional* crew vitals simulation.

IMPORTANT: this is an educational game scenario with invented numbers. It is
not medical guidance, not a diagnostic tool and not a training device. Every
API response that carries vitals repeats that disclaimer.

Scenario: after a long EVA the crew member shows simulated dehydration and
mild hypoxia. The player must monitor, fetch the right equipment and apply
support in a sensible order; the model then converges to a stable state.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from app.core.utils import clamp, round_to
from app.domain.enums import SIMULATION_DISCLAIMER, SimulationKind, SimulationStatus
from app.simulations.base import Simulation, SimulationState
from app.systems.scoring import grade_for

SAFE_BANDS = {
    "spo2_pct": (94.0, 100.0),
    "heart_rate_bpm": (55.0, 100.0),
    "temperature_c": (36.0, 37.6),
    "systolic_mmhg": (100.0, 135.0),
    "respiration_rpm": (12.0, 20.0),
}
STABLE_SECONDS_REQUIRED = 30.0


class VitalsState(SimulationState):
    kind: SimulationKind = SimulationKind.crew_vitals
    crew_member: str = "Crew Specialist R. Haddad"
    scenario: str = "post_eva_dehydration_hypoxia"
    # vitals
    spo2_pct: float = 89.0
    heart_rate_bpm: float = 112.0
    temperature_c: float = 37.9
    systolic_mmhg: float = 104.0
    diastolic_mmhg: float = 68.0
    respiration_rpm: float = 22.0
    hydration: float = 0.45
    # interventions (set by mission actions)
    monitored: bool = False
    oxygen_support: bool = False
    salts_administered: bool = False
    resting: bool = False
    cooling_applied: bool = False
    stable_seconds: float = 0.0
    treatments: list[str] = Field(default_factory=list)
    disclaimer: str = SIMULATION_DISCLAIMER

    def vitals(self) -> dict[str, float]:
        return {
            "spo2_pct": round_to(self.spo2_pct, 1),
            "heart_rate_bpm": round_to(self.heart_rate_bpm, 1),
            "temperature_c": round_to(self.temperature_c, 2),
            "systolic_mmhg": round_to(self.systolic_mmhg, 1),
            "diastolic_mmhg": round_to(self.diastolic_mmhg, 1),
            "respiration_rpm": round_to(self.respiration_rpm, 1),
            "hydration_index": round_to(self.hydration, 3),
        }

    def in_band(self) -> bool:
        values = self.vitals()
        return all(
            low <= values[name] <= high for name, (low, high) in SAFE_BANDS.items()
        )


class CrewVitalsSimulation(Simulation):
    kind: ClassVar[SimulationKind] = SimulationKind.crew_vitals
    model: ClassVar[type[SimulationState]] = VitalsState
    max_duration: ClassVar[float] = 600.0

    @classmethod
    def initialise(cls, mission_id: str, seed: int, params: dict[str, Any]) -> SimulationState:
        severity = {"cadet": 0.7, "standard": 1.0, "veteran": 1.25}.get(
            str(params.get("difficulty", "standard")), 1.0
        )
        return VitalsState(
            kind=cls.kind,
            mission_id=mission_id,
            seed=seed,
            status=SimulationStatus.ready,
            spo2_pct=round_to(94.0 - 5.0 * severity, 1),
            heart_rate_bpm=round_to(90.0 + 22.0 * severity, 1),
            temperature_c=round_to(37.0 + 0.9 * severity, 2),
            hydration=round_to(clamp(0.62 - 0.17 * severity, 0.1, 0.9), 3),
            crew_member=str(params.get("crew_member", "Crew Specialist R. Haddad")),
        )

    @classmethod
    def advance(cls, state: SimulationState, command: dict[str, Any], dt: float) -> SimulationState:
        assert isinstance(state, VitalsState)
        if state.status != SimulationStatus.running:
            return state

        for flag in ("oxygen_support", "salts_administered", "resting", "cooling_applied", "monitored"):
            if flag in command:
                value = bool(command[flag])
                if value and not getattr(state, flag):
                    state.treatments.append(flag)
                    state.note(f"treatment.{flag}", elapsed=round_to(state.elapsed, 1))
                setattr(state, flag, value)

        steps, h = cls.substeps(dt)
        for _ in range(steps):
            if state.status != SimulationStatus.running:
                break
            cls._step(state, h)
            state.elapsed += h
            state.ticks += 1
            if state.elapsed >= cls.max_duration:
                cls.finish(state, False, cls._result(state, False, "timeout"))
                break
        return state

    @classmethod
    def _approach(cls, current: float, target: float, rate: float, h: float) -> float:
        delta = target - current
        step = clamp(delta, -rate * h, rate * h)
        return current + step

    @classmethod
    def _step(cls, state: VitalsState, h: float) -> None:
        # Oxygen support pulls saturation up; without it the crew stays low.
        spo2_target = 97.5 if state.oxygen_support else 89.5
        state.spo2_pct = cls._approach(state.spo2_pct, spo2_target, 0.55, h)

        hydration_target = 0.92 if state.salts_administered else 0.42
        state.hydration = clamp(
            cls._approach(state.hydration, hydration_target, 0.012, h), 0.0, 1.0
        )

        hr_target = 72.0 + 34.0 * (1.0 - state.hydration) + max(0.0, (95.0 - state.spo2_pct)) * 2.2
        if state.resting:
            hr_target -= 6.0
        state.heart_rate_bpm = cls._approach(state.heart_rate_bpm, hr_target, 2.2, h)

        temp_target = 36.8 + 1.1 * (1.0 - state.hydration) - (0.35 if state.cooling_applied else 0.0)
        state.temperature_c = cls._approach(state.temperature_c, temp_target, 0.035, h)

        state.systolic_mmhg = cls._approach(
            state.systolic_mmhg, 100.0 + 26.0 * state.hydration, 1.4, h
        )
        state.diastolic_mmhg = cls._approach(
            state.diastolic_mmhg, 64.0 + 16.0 * state.hydration, 1.0, h
        )
        state.respiration_rpm = cls._approach(
            state.respiration_rpm, 13.0 + max(0.0, 96.0 - state.spo2_pct) * 1.1, 0.6, h
        )

        if state.in_band():
            state.stable_seconds = round_to(state.stable_seconds + h, 2)
        else:
            state.stable_seconds = max(0.0, state.stable_seconds - h * 0.5)

        if state.spo2_pct < 82.0:
            cls.finish(state, False, cls._result(state, False, "critical_desaturation"))
        elif state.stable_seconds >= STABLE_SECONDS_REQUIRED:
            cls.finish(state, True, cls._result(state, True, "crew_stable"))

    @classmethod
    def _result(cls, state: VitalsState, success: bool, reason: str) -> dict[str, Any]:
        speed = clamp(1.0 - state.elapsed / 300.0, 0.0, 1.0)
        physiology = clamp((state.spo2_pct - 85.0) / 12.5, 0.0, 1.0)
        hydration = state.hydration
        protocol = 1.0 if state.treatments[:1] == ["monitored"] else 0.6
        score_pct = round_to(
            100 * (0.30 * speed + 0.28 * physiology + 0.24 * hydration + 0.18 * protocol), 2
        )
        if not success:
            score_pct = min(score_pct, 40.0)
        return {
            "success": success,
            "reason": reason,
            "score_pct": score_pct,
            "grade": grade_for(score_pct),
            "vitals": state.vitals(),
            "stable_seconds": state.stable_seconds,
            "treatments": list(state.treatments),
            "elapsed_seconds": round_to(state.elapsed, 2),
            "disclaimer": SIMULATION_DISCLAIMER,
        }

    @classmethod
    def telemetry(cls, state: SimulationState) -> dict[str, Any]:
        assert isinstance(state, VitalsState)
        vitals = state.vitals()
        return {
            "kind": cls.kind.value,
            "status": state.status.value,
            "elapsed": round_to(state.elapsed, 2),
            "crew_member": state.crew_member,
            "scenario": state.scenario,
            "vitals": vitals,
            "bands": {name: {"min": low, "max": high} for name, (low, high) in SAFE_BANDS.items()},
            "out_of_band": [
                name
                for name, (low, high) in SAFE_BANDS.items()
                if not low <= vitals[name] <= high
            ],
            "interventions": {
                "monitored": state.monitored,
                "oxygen_support": state.oxygen_support,
                "salts_administered": state.salts_administered,
                "resting": state.resting,
                "cooling_applied": state.cooling_applied,
            },
            "stable_seconds": state.stable_seconds,
            "stable_required": STABLE_SECONDS_REQUIRED,
            "treatments": list(state.treatments),
            "result": state.result,
            "disclaimer": SIMULATION_DISCLAIMER,
        }

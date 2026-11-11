"""Common simulation contracts.

Simulations are deterministic, server-authoritative and stored as plain JSON
inside ``GameState.simulations[mission_id]`` so a save game contains everything
needed to resume a run.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.core.utils import clamp
from app.domain.enums import SimulationKind, SimulationStatus

MAX_TICK_SECONDS = 1.0
MAX_SUBSTEP = 0.1


class SimulationState(BaseModel):
    kind: SimulationKind
    status: SimulationStatus = SimulationStatus.ready
    seed: int = 0
    elapsed: float = 0.0
    ticks: int = 0
    mission_id: str = ""
    log: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None

    def note(self, event: str, **data: Any) -> None:
        self.log.append({"t": round(self.elapsed, 2), "event": event, **data})
        if len(self.log) > 120:
            del self.log[:-120]


class Simulation:
    """Base class: subclasses implement ``initialise`` and ``advance``."""

    kind: ClassVar[SimulationKind]
    model: ClassVar[type[SimulationState]]
    max_duration: ClassVar[float] = 900.0

    @classmethod
    def start(cls, mission_id: str, seed: int, params: dict[str, Any] | None = None) -> SimulationState:
        state = cls.initialise(mission_id, seed, params or {})
        state.status = SimulationStatus.running
        state.note("simulation.started", kind=cls.kind.value)
        return state

    @classmethod
    def initialise(cls, mission_id: str, seed: int, params: dict[str, Any]) -> SimulationState:
        raise NotImplementedError

    @classmethod
    def advance(cls, state: SimulationState, command: dict[str, Any], dt: float) -> SimulationState:
        raise NotImplementedError

    @classmethod
    def load(cls, raw: dict[str, Any]) -> SimulationState:
        return cls.model.model_validate(raw)

    @classmethod
    def dump(cls, state: SimulationState) -> dict[str, Any]:
        return state.model_dump(mode="json")

    # -- helpers --------------------------------------------------------
    @staticmethod
    def substeps(dt: float) -> tuple[int, float]:
        total = clamp(float(dt), 0.0, MAX_TICK_SECONDS)
        steps = max(1, int(round(total / MAX_SUBSTEP)))
        return steps, total / steps

    @classmethod
    def finish(cls, state: SimulationState, success: bool, result: dict[str, Any]) -> None:
        state.status = SimulationStatus.succeeded if success else SimulationStatus.failed
        state.result = result
        state.note("simulation.finished", success=success, **result)

"""Suit oxygen model.

Server-authoritative and deterministic: the frontend reports how the avatar is
moving, the backend decides how much oxygen that costs. ``dt`` is clamped so a
modified client cannot fast-forward (or freeze) life support.

The model is an *educational simplification*: real EVA consumables depend on
metabolic rate, suit pressure and pre-breathe protocols.
"""

from __future__ import annotations

from typing import Any

from app.core.utils import clamp, round_to
from app.domain.catalog import Catalog
from app.domain.enums import Activity, Difficulty
from app.domain.state import GameState
from app.systems.resource_manager import ResourceManager

#: Oxygen units consumed per second, per activity (simplified).
BASE_RATES: dict[str, float] = {
    Activity.idle.value: 0.010,
    Activity.walk.value: 0.030,
    Activity.run.value: 0.065,
    Activity.eva.value: 0.050,
    Activity.rover.value: 0.008,
}

DIFFICULTY_MULTIPLIER: dict[str, float] = {
    Difficulty.cadet.value: 0.65,
    Difficulty.standard.value: 1.0,
    Difficulty.veteran.value: 1.35,
}

WARNING_LEVEL = 25
CRITICAL_LEVEL = 10


class OxygenSystem:
    def __init__(self, state: GameState, catalog: Catalog) -> None:
        self.state = state
        self.resources = ResourceManager(state, catalog)

    def rate_for(self, activity: str) -> float:
        base = BASE_RATES.get(activity, BASE_RATES[Activity.idle.value])
        multiplier = DIFFICULTY_MULTIPLIER.get(self.state.difficulty.value, 1.0)
        # A damaged suit leaks. Below 60% integrity the loss grows quickly.
        integrity_factor = 1.0 + max(0.0, (60 - self.state.suit.integrity) / 100.0)
        # Pressurised interiors (training, habitat, lab) are cheaper than EVA.
        sheltered = self.state.environment.value in {
            "training_facility",
            "earth_analog",
            "medical_bay",
            "mars_lab",
            "simulation_center",
            "earth_observation",
        }
        shelter_factor = 0.35 if sheltered and activity != Activity.eva.value else 1.0
        return round_to(base * multiplier * integrity_factor * shelter_factor, 5)

    def tick(self, dt_seconds: float, activity: str = "idle") -> dict[str, Any]:
        dt = clamp(float(dt_seconds), 0.0, 5.0)
        activity = activity if activity in BASE_RATES else Activity.idle.value
        rate = self.rate_for(activity)
        spend = rate * dt

        before = self.resources.get("oxygen")
        # Fractional consumption is accumulated so slow ticks still cost oxygen.
        pending = float(self.state.world.get("_oxygen_fraction", 0.0)) + spend
        whole = int(pending)
        self.state.world["_oxygen_fraction"] = round_to(pending - whole, 5)
        if whole > 0:
            self.resources.apply({"oxygen": -whole})
        after = self.resources.get("oxygen")

        self.state.suit.last_activity = activity
        if activity in {Activity.eva.value, Activity.run.value}:
            self.state.stats.eva_seconds = round_to(self.state.stats.eva_seconds + dt, 2)

        alarm: str | None = None
        if after <= CRITICAL_LEVEL:
            alarm = "critical"
            self.state.suit.integrity = clamp(self.state.suit.integrity - int(dt * 0.5), 0, 100)
        elif after <= WARNING_LEVEL:
            alarm = "warning"
        self.state.suit.oxygen_alarm = alarm

        return {
            "dt_seconds": dt,
            "activity": activity,
            "rate_per_second": rate,
            "oxygen_before": before,
            "oxygen_after": after,
            "alarm": alarm,
            "suit_integrity": self.state.suit.integrity,
        }

    def set_eva(self, active: bool) -> dict[str, Any]:
        self.state.suit.eva_active = active
        self.state.set_flag("eva_active", active)
        return {"eva_active": active, "oxygen": self.resources.get("oxygen")}

    def damage_suit(self, amount: int) -> int:
        self.state.suit.integrity = clamp(self.state.suit.integrity - amount, 0, 100)
        return self.state.suit.integrity

    def repair_suit(self, amount: int = 30) -> int:
        self.state.suit.integrity = clamp(self.state.suit.integrity + amount, 0, 100)
        return self.state.suit.integrity

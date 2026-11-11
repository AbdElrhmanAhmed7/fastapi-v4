"""Resource pool management (water, oxygen, power, fuel).

All mutations are **validated before anything is applied**, so a failed action
can never leave the player with half-spent resources.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InsufficientResourceError
from app.core.utils import clamp
from app.domain.catalog import Catalog
from app.domain.state import GameState


class ResourceManager:
    def __init__(self, state: GameState, catalog: Catalog) -> None:
        self.state = state
        self.catalog = catalog

    # -- reads --------------------------------------------------------------
    def get(self, name: str) -> int:
        return int(self.state.resources.get(name, 0))

    def cap(self, name: str) -> int:
        return int(self.state.resource_caps.get(name, self.catalog.resources[name].cap))

    def all(self) -> dict[str, int]:
        return dict(self.state.resources)

    def ratio(self, name: str) -> float:
        cap = self.cap(name)
        return self.get(name) / cap if cap else 0.0

    def critical(self) -> list[str]:
        return [
            rid
            for rid, definition in self.catalog.resources.items()
            if self.get(rid) <= definition.critical_below
        ]

    # -- validation ---------------------------------------------------------
    def shortfalls(self, deltas: dict[str, int]) -> list[dict[str, Any]]:
        """Return every resource that cannot cover the requested spend."""
        problems: list[dict[str, Any]] = []
        for name, delta in deltas.items():
            if name not in self.catalog.resources:
                problems.append({"resource": name, "reason": "unknown_resource"})
                continue
            if delta < 0 and self.get(name) < -delta:
                problems.append(
                    {
                        "resource": name,
                        "required": -delta,
                        "available": self.get(name),
                        "reason": "insufficient",
                    }
                )
        return problems

    def validate(self, deltas: dict[str, int]) -> None:
        problems = self.shortfalls(deltas)
        if problems:
            first = problems[0]
            raise InsufficientResourceError(
                f"Not enough {first['resource']}: needs {first.get('required', '?')}, "
                f"has {first.get('available', 0)}.",
                shortfalls=problems,
            )

    # -- writes -------------------------------------------------------------
    def apply(self, deltas: dict[str, int]) -> list[dict[str, Any]]:
        """Atomically apply resource deltas. Positive values clamp at the cap."""
        self.validate(deltas)
        changes: list[dict[str, Any]] = []
        for name, delta in deltas.items():
            if delta == 0:
                continue
            before = self.get(name)
            after = clamp(before + delta, 0, self.cap(name))
            self.state.resources[name] = after
            effective = after - before
            if effective < 0:
                bucket = self.state.stats.resources_consumed
                bucket[name] = bucket.get(name, 0) + (-effective)
            elif effective > 0:
                bucket = self.state.stats.resources_recovered
                bucket[name] = bucket.get(name, 0) + effective
            changes.append(
                {"resource": name, "before": before, "after": after, "delta": effective}
            )
        return changes

    def consume(self, name: str, amount: int) -> int:
        if amount <= 0:
            raise ValueError("Consumed amount must be positive")
        self.apply({name: -amount})
        return self.get(name)

    def add(self, name: str, amount: int) -> int:
        if amount <= 0:
            raise ValueError("Added amount must be positive")
        self.apply({name: amount})
        return self.get(name)

    def refill(self, name: str) -> int:
        return self.add(name, max(0, self.cap(name) - self.get(name)))

"""Inventory, tool condition and item usage.

Tools are *durable*: every use wears them down. Condition feeds the Mission
Continuity System – a neglected scanner will malfunction on Mars.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import EquipmentFailureError, InvalidInputError, MissingItemError
from app.core.utils import clamp
from app.domain.catalog import Catalog
from app.domain.enums import ItemCategory
from app.domain.state import EquipmentState, GameState
from app.systems.resource_manager import ResourceManager

#: Below this condition a durable tool can refuse to work.
FAILURE_THRESHOLD = 20
#: Below this condition the HUD shows a maintenance warning.
WARNING_THRESHOLD = 45


class InventorySystem:
    def __init__(self, state: GameState, catalog: Catalog) -> None:
        self.state = state
        self.catalog = catalog
        self.resources = ResourceManager(state, catalog)

    # -- reads --------------------------------------------------------------
    def count(self, item_id: str) -> int:
        return int(self.state.inventory.get(item_id, 0))

    def has(self, item_id: str, quantity: int = 1) -> bool:
        return self.count(item_id) >= quantity

    def all(self) -> dict[str, int]:
        return dict(self.state.inventory)

    def condition(self, item_id: str) -> int:
        equipment = self.state.equipment.get(item_id)
        return equipment.condition if equipment else 100

    def degraded_tools(self) -> list[dict[str, Any]]:
        return [
            {"item": item_id, "condition": eq.condition}
            for item_id, eq in self.state.equipment.items()
            if eq.condition < WARNING_THRESHOLD
        ]

    # -- validation ---------------------------------------------------------
    def validate(self, deltas: dict[str, int]) -> None:
        problems: list[dict[str, Any]] = []
        for item_id, delta in deltas.items():
            definition = self.catalog.item(item_id)
            if definition is None:
                problems.append({"item": item_id, "reason": "unknown_item"})
            elif delta < 0 and self.count(item_id) < -delta:
                problems.append(
                    {
                        "item": item_id,
                        "required": -delta,
                        "available": self.count(item_id),
                        "reason": "missing",
                    }
                )
        if problems:
            first = problems[0]
            raise MissingItemError(
                f"Missing item '{first['item']}' "
                f"(needs {first.get('required', 1)}, has {first.get('available', 0)}).",
                missing=problems,
            )

    # -- writes -------------------------------------------------------------
    def apply(self, deltas: dict[str, int]) -> list[dict[str, Any]]:
        self.validate(deltas)
        changes: list[dict[str, Any]] = []
        for item_id, delta in deltas.items():
            if delta == 0:
                continue
            definition = self.catalog.item(item_id)
            assert definition is not None  # validated above
            before = self.count(item_id)
            after = clamp(before + delta, 0, definition.max_stack)
            self.state.inventory[item_id] = after
            if definition.durable and after > 0 and item_id not in self.state.equipment:
                self.state.equipment[item_id] = EquipmentState()
            changes.append({"item": item_id, "before": before, "after": after, "delta": after - before})
        return changes

    def add(self, item_id: str, quantity: int = 1) -> int:
        self.apply({item_id: quantity})
        return self.count(item_id)

    def remove(self, item_id: str, quantity: int = 1) -> int:
        self.apply({item_id: -quantity})
        return self.count(item_id)

    def wear(self, item_id: str, amount: int | None = None) -> int:
        """Reduce tool condition. Returns the new condition."""
        definition = self.catalog.item(item_id)
        if definition is None or not definition.durable:
            return 100
        equipment = self.state.equipment.setdefault(item_id, EquipmentState())
        equipment.condition = clamp(equipment.condition - (definition.wear_per_use if amount is None else amount), 0, 100)
        return equipment.condition

    def restore(self, item_id: str, amount: int = 40) -> int:
        definition = self.catalog.item(item_id)
        if definition is None or not definition.durable:
            raise InvalidInputError(f"'{item_id}' is not a durable tool.")
        equipment = self.state.equipment.setdefault(item_id, EquipmentState())
        equipment.condition = clamp(equipment.condition + amount, 0, 100)
        equipment.maintenance_count += 1
        equipment.calibrated = True
        return equipment.condition

    def check_tool_ready(self, item_id: str) -> None:
        """Raise if a durable tool is too worn to be trusted."""
        definition = self.catalog.item(item_id)
        if definition is None or not definition.durable or not self.has(item_id):
            return
        equipment = self.state.equipment.setdefault(item_id, EquipmentState())
        if equipment.condition <= FAILURE_THRESHOLD:
            equipment.failure_count += 1
            raise EquipmentFailureError(
                f"'{item_id}' is too worn to operate ({equipment.condition}% condition). "
                "Service it first.",
                item=item_id,
                condition=equipment.condition,
                remedy="use a maintenance_kit or a service station",
            )

    # -- consumables --------------------------------------------------------
    def use(self, item_id: str, target: str | None = None) -> dict[str, Any]:
        """Use a consumable outside of a mission script (F key)."""
        definition = self.catalog.item(item_id)
        if definition is None:
            raise InvalidInputError(f"Unknown item '{item_id}'.")
        if not self.has(item_id):
            raise MissingItemError(f"You do not carry '{item_id}'.", missing=[{"item": item_id}])

        effects: dict[str, Any] = {"item": item_id, "changes": [], "message": ""}

        if definition.category == ItemCategory.consumable and item_id == "oxygen_tank":
            self.remove(item_id)
            effects["changes"] = self.resources.apply({"oxygen": 40})
            effects["message"] = "Suit oxygen reserve refilled (+40)."
            self.state.suit.oxygen_alarm = None
        elif item_id == "power_cell":
            self.remove(item_id)
            effects["changes"] = self.resources.apply({"power": 25})
            effects["message"] = "Power cell connected (+25 kWh)."
        elif item_id == "maintenance_kit":
            tool_id = target or self._most_worn_tool()
            if tool_id is None:
                raise InvalidInputError("No durable tool needs maintenance.")
            self.remove(item_id)
            condition = self.restore(tool_id, 45)
            effects["target"] = tool_id
            effects["condition"] = condition
            effects["message"] = f"{tool_id} serviced. Condition {condition}%."
        elif item_id == "filter_cartridge":
            self.remove(item_id)
            self.state.set_flag("water_filter_fresh")
            effects["changes"] = self.resources.apply({"water": 10})
            effects["message"] = "Fresh filter installed, recycling efficiency restored."
        elif item_id == "coolant_canister":
            self.remove(item_id)
            self.state.set_flag("equipment_cooled")
            effects["message"] = "Overheated hardware cooled down."
        else:
            raise InvalidInputError(
                f"'{item_id}' is used through a mission interaction, not directly.",
                item=item_id,
            )
        return effects

    def _most_worn_tool(self) -> str | None:
        candidates = [
            (eq.condition, item_id)
            for item_id, eq in self.state.equipment.items()
            if self.has(item_id)
        ]
        if not candidates:
            return None
        return min(candidates)[1]

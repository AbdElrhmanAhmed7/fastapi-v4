from collections.abc import Mapping


class InventorySystem:
    """Manage the tools and consumable items owned by one player."""

    DEFAULT_ITEMS = {
        "scanner": 1,
        "wrench": 1,
        "repair_kit": 1,
    }

    def __init__(self, initial_items: Mapping[str, int] | None = None):
        source = self.DEFAULT_ITEMS if initial_items is None else initial_items
        if not isinstance(source, Mapping):
            raise TypeError("Initial inventory must be a mapping")

        self._items: dict[str, int] = dict(source)

        for name, quantity in self._items.items():
            self._validate_name(name)
            self._validate_initial_quantity(quantity, name)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Item name must be a non-empty string")

    @staticmethod
    def _validate_initial_quantity(quantity: int, name: str) -> None:
        if type(quantity) is not int or quantity < 0:
            raise ValueError(f"Initial quantity for '{name}' must be a non-negative integer")

    @staticmethod
    def _validate_quantity(quantity: int) -> None:
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("Quantity must be a positive integer")

    def has_item(self, name: str) -> bool:
        self._validate_name(name)
        return self._items.get(name, 0) > 0

    def get_all(self) -> dict[str, int]:
        return self._items.copy()

    def add_item(self, name: str, quantity: int) -> int:
        self._validate_name(name)
        self._validate_quantity(quantity)
        self._items[name] = self._items.get(name, 0) + quantity
        return self._items[name]

    def remove_item(self, name: str, quantity: int) -> int:
        self._validate_name(name)
        self._validate_quantity(quantity)
        if name not in self._items:
            raise KeyError(f"Unknown item: {name}")
        if self._items[name] < quantity:
            raise ValueError(f"Not enough {name}")

        self._items[name] -= quantity
        return self._items[name]

    def consume_item(self, name: str) -> int:
        return self.remove_item(name, 1)

    def to_dict(self) -> dict[str, int]:
        return self.get_all()

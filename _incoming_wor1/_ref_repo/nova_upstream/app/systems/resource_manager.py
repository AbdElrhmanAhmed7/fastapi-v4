from collections.abc import Mapping


class ResourceManager:
    """Manage a player's numeric resources.

    Each instance owns its own dictionary, so separate game sessions cannot
    accidentally share resource values.
    """

    DEFAULT_RESOURCES = {
        "water": 100,
        "oxygen": 80,
        "power": 60,
    }

    def __init__(self, initial_resources: Mapping[str, int] | None = None):
        source = self.DEFAULT_RESOURCES if initial_resources is None else initial_resources
        if not isinstance(source, Mapping):
            raise TypeError("Initial resources must be a mapping")

        self._resources: dict[str, int] = dict(source)

        for name, amount in self._resources.items():
            self._validate_name(name)
            self._validate_initial_amount(amount, name)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Resource name must be a non-empty string")

    @staticmethod
    def _validate_initial_amount(amount: int, name: str) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError(f"Initial amount for '{name}' must be a non-negative integer")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if type(amount) is not int or amount <= 0:
            raise ValueError("Amount must be a positive integer")

    def get_resource(self, name: str) -> int:
        self._validate_name(name)
        if name not in self._resources:
            raise KeyError(f"Unknown resource: {name}")
        return self._resources[name]

    def get_all(self) -> dict[str, int]:
        return self._resources.copy()

    def consume(self, name: str, amount: int) -> int:
        self._validate_name(name)
        self._validate_amount(amount)
        if name not in self._resources:
            raise KeyError(f"Unknown resource: {name}")
        if self._resources[name] < amount:
            raise ValueError(f"Not enough {name}")

        self._resources[name] -= amount
        return self._resources[name]

    def add(self, name: str, amount: int) -> int:
        self._validate_name(name)
        self._validate_amount(amount)
        if name not in self._resources:
            raise KeyError(f"Unknown resource: {name}")

        self._resources[name] += amount
        return self._resources[name]

    def to_dict(self) -> dict[str, int]:
        return self.get_all()

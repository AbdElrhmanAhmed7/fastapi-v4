from threading import RLock

from app.missions.water_leak_mission import WaterLeakMission
from app.systems.inventory_system import InventorySystem
from app.systems.resource_manager import ResourceManager


class GameSession:
    """Own all mutable state for one player session."""

    def __init__(self, game_id: str, player_name: str):
        if not isinstance(player_name, str) or not player_name.strip():
            raise ValueError("Player name must be a non-empty string")

        self.game_id = game_id
        self.player_name = player_name.strip()
        self.resources = ResourceManager()
        self.inventory = InventorySystem()
        self.water_leak_mission = WaterLeakMission()
        self._lock = RLock()

    def get_resources(self) -> dict[str, int]:
        with self._lock:
            return self.resources.get_all()

    def get_inventory(self) -> dict[str, int]:
        with self._lock:
            return self.inventory.get_all()

    def get_mission_state(self) -> dict:
        with self._lock:
            return self.water_leak_mission.get_state()

    def execute_mission_action(self, action: str) -> dict:
        """Execute one mission action and return a consistent state snapshot."""
        with self._lock:
            result = self.water_leak_mission.execute_action(
                action,
                self.resources,
                self.inventory,
            )
            result["resources"] = self.resources.get_all()
            result["inventory"] = self.inventory.get_all()
            result["mission"] = self.water_leak_mission.get_state()
            return result

    def get_state(self) -> dict:
        with self._lock:
            return {
                "game_id": self.game_id,
                "player_name": self.player_name,
                "resources": self.resources.get_all(),
                "inventory": self.inventory.get_all(),
                "missions": {
                    "water_leak": self.water_leak_mission.get_state(),
                },
            }

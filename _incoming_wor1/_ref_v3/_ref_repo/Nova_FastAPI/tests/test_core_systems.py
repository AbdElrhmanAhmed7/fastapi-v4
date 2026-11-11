import pytest

from app.systems.inventory_system import InventorySystem
from app.systems.resource_manager import ResourceManager


def test_resource_manager_consumes_and_adds_resources():
    resources = ResourceManager()
    assert resources.consume("power", 5) == 55
    assert resources.add("water", 10) == 110


def test_failed_resource_consumption_does_not_change_state():
    resources = ResourceManager()
    with pytest.raises(ValueError, match="Not enough power"):
        resources.consume("power", 100)
    assert resources.get_resource("power") == 60


def test_resource_snapshot_cannot_mutate_original():
    resources = ResourceManager()
    snapshot = resources.get_all()
    snapshot["power"] = 999
    assert resources.get_resource("power") == 60


def test_inventory_consumes_one_item():
    inventory = InventorySystem()
    assert inventory.consume_item("repair_kit") == 0
    assert inventory.has_item("repair_kit") is False


def test_failed_inventory_removal_does_not_change_state():
    inventory = InventorySystem()
    with pytest.raises(ValueError, match="Not enough scanner"):
        inventory.remove_item("scanner", 2)
    assert inventory.get_all()["scanner"] == 1


def test_failed_repair_does_not_consume_repair_kit_or_extra_power():
    from app.missions.water_leak_mission import WaterLeakMission

    resources = ResourceManager({"water": 100, "oxygen": 80, "power": 3})
    inventory = InventorySystem()
    mission = WaterLeakMission()

    mission.execute_action("scan_leak", resources, inventory)
    mission.execute_action("close_valve", resources, inventory)

    # Scanning used 2 of 3 power, so repair cannot proceed.
    with pytest.raises(ValueError, match="Not enough power"):
        mission.execute_action("repair_pipe", resources, inventory)

    assert resources.get_resource("power") == 1
    assert inventory.get_all()["repair_kit"] == 1
    assert mission.pipe_repaired is False
    assert mission.leak_active is True

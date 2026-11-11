class WaterLeakMission:
    """Gameplay rules for the Earth Analog water-leak mission."""

    def __init__(self):
        self.mission_id = "water_leak"
        self.status = "active"
        self.leak_detected = False
        self.valve_closed = False
        self.pipe_repaired = False
        self.leak_active = True
        self.current_objective = "scan_leak"

    def _update_objective(self) -> None:
        if not self.leak_detected:
            self.current_objective = "scan_leak"
        elif not self.valve_closed:
            self.current_objective = "close_valve"
        elif not self.pipe_repaired:
            self.current_objective = "repair_pipe"
        else:
            self.current_objective = None
            self.status = "completed"

    def execute_action(self, action: str, resources, inventory) -> dict:
        if self.status == "completed":
            raise ValueError("Mission already completed")

        actions = {
            "scan_leak": self._scan_leak,
            "close_valve": self._close_valve,
            "repair_pipe": self._repair_pipe,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Unknown action: {action}")
        return handler(resources, inventory)

    def _scan_leak(self, resources, inventory) -> dict:
        if self.leak_detected:
            raise ValueError("Leak already detected")
        if not inventory.has_item("scanner"):
            raise ValueError("Scanner required")
        if resources.get_resource("power") < 2:
            raise ValueError("Not enough power")

        resources.consume("power", 2)
        self.leak_detected = True
        self._update_objective()
        return self._success_response("leak_detected", "Leak detected successfully")

    def _close_valve(self, resources, inventory) -> dict:
        if not self.leak_detected:
            raise ValueError("Detect the leak first")
        if self.valve_closed:
            raise ValueError("Valve already closed")
        if not inventory.has_item("wrench"):
            raise ValueError("Wrench required")

        self.valve_closed = True
        self._update_objective()
        return self._success_response("valve_closed", "Valve closed successfully")

    def _repair_pipe(self, resources, inventory) -> dict:
        if not self.valve_closed:
            raise ValueError("Close the valve first")
        if self.pipe_repaired:
            raise ValueError("Pipe already repaired")
        if not inventory.has_item("repair_kit"):
            raise ValueError("Repair kit required")
        if resources.get_resource("power") < 5:
            raise ValueError("Not enough power")

        # Validate every precondition before changing any state.
        resources.consume("power", 5)
        inventory.consume_item("repair_kit")
        self.pipe_repaired = True
        self.leak_active = False
        self._update_objective()
        return self._success_response("pipe_repaired", "Pipe repaired successfully")

    def _success_response(self, event: str, message: str) -> dict:
        return {
            "success": True,
            "event": event,
            "message": message,
            "mission_status": self.status,
            "next_objective": self.current_objective,
        }

    def get_state(self) -> dict:
        return {
            "id": self.mission_id,
            "status": self.status,
            "current_objective": self.current_objective,
            "leak_detected": self.leak_detected,
            "valve_closed": self.valve_closed,
            "pipe_repaired": self.pipe_repaired,
            "leak_active": self.leak_active,
        }

"""Reference client: plays the whole NOVA campaign through the public HTTP API.

This is both a developer tool and the backbone of the end-to-end test suite.
It deliberately uses *only* public endpoints – no database access, no private
shortcuts – so a successful run proves the API is sufficient for a real client.

Usage::

    python scripts/playthrough.py --base-url http://127.0.0.1:8000
    python scripts/playthrough.py --difficulty veteran --seed 7 --json report.json

The simulation controllers below are simple, readable autopilots. A human
player flies with the mouse; these exist so CI can verify the physics models
are actually winnable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol


class HttpClient(Protocol):
    """Minimal contract shared by ``httpx.Client`` and Starlette's TestClient."""

    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class PlaythroughError(RuntimeError):
    """Raised when the autopilot cannot make progress (a real backend bug)."""


# ---------------------------------------------------------------------------
# Simulation controllers
# ---------------------------------------------------------------------------
def lander_command(telemetry: dict[str, Any]) -> dict[str, float]:
    """Guidance law for the lunar descent: null the drift, then brake.

    Two coupled loops:
    * horizontal – aim for a closing velocity proportional to pad distance,
      converted into an attitude request (tilting trades lift for translation);
    * vertical – track a descent-rate profile that flattens near the ground.
    """
    altitude = max(float(telemetry["altitude_agl"]), 0.0)
    vertical = float(telemetry["vertical_speed"])
    horizontal = float(telemetry["horizontal_speed"])
    offset = float(telemetry["pad_x"]) - float(telemetry["position_x"])
    attitude = float(telemetry["attitude"])
    fuel = float(telemetry["fuel"])

    # --- horizontal guidance -------------------------------------------------
    closing = max(1.2, min(9.0, abs(offset) * 0.045))
    wanted_horizontal = math.copysign(closing, offset) if abs(offset) > 2.0 else 0.0
    if abs(offset) <= 2.0:
        wanted_horizontal = -horizontal * 0.5
    accel_needed = (wanted_horizontal - horizontal) / 4.0
    mass = 1050.0 + max(fuel, 0.0)
    thrust_guess = max(mass * 1.62, 1.0)
    wanted_attitude = math.degrees(math.asin(max(-0.7, min(0.7, accel_needed * mass / thrust_guess))))
    if altitude < 25.0:
        wanted_attitude = max(-4.0, min(4.0, wanted_attitude))
    attitude_input = max(-1.0, min(1.0, (wanted_attitude - attitude) / 6.0))

    # --- vertical guidance ---------------------------------------------------
    target_descent = -max(1.4, min(38.0, altitude * 0.075 + 1.4))
    if altitude < 12.0:
        target_descent = -1.6
    accel_y = (target_descent - vertical) / 3.0 + 1.62
    throttle = mass * accel_y / (9000.0 * max(math.cos(math.radians(attitude)), 0.35))
    return {"throttle": round(max(0.0, min(1.0, throttle)), 3), "attitude": round(attitude_input, 3)}


def fire_command(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Flooding strategy: short CO₂ bursts, then scrub the cabin atmosphere."""
    intensity = float(telemetry.get("intensity", 0.0))
    suppressed = bool(telemetry.get("suppressed", False))
    oxygen = float(telemetry.get("oxygen_pct", 20.9))
    co2 = float(telemetry.get("co2_ppm", 450.0))
    charges = float(telemetry.get("co2_charges", 0.0))

    if not suppressed and intensity > 0.02 and charges > 0 and oxygen > 19.0:
        # Meter the dose: 1.15 intensity reduction per unit of CO₂.
        dose = min(1.0, max(0.25, intensity / 1.15 / 0.5))
        return {"co2": round(dose, 2), "ventilation": False, "scrubber": False}
    return {"co2": 0.0, "ventilation": co2 > 3_000, "scrubber": True}


def vitals_command(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Supportive care inside the (fictional) medical simulation."""
    return {"oxygen_support": True, "resting": True, "cooling_applied": True}


def rover_command(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Engage the waypoint autopilot on the next unreached required waypoint."""
    waypoints = telemetry.get("waypoints", [])
    for waypoint in waypoints:
        if waypoint.get("required", True) and not waypoint.get("reached", False):
            return {"target": waypoint["id"], "throttle": 0.85}
    return {"throttle": 0.4}


CONTROLLERS = {
    "lunar_lander": lander_command,
    "microgravity_fire": fire_command,
    "crew_vitals": vitals_command,
    "rover_traverse": rover_command,
}


# ---------------------------------------------------------------------------
# Autopilot
# ---------------------------------------------------------------------------
@dataclass
class MissionOutcome:
    mission_id: str
    score_pct: float
    grade: str
    mistakes: int
    steps: int
    simulation: dict[str, Any] | None = None


@dataclass
class Autopilot:
    client: HttpClient
    prefix: str = "/api/v1"
    verbose: bool = False
    log: list[str] = field(default_factory=list)

    # -- plumbing ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.prefix}{path}"

    def _get(self, path: str, **kwargs: Any) -> Any:
        response = self.client.get(self._url(path), **kwargs)
        if response.status_code >= 400:
            raise PlaythroughError(f"GET {path} → {response.status_code}: {response.text[:400]}")
        return response.json()

    def _post(self, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        response = self.client.post(self._url(path), json=payload if payload is not None else {}, **kwargs)
        if response.status_code >= 400:
            raise PlaythroughError(f"POST {path} → {response.status_code}: {response.text[:400]}")
        return response.json()

    def _say(self, message: str) -> None:
        self.log.append(message)
        if self.verbose:
            print(message)

    # -- campaign ---------------------------------------------------------
    def create_game(self, player_name: str = "Autopilot", **kwargs: Any) -> str:
        body = self._post("/games", {"player_name": player_name, **kwargs})
        self.game_id: str = body["game_id"]
        self.session_token: str = body["session_token"]
        self._say(f"▶ game {self.game_id} ({player_name})")
        return self.game_id

    def headers(self) -> dict[str, str]:
        return {"X-Session-Token": getattr(self, "session_token", "")}

    def run_campaign(self, player_name: str = "Autopilot", **kwargs: Any) -> dict[str, Any]:
        self.create_game(player_name, **kwargs)
        outcomes: list[MissionOutcome] = []
        for _ in range(40):
            catalogue = self._get(f"/games/{self.game_id}/missions")
            pending = [m for m in catalogue if m["status"] in {"available", "active", "failed"}]
            if not pending:
                break
            pending.sort(key=lambda m: (m["stage"], m["id"]))
            mission = pending[0]
            outcomes.append(self.play_mission(mission["id"]))
        report = self._get(f"/games/{self.game_id}/reports")
        report["autopilot"] = [outcome.__dict__ for outcome in outcomes]
        return report

    # -- one mission ------------------------------------------------------
    def play_mission(self, mission_id: str, max_steps: int = 120) -> MissionOutcome:
        base = f"/games/{self.game_id}/missions/{mission_id}"
        self._post(f"{base}/start")
        blueprint = self._get(f"/content/missions/{mission_id}")
        self._say(f"  ▸ {mission_id}")

        steps = 0
        simulation_result: dict[str, Any] | None = None
        for steps in range(1, max_steps + 1):
            view = self._get(base)
            if view["status"] == "completed":
                break
            pending = [o for o in view["objectives"] if not o["completed"]]
            if not pending:
                break

            actionable = next((o for o in pending if o["available"]), None)
            if actionable is not None:
                payload = self._payload_for(blueprint, actionable["id"])
                result = self._post(f"{base}/actions", {"action": actionable["id"], "payload": payload})
                self._say(f"     ✓ {actionable['id']} → {result['message'][:70]}")
                if result.get("completed"):
                    simulation_result = (result.get("report") or {}).get("simulation")
                    break
                continue

            blocker = pending[0]
            if not self._resolve_blocker(mission_id, blueprint, blocker):
                raise PlaythroughError(
                    f"{mission_id}: stuck on '{blocker['id']}' – unmet {blocker['unmet']}"
                )
        else:
            raise PlaythroughError(f"{mission_id}: exceeded {max_steps} steps")

        report = self._get(f"{base}/report")
        self._say(
            f"     ★ {mission_id}: {report['score_pct']}% grade {report['grade']} "
            f"({report['mistakes']} mistakes, {steps} steps)"
        )
        return MissionOutcome(
            mission_id=mission_id,
            score_pct=report["score_pct"],
            grade=report["grade"],
            mistakes=report["mistakes"],
            steps=steps,
            simulation=simulation_result,
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _payload_for(blueprint: dict[str, Any], action_id: str) -> dict[str, Any]:
        """Build a valid payload from the mission blueprint's field specs."""
        actions = {o["id"]: o for o in blueprint["objectives"] + blueprint.get("side_actions", [])}
        objective = actions.get(action_id, {})
        payload: dict[str, Any] = {}
        for spec in objective.get("payload", []):
            if spec.get("default") is not None:
                payload[spec["name"]] = spec["default"]
            elif spec.get("choices"):
                payload[spec["name"]] = spec["choices"][0]
            elif spec["type"] in {"number", "integer"}:
                low = spec.get("minimum", 0)
                high = spec.get("maximum", low + 10)
                value = (low + high) / 2
                payload[spec["name"]] = int(value) if spec["type"] == "integer" else value
            elif spec["type"] == "boolean":
                payload[spec["name"]] = True
            else:
                payload[spec["name"]] = "auto"
        return payload

    def _resolve_blocker(
        self, mission_id: str, blueprint: dict[str, Any], objective: dict[str, Any]
    ) -> bool:
        """Try to satisfy the first unmet precondition of a blocked objective."""
        base = f"/games/{self.game_id}/missions/{mission_id}"
        for unmet in objective["unmet"]:
            kind = unmet.get("type")
            if kind == "sim":
                self.run_simulation(mission_id)
                return True
            if kind == "equipment":
                self._post(
                    f"/games/{self.game_id}/inventory/maintain",
                    {"item_id": unmet.get("id") or unmet.get("item")},
                )
                return True
            if kind == "item":
                wanted = unmet.get("id") or unmet.get("item")
                # Another action in this mission may hand out the item.
                for candidate in blueprint["objectives"] + blueprint.get("side_actions", []):
                    if candidate["id"] == objective["id"]:
                        continue
                    if candidate.get("effects", {}).get("items", {}).get(wanted, 0) > 0:
                        self._post(
                            f"{base}/actions",
                            {"action": candidate["id"], "payload": self._payload_for(blueprint, candidate["id"])},
                        )
                        self._say(f"     ↻ {candidate['id']} (to obtain {wanted})")
                        return True
                return False
            if kind == "resource":
                resource = unmet.get("id") or unmet.get("resource")
                item = {"oxygen": "oxygen_tank", "power": "power_cell", "water": "filter_cartridge"}.get(resource)
                if item:
                    try:
                        self._post(f"/games/{self.game_id}/inventory/use", {"item_id": item})
                        return True
                    except PlaythroughError:
                        return False
        return False

    def run_simulation(self, mission_id: str, max_ticks: int = 1_500) -> dict[str, Any]:
        """Fly/drive/treat until the server says the run finished."""
        base = f"/games/{self.game_id}/missions/{mission_id}/simulation"
        status = self._get(base)
        telemetry = status["telemetry"]
        if telemetry["status"] != "running":
            telemetry = self._post(f"{base}/start", {"params": {}})["telemetry"]

        controller = CONTROLLERS.get(telemetry["kind"])
        if controller is None:
            raise PlaythroughError(f"No controller for simulation '{telemetry['kind']}'")

        for _ in range(max_ticks):
            command = controller(telemetry)
            response = self._post(f"{base}/tick", {"dt_seconds": 0.5, "command": command})
            telemetry = response["telemetry"]
            if response.get("finished"):
                result = response.get("result") or {}
                self._say(
                    f"     ⚙ {telemetry['kind']}: {telemetry['status']} "
                    f"score={result.get('score_pct')} reason={result.get('reason')}"
                )
                if telemetry["status"] != "succeeded":
                    raise PlaythroughError(
                        f"{mission_id}: simulation failed ({result.get('reason')})"
                    )
                return result
        raise PlaythroughError(f"{mission_id}: simulation did not finish in {max_ticks} ticks")

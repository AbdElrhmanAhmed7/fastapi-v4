"""The mission engine: one interpreter for every NOVA stage.

Responsibilities
----------------
* unlock / start / progress / complete missions,
* validate every action against data-driven preconditions,
* apply effects **atomically** (validate everything before mutating anything),
* punish wrong-order attempts through explicit hazard rules,
* emit events for the 3D frontend (animation cues, world changes),
* score the run and feed the Mission Continuity System.

The engine is pure domain logic: it never touches the database or HTTP.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import (
    EquipmentFailureError,
    InvalidInputError,
    MissionCompletedError,
    MissionLockedError,
    MissionNotFoundError,
    PreconditionFailedError,
    UnknownActionError,
)
from app.core.logging import get_logger
from app.core.utils import clamp, deep_get, merge_dicts, round_to, utcnow
from app.domain.enums import MissionStatus
from app.domain.state import GameState, MissionProgress
from app.missions.definitions import (
    Condition,
    Effects,
    MissionDefinition,
    Objective,
    PayloadField,
)
from app.missions.registry import GameContent
from app.systems.continuity import ContinuitySystem
from app.systems.events import EventRecorder
from app.systems.inventory_system import InventorySystem
from app.systems.resource_manager import ResourceManager
from app.systems.scoring import score_mission

logger = get_logger("nova.missions")

_COMPARATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
}


class MissionEngine:
    def __init__(self, content: GameContent) -> None:
        self.content = content
        self.catalog = content.catalog
        self.library = content.missions

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def bootstrap(self, state: GameState) -> None:
        """Create mission progress rows and unlock the entry mission."""
        for definition in self.library.ordered():
            progress = state.mission(definition.id)
            if progress.status == MissionStatus.locked and not definition.requires_missions:
                progress.status = MissionStatus.available
                progress.current_objective = definition.objectives[0].id
        self.refresh_unlocks(state)

    def refresh_unlocks(self, state: GameState) -> list[str]:
        unlocked: list[str] = []
        for definition in self.library.ordered():
            progress = state.mission(definition.id)
            if progress.status != MissionStatus.locked:
                continue
            if all(
                state.mission(dep).status == MissionStatus.completed
                for dep in definition.requires_missions
            ):
                progress.status = MissionStatus.available
                progress.current_objective = definition.objectives[0].id
                unlocked.append(definition.id)
        return unlocked

    def start(
        self, state: GameState, mission_id: str, recorder: EventRecorder
    ) -> dict[str, Any]:
        definition = self.require_definition(mission_id)
        progress = state.mission(mission_id)

        if progress.status == MissionStatus.locked:
            missing = [
                dep
                for dep in definition.requires_missions
                if state.mission(dep).status != MissionStatus.completed
            ]
            raise MissionLockedError(
                f"Mission '{mission_id}' is locked. Complete {', '.join(missing)} first.",
                mission_id=mission_id,
                requires=missing,
            )
        if progress.status == MissionStatus.completed:
            raise MissionCompletedError(f"Mission '{mission_id}' is already completed.")

        if progress.status in {MissionStatus.available, MissionStatus.failed}:
            progress.status = MissionStatus.active
            progress.started_at = utcnow()
            progress.completed_at = None
            progress.completed_objectives = []
            progress.current_objective = definition.objectives[0].id
            progress.elapsed_seconds = 0.0
            progress.data = {}
            state.environment = definition.environment

            continuity = ContinuitySystem(state, self.catalog)
            applied = continuity.apply_pending(mission_id)

            recorder.emit(
                "mission.started",
                {
                    "mission_id": mission_id,
                    "title": definition.title.model_dump(),
                    "environment": definition.environment.value,
                    "objectives": [o.id for o in definition.objectives],
                    "continuity_applied": applied,
                },
                mission_id=mission_id,
            )

        return self.mission_view(state, mission_id)

    def require_definition(self, mission_id: str) -> MissionDefinition:
        definition = self.library.get(mission_id)
        if definition is None:
            raise MissionNotFoundError(
                f"Unknown mission '{mission_id}'.",
                available=[m.id for m in self.library.ordered()],
            )
        return definition

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------
    def execute(
        self,
        state: GameState,
        mission_id: str,
        action_id: str,
        recorder: EventRecorder,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        definition = self.require_definition(mission_id)
        progress = state.mission(mission_id)

        if progress.status == MissionStatus.locked:
            raise MissionLockedError(
                f"Mission '{mission_id}' is locked.", mission_id=mission_id
            )
        if progress.status == MissionStatus.completed:
            raise MissionCompletedError(f"Mission '{mission_id}' is already completed.")
        if progress.status != MissionStatus.active:
            self.start(state, mission_id, recorder)
            progress = state.mission(mission_id)

        objective = definition.find_action(action_id)
        if objective is None:
            raise UnknownActionError(
                f"Mission '{mission_id}' has no action '{action_id}'.",
                mission_id=mission_id,
                allowed=[o.id for o in (*definition.objectives, *definition.side_actions)],
            )

        is_main = objective in definition.objectives
        if (
            is_main
            and not objective.repeatable
            and objective.id in progress.completed_objectives
        ):
            raise PreconditionFailedError(
                f"Objective '{objective.id}' is already done.",
                mission_id=mission_id,
                objective=objective.id,
                reason="already_completed",
            )

        clean_payload = self._validate_payload(objective, payload or {})
        progress.attempts += 1
        progress.action_counts[action_id] = progress.action_counts.get(action_id, 0) + 1
        state.stats.actions += 1

        unmet = self.unmet_conditions(state, definition, progress, objective)
        if unmet:
            return self._handle_failure(
                state, definition, progress, objective, unmet, recorder
            )

        # A worn-out tool can refuse to work even when everything else is fine.
        try:
            self._tool_check(state, objective)
        except EquipmentFailureError as exc:
            recorder.emit(
                "equipment.failed",
                {"mission_id": mission_id, "action": action_id, **exc.details},
                mission_id=mission_id,
            )
            raise

        effects = self._render_effects(objective.effects, clean_payload)
        changes = self._apply_effects(state, effects, mission_id=mission_id)

        # Side actions are tracked too (they earn the efficiency bonus) but they
        # never gate completion: only ``definition.objectives`` do.
        if objective.id not in progress.completed_objectives:
            progress.completed_objectives.append(objective.id)
        progress.data[f"{action_id}_payload"] = clean_payload
        state.stats.successful_actions += 1
        self._advance_objective(definition, progress)

        message = (objective.success_message.en if objective.success_message else objective.title.en)
        recorder.emit(
            "action.succeeded",
            {
                "mission_id": mission_id,
                "action": action_id,
                "target": objective.target,
                "animation": objective.animation,
                "duration_ms": objective.duration_ms,
                "message": message,
                "changes": changes,
                "payload": clean_payload,
                "science_note": objective.science_note,
            },
            mission_id=mission_id,
        )

        result: dict[str, Any] = {
            "success": True,
            "mission_id": mission_id,
            "action": action_id,
            "event": action_id,
            "message": message,
            "animation": objective.animation,
            "duration_ms": objective.duration_ms,
            "target": objective.target,
            "changes": changes,
            "mission": self.mission_view(state, mission_id),
            "resources": dict(state.resources),
            "inventory": dict(state.inventory),
            "completed": False,
        }

        if self._is_complete(definition, progress):
            result["completed"] = True
            result["report"] = self.complete(state, definition, progress, recorder)
        return result

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------
    def unmet_conditions(
        self,
        state: GameState,
        definition: MissionDefinition,
        progress: MissionProgress,
        objective: Objective,
    ) -> list[dict[str, Any]]:
        unmet: list[dict[str, Any]] = []
        inventory = InventorySystem(state, self.catalog)
        resources = ResourceManager(state, self.catalog)

        if objective.require_previous and objective in definition.objectives:
            for previous in definition.objectives:
                if previous is objective:
                    break
                if previous.optional or previous.id in progress.completed_objectives:
                    continue
                unmet.append(
                    {
                        "type": "objective",
                        "id": previous.id,
                        "message": f"Complete '{previous.title.en}' first.",
                        "hint": previous.hint.en,
                    }
                )

        for condition in objective.requires:
            problem = self._check_condition(state, condition, inventory, resources, progress, definition)
            if problem:
                unmet.append(problem)

        for item_id, delta in objective.effects.items.items():
            if delta < 0 and not inventory.has(item_id, -delta):
                unmet.append(
                    {
                        "type": "item",
                        "id": item_id,
                        "required": -delta,
                        "available": inventory.count(item_id),
                        "message": f"You need {self.catalog.items[item_id].name.en}.",
                    }
                )
        for resource_id, delta in objective.effects.resources.items():
            if delta < 0 and resources.get(resource_id) < -delta:
                unmet.append(
                    {
                        "type": "resource",
                        "id": resource_id,
                        "required": -delta,
                        "available": resources.get(resource_id),
                        "message": f"Not enough {resource_id} ({resources.get(resource_id)} available).",
                    }
                )
        return unmet

    def _check_condition(
        self,
        state: GameState,
        condition: Condition,
        inventory: InventorySystem,
        resources: ResourceManager,
        progress: MissionProgress,
        definition: MissionDefinition,
    ) -> dict[str, Any] | None:
        hint = condition.hint.en if condition.hint else None
        kind = condition.type

        if kind == "item":
            if not inventory.has(condition.id, condition.min):
                return {
                    "type": "item",
                    "id": condition.id,
                    "required": condition.min,
                    "available": inventory.count(condition.id),
                    "message": hint or f"Requires {condition.id} x{condition.min}.",
                }
        elif kind == "resource":
            if resources.get(condition.id) < condition.min:
                return {
                    "type": "resource",
                    "id": condition.id,
                    "required": condition.min,
                    "available": resources.get(condition.id),
                    "message": hint or f"Requires {condition.min} {condition.id}.",
                }
        elif kind == "flag":
            if state.has_flag(condition.id) != condition.value:
                return {
                    "type": "flag",
                    "id": condition.id,
                    "message": hint or f"World state '{condition.id}' is not ready.",
                }
        elif kind == "objective":
            if condition.id not in progress.completed_objectives:
                return {
                    "type": "objective",
                    "id": condition.id,
                    "message": hint or f"Complete '{condition.id}' first.",
                }
        elif kind == "mission":
            other = state.mission(condition.id)
            if other.status.value != condition.status:
                return {
                    "type": "mission",
                    "id": condition.id,
                    "message": hint or f"Mission '{condition.id}' must be {condition.status}.",
                }
        elif kind == "world":
            actual = deep_get(state.world, condition.path)
            if not _COMPARATORS[condition.op](actual, condition.value):
                return {
                    "type": "world",
                    "id": condition.path,
                    "message": hint or f"World condition '{condition.path}' not satisfied.",
                }
        elif kind == "sim":
            simulation = state.simulations.get(definition.id, {})
            actual = deep_get(simulation, condition.path)
            if not _COMPARATORS[condition.op](actual, condition.value):
                return {
                    "type": "sim",
                    "id": condition.path,
                    "actual": actual,
                    "message": hint or f"Simulation condition '{condition.path}' not satisfied.",
                }
        elif kind == "equipment":
            condition_value = inventory.condition(condition.id)
            if condition_value < condition.min_condition:
                return {
                    "type": "equipment",
                    "id": condition.id,
                    "required": condition.min_condition,
                    "available": condition_value,
                    "message": hint
                    or f"{condition.id} condition too low ({condition_value}%). Service the tool.",
                }
        return None

    def _tool_check(self, state: GameState, objective: Objective) -> None:
        inventory = InventorySystem(state, self.catalog)
        for condition in objective.requires:
            if condition.type == "item":
                inventory.check_tool_ready(condition.id)

    # ------------------------------------------------------------------
    # Failure handling (wrong order, missing gear, worn tools)
    # ------------------------------------------------------------------
    def _handle_failure(
        self,
        state: GameState,
        definition: MissionDefinition,
        progress: MissionProgress,
        objective: Objective,
        unmet: list[dict[str, Any]],
        recorder: EventRecorder,
    ) -> dict[str, Any]:
        progress.mistakes += 1
        state.stats.mistakes += 1

        missing_ids = {str(item.get("id")) for item in unmet}
        hazard = next(
            (
                h
                for h in definition.hazards
                if h.action == objective.id
                and (not h.missing_any or missing_ids.intersection(h.missing_any))
            ),
            None,
        )

        hazard_changes: dict[str, Any] = {}
        message = unmet[0]["message"]
        if hazard is not None:
            message = hazard.message.en
            hazard_changes = self._apply_effects(state, hazard.effects, lenient=True)

        recorder.emit(
            "action.failed",
            {
                "mission_id": definition.id,
                "action": objective.id,
                "target": objective.target,
                "unmet": unmet,
                "hazard": hazard.model_dump(mode="json") if hazard else None,
                "changes": hazard_changes,
                "message": message,
                "animation": "fail",
            },
            mission_id=definition.id,
        )

        raise PreconditionFailedError(
            message,
            mission_id=definition.id,
            action=objective.id,
            unmet=unmet,
            hazard_applied=hazard is not None,
            changes=hazard_changes,
            mistakes=progress.mistakes,
        )

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------
    def _render_effects(self, effects: Effects, payload: dict[str, Any]) -> Effects:
        """Support ``{{payload.field}}`` placeholders inside world effects."""
        if not payload or not effects.world:
            return effects
        rendered = dict(effects.world)
        for key, value in rendered.items():
            if isinstance(value, str) and value.startswith("{{payload.") and value.endswith("}}"):
                field = value[len("{{payload.") : -2]
                if field in payload:
                    rendered[key] = payload[field]
        return effects.model_copy(update={"world": rendered})

    def _apply_effects(
        self, state: GameState, effects: Effects, *, lenient: bool = False, mission_id: str | None = None
    ) -> dict[str, Any]:
        resources = ResourceManager(state, self.catalog)
        inventory = InventorySystem(state, self.catalog)
        changes: dict[str, Any] = {}

        if effects.resources:
            if lenient:
                affordable = {
                    name: delta
                    for name, delta in effects.resources.items()
                    if delta >= 0 or resources.get(name) >= -delta
                }
                changes["resources"] = resources.apply(affordable)
            else:
                changes["resources"] = resources.apply(effects.resources)
        if effects.items:
            if lenient:
                affordable_items = {
                    name: delta
                    for name, delta in effects.items.items()
                    if delta >= 0 or inventory.has(name, -delta)
                }
                changes["items"] = inventory.apply(affordable_items)
            else:
                changes["items"] = inventory.apply(effects.items)

        for flag in effects.flags_set:
            state.set_flag(flag, True)
        for flag in effects.flags_clear:
            state.set_flag(flag, False)
        if effects.flags_set or effects.flags_clear:
            changes["flags"] = {
                "set": effects.flags_set,
                "cleared": effects.flags_clear,
            }

        if effects.world:
            state.world.update(effects.world)
            changes["world"] = dict(effects.world)

        if effects.sim and mission_id:
            current = state.simulations.setdefault(mission_id, {})
            state.simulations[mission_id] = merge_dicts(current, effects.sim)
            changes["sim"] = dict(effects.sim)

        for item_id, amount in effects.equipment_wear.items():
            changes.setdefault("equipment", {})[item_id] = inventory.wear(item_id, amount)
        for item_id, amount in effects.equipment_restore.items():
            if self.catalog.item(item_id) and self.catalog.item(item_id).durable:  # type: ignore[union-attr]
                changes.setdefault("equipment", {})[item_id] = inventory.restore(item_id, amount)

        if effects.readiness:
            state.continuity.readiness = clamp(
                state.continuity.readiness + effects.readiness, 0.0, 100.0
            )
            changes["readiness"] = state.continuity.readiness

        for stat_name, value in effects.stats.items():
            if hasattr(state.stats, stat_name):
                current = getattr(state.stats, stat_name)
                if isinstance(current, (int, float)):
                    setattr(state.stats, stat_name, round_to(current + value, 3))
        if effects.stats:
            changes["stats"] = dict(effects.stats)

        for unlocked_id in effects.unlock:
            unlocked = state.mission(unlocked_id)
            if unlocked.status == MissionStatus.locked:
                unlocked.status = MissionStatus.available
        return changes

    # ------------------------------------------------------------------
    # Payload validation
    # ------------------------------------------------------------------
    def _validate_payload(self, objective: Objective, payload: dict[str, Any]) -> dict[str, Any]:
        if not objective.payload:
            return {}
        specs: dict[str, PayloadField] = {field.name: field for field in objective.payload}
        unknown = set(payload) - set(specs)
        if unknown:
            raise InvalidInputError(
                f"Unexpected payload fields for '{objective.id}': {sorted(unknown)}",
                allowed=sorted(specs),
            )

        clean: dict[str, Any] = {}
        for field in objective.payload:
            if field.name not in payload:
                if field.required and field.default is None:
                    raise InvalidInputError(
                        f"Action '{objective.id}' requires payload field '{field.name}'.",
                        field=field.name,
                    )
                if field.default is not None:
                    clean[field.name] = field.default
                continue
            value = payload[field.name]
            if field.type in {"number", "integer"}:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise InvalidInputError(f"'{field.name}' must be a number.")
                if field.type == "integer" and int(value) != value:
                    raise InvalidInputError(f"'{field.name}' must be an integer.")
                if field.minimum is not None and value < field.minimum:
                    raise InvalidInputError(f"'{field.name}' must be >= {field.minimum}.")
                if field.maximum is not None and value > field.maximum:
                    raise InvalidInputError(f"'{field.name}' must be <= {field.maximum}.")
                clean[field.name] = int(value) if field.type == "integer" else float(value)
            elif field.type == "boolean":
                if not isinstance(value, bool):
                    raise InvalidInputError(f"'{field.name}' must be a boolean.")
                clean[field.name] = value
            else:
                if not isinstance(value, str):
                    raise InvalidInputError(f"'{field.name}' must be a string.")
                if field.choices and value not in field.choices:
                    raise InvalidInputError(
                        f"'{field.name}' must be one of {field.choices}.",
                        field=field.name,
                        choices=field.choices,
                    )
                clean[field.name] = value
        return clean

    # ------------------------------------------------------------------
    # Progression / completion
    # ------------------------------------------------------------------
    def _advance_objective(self, definition: MissionDefinition, progress: MissionProgress) -> None:
        remaining = [
            objective.id
            for objective in definition.objectives
            if not objective.optional and objective.id not in progress.completed_objectives
        ]
        progress.current_objective = remaining[0] if remaining else None

    def _is_complete(self, definition: MissionDefinition, progress: MissionProgress) -> bool:
        return all(
            objective.id in progress.completed_objectives
            for objective in definition.required_objectives()
        )

    def complete(
        self,
        state: GameState,
        definition: MissionDefinition,
        progress: MissionProgress,
        recorder: EventRecorder,
    ) -> dict[str, Any]:
        if progress.started_at is not None:
            progress.elapsed_seconds = round_to(
                max(progress.elapsed_seconds, (utcnow() - progress.started_at).total_seconds()), 2
            )

        earned = sum(
            objective.score
            for objective in definition.objectives
            if objective.id in progress.completed_objectives
        )
        optional_done = len(
            [
                objective
                for objective in definition.side_actions
                if objective.id in progress.completed_objectives
            ]
        )
        report = score_mission(
            definition,
            earned_points=earned,
            mistakes=progress.mistakes,
            elapsed_seconds=progress.elapsed_seconds,
            optional_done=optional_done,
        )

        progress.status = MissionStatus.completed
        progress.completed_at = utcnow()
        progress.score = report["score_pct"]
        progress.grade = report["grade"]
        progress.current_objective = None
        state.stats.missions_completed += 1

        self._apply_effects(state, definition.completion.effects, mission_id=definition.id)
        for mission_id in definition.completion.unlocks:
            other = state.mission(mission_id)
            if other.status == MissionStatus.locked:
                other.status = MissionStatus.available
        unlocked = self.refresh_unlocks(state)

        continuity = ContinuitySystem(state, self.catalog)
        modifiers = continuity.evaluate(definition, progress, report)

        full_report = {
            "mission_id": definition.id,
            "title": definition.title.model_dump(),
            "stage": definition.stage,
            "environment": definition.environment.value,
            "status": progress.status.value,
            "elapsed_seconds": progress.elapsed_seconds,
            "mistakes": progress.mistakes,
            "attempts": progress.attempts,
            "objectives": [
                {
                    "id": objective.id,
                    "title": objective.title.model_dump(),
                    "completed": objective.id in progress.completed_objectives,
                    "optional": objective.optional,
                    "points": objective.score,
                }
                for objective in definition.objectives
            ],
            **report,
            "resources": dict(state.resources),
            "continuity_modifiers": [m.model_dump(mode="json") for m in modifiers],
            "readiness": state.continuity.readiness,
            "readiness_grade": state.continuity.readiness_grade,
            "unlocked_missions": unlocked,
            "science_notes": [note.model_dump(mode="json") for note in definition.science_notes],
            "disclaimer": definition.disclaimer,
            "summary": definition.completion.summary.model_dump() if definition.completion.summary else None,
            "generated_at": utcnow().isoformat(),
        }

        recorder.emit(
            "mission.completed",
            {
                "mission_id": definition.id,
                "score_pct": report["score_pct"],
                "grade": report["grade"],
                "unlocked": unlocked,
                "continuity_modifiers": [m.model_dump(mode="json") for m in modifiers],
            },
            mission_id=definition.id,
        )
        logger.info(
            "mission.completed",
            extra={"mission_id": definition.id, "score_pct": report["score_pct"]},
        )
        return full_report

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------
    def mission_view(self, state: GameState, mission_id: str) -> dict[str, Any]:
        definition = self.require_definition(mission_id)
        progress = state.mission(mission_id)
        inventory = InventorySystem(state, self.catalog)
        resources = ResourceManager(state, self.catalog)

        objectives = []
        for objective in definition.objectives:
            unmet = self.unmet_conditions(state, definition, progress, objective)
            objectives.append(
                {
                    "id": objective.id,
                    "title": objective.title.model_dump(),
                    "hint": objective.hint.model_dump(),
                    "target": objective.target,
                    "animation": objective.animation,
                    "duration_ms": objective.duration_ms,
                    "optional": objective.optional,
                    "completed": objective.id in progress.completed_objectives,
                    "available": not unmet and objective.id not in progress.completed_objectives,
                    "unmet": unmet,
                    "points": objective.score,
                }
            )

        return {
            "id": definition.id,
            "stage": definition.stage,
            "title": definition.title.model_dump(),
            "summary": definition.summary.model_dump(),
            "environment": definition.environment.value,
            "status": progress.status.value,
            "current_objective": progress.current_objective,
            "completed_objectives": list(progress.completed_objectives),
            "objectives": objectives,
            "side_actions": [o.id for o in definition.side_actions],
            "attempts": progress.attempts,
            "mistakes": progress.mistakes,
            "score": progress.score,
            "grade": progress.grade,
            "elapsed_seconds": progress.elapsed_seconds,
            "requires_missions": definition.requires_missions,
            "simulation": definition.simulation.value if definition.simulation else None,
            "simulation_state": state.simulations.get(mission_id),
            "data": progress.data,
            "disclaimer": definition.disclaimer,
            "degraded_tools": inventory.degraded_tools(),
            "critical_resources": resources.critical(),
        }

    def catalogue_view(self, state: GameState) -> list[dict[str, Any]]:
        catalogue = []
        for definition in self.library.ordered():
            progress = state.mission(definition.id)
            required = len(definition.required_objectives()) or 1
            catalogue.append(
                {
                    "id": definition.id,
                    "stage": definition.stage,
                    "title": definition.title.model_dump(),
                    "summary": definition.summary.model_dump(),
                    "environment": definition.environment.value,
                    "status": progress.status.value,
                    "requires_missions": definition.requires_missions,
                    "objectives_total": required,
                    "objectives_done": len(
                        [
                            objective
                            for objective in definition.required_objectives()
                            if objective.id in progress.completed_objectives
                        ]
                    ),
                    "score": progress.score,
                    "grade": progress.grade,
                    "simulation": definition.simulation.value if definition.simulation else None,
                    "estimated_minutes": definition.estimated_minutes,
                }
            )
        return catalogue

    def interaction_map(self, state: GameState, mission_id: str) -> dict[str, Any]:
        """World objects + which interactions are currently possible (E key)."""
        definition = self.require_definition(mission_id)
        progress = state.mission(mission_id)
        objects = []
        for world_object in definition.world_objects:
            interactions = []
            for action_id in world_object.interactions:
                objective = definition.find_action(action_id)
                if objective is None:
                    continue
                unmet = self.unmet_conditions(state, definition, progress, objective)
                interactions.append(
                    {
                        "action": action_id,
                        "title": objective.title.model_dump(),
                        "animation": objective.animation,
                        "duration_ms": objective.duration_ms,
                        "available": not unmet,
                        "completed": action_id in progress.completed_objectives,
                        "unmet": unmet,
                        "payload": [field.model_dump(mode="json") for field in objective.payload],
                    }
                )
            objects.append(
                {
                    "id": world_object.id,
                    "label": world_object.label.model_dump(),
                    "kind": world_object.kind,
                    "position": world_object.position,
                    "prompt": world_object.prompt.model_dump() if world_object.prompt else None,
                    "interactions": interactions,
                }
            )
        return {"mission_id": mission_id, "objects": objects}

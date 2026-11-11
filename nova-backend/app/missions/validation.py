"""Static validation of the mission content pack.

Mission design lives in JSON, which means a typo can silently *lock a whole
stage*: a condition that reads ``device.repaired`` while no action ever writes
that path is not a crash – it is an unwinnable game. This module reasons about
the content the same way the engine does and reports those dead ends before a
player ever hits them.

Checks
------
* references: items, resources, missions, objectives, world objects, hazards
* ordering: an objective may only depend on something reachable before it
* world/flag conditions: some reachable action must be able to produce the value
* payload placeholders (``{{payload.x}}``) must be declared fields
* continuity rules must target real resources / tools / missions
* campaign graph: acyclic, every mission reachable from an entry mission
* localisation and scoring sanity (warnings)

Run it from the CLI (``python scripts/validate_content.py``), over HTTP
(``GET /api/v1/content/validation``) or in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.utils import deep_get
from app.domain.enums import SimulationKind
from app.missions.definitions import (
    Condition,
    Effects,
    MissionDefinition,
    Objective,
)
from app.missions.registry import GameContent

ERROR = "error"
WARNING = "warning"

#: Written by services (action hooks), not by mission JSON.
HOOK_WORLD_PATHS: dict[str, list[str]] = {
    "martian_guide": [
        "geology.last_sample_id",
        "geology.last_rock_type",
        "geology.logged_sample_id",
    ],
    "earth_trends": ["earth.verdict", "earth.mean_delta", "earth.report_id"],
}

#: Set by the inventory system when a consumable is used (F key).
CONSUMABLE_FLAGS = {"water_filter_fresh", "equipment_cooled"}

_COMPARATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) is not None and _num(a) > _num(b),
    "gte": lambda a, b: _num(a) is not None and _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) is not None and _num(a) < _num(b),
    "lte": lambda a, b: _num(a) is not None and _num(a) <= _num(b),
}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(slots=True)
class Issue:
    severity: str
    code: str
    message: str
    mission_id: str | None = None
    where: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "mission_id": self.mission_id,
            "where": self.where,
        }


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    missions_checked: int = 0
    actions_checked: int = 0

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        mission_id: str | None = None,
        where: str | None = None,
    ) -> None:
        self.issues.append(Issue(severity, code, message, mission_id, where))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missions_checked": self.missions_checked,
            "actions_checked": self.actions_checked,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def render(self) -> str:
        lines = [
            f"{'✗' if i.severity == ERROR else '!'} [{i.code}] "
            f"{i.mission_id or '-'}/{i.where or '-'}: {i.message}"
            for i in self.issues
        ]
        lines.append(
            f"{self.missions_checked} missions / {self.actions_checked} actions checked – "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Effect harvesting
# ---------------------------------------------------------------------------
def _harvest(effects: Effects, world: dict[str, list[Any]], flags: set[str]) -> None:
    for path, value in effects.world.items():
        world.setdefault(path, []).append(value)
    flags.update(effects.flags_set)


def _mission_writers(definition: MissionDefinition) -> tuple[dict[str, list[Any]], set[str]]:
    world: dict[str, list[Any]] = {}
    flags: set[str] = set()
    for objective in (*definition.objectives, *definition.side_actions):
        _harvest(objective.effects, world, flags)
    _harvest(definition.completion.effects, world, flags)
    for hazard in definition.hazards:
        _harvest(hazard.effects, world, flags)
    for path in HOOK_WORLD_PATHS.get(definition.id, []):
        world.setdefault(path, []).append("*")
    return world, flags


def _satisfies(written: list[Any], op: str, expected: Any) -> bool:
    for value in written:
        if value == "*":  # produced at runtime by a service hook
            return True
        if isinstance(value, str) and value.startswith("{{payload."):
            return True  # player-supplied, assume any value is possible
        try:
            if _COMPARATORS[op](value, expected):
                return True
        except TypeError:  # pragma: no cover - defensive against odd content
            continue
    return False


def _is_wildcard(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("{{payload.")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _check_condition_references(
    report: ValidationReport,
    definition: MissionDefinition,
    objective: Objective,
    condition: Condition,
    content: GameContent,
    action_ids: set[str],
) -> None:
    where = f"{objective.id}.requires"
    kind = condition.type
    if kind == "item" and condition.id not in content.catalog.items:
        report.add(ERROR, "unknown_item", f"Condition references unknown item '{condition.id}'.",
                   mission_id=definition.id, where=where)
    elif kind == "resource" and condition.id not in content.catalog.resources:
        report.add(ERROR, "unknown_resource", f"Condition references unknown resource '{condition.id}'.",
                   mission_id=definition.id, where=where)
    elif kind == "objective" and condition.id not in action_ids:
        report.add(ERROR, "unknown_objective", f"Condition references unknown objective '{condition.id}'.",
                   mission_id=definition.id, where=where)
    elif kind == "mission" and condition.id not in content.missions.missions:
        report.add(ERROR, "unknown_mission", f"Condition references unknown mission '{condition.id}'.",
                   mission_id=definition.id, where=where)
    elif kind == "equipment":
        item = content.catalog.item(condition.id)
        if item is None:
            report.add(ERROR, "unknown_item", f"Equipment condition references unknown item '{condition.id}'.",
                       mission_id=definition.id, where=where)
        elif not item.durable:
            report.add(WARNING, "not_durable", f"'{condition.id}' is not durable; condition is always 100%.",
                       mission_id=definition.id, where=where)
    elif kind == "sim" and definition.simulation is None:
        report.add(ERROR, "sim_without_simulation",
                   f"Condition reads simulation path '{condition.path}' but the mission has no simulation.",
                   mission_id=definition.id, where=where)


def _check_effects(
    report: ValidationReport,
    definition: MissionDefinition,
    objective: Objective,
    content: GameContent,
) -> None:
    where = f"{objective.id}.effects"
    effects = objective.effects
    for item_id in (*effects.items, *effects.equipment_wear, *effects.equipment_restore):
        if item_id not in content.catalog.items:
            report.add(ERROR, "unknown_item", f"Effect references unknown item '{item_id}'.",
                       mission_id=definition.id, where=where)
    for resource_id in effects.resources:
        if resource_id not in content.catalog.resources:
            report.add(ERROR, "unknown_resource", f"Effect references unknown resource '{resource_id}'.",
                       mission_id=definition.id, where=where)
    for mission_id in effects.unlock:
        if mission_id not in content.missions.missions:
            report.add(ERROR, "unknown_mission", f"Effect unlocks unknown mission '{mission_id}'.",
                       mission_id=definition.id, where=where)
    declared = {field.name for field in objective.payload}
    for path, value in effects.world.items():
        if _is_wildcard(value):
            name = value[len("{{payload.") : -2]
            if name not in declared:
                report.add(ERROR, "undeclared_payload",
                           f"World effect '{path}' uses payload field '{name}' that is not declared.",
                           mission_id=definition.id, where=where)
    for field_spec in objective.payload:
        if field_spec.choices and field_spec.default is not None and field_spec.default not in field_spec.choices:
            report.add(ERROR, "payload_default",
                       f"Payload '{field_spec.name}' default is not one of its choices.",
                       mission_id=definition.id, where=f"{objective.id}.payload")


def _check_reachability(
    report: ValidationReport,
    definition: MissionDefinition,
    global_world: dict[str, list[Any]],
    global_flags: set[str],
) -> None:
    """Can every mandatory objective eventually be performed?

    Economy (items/resources) is deliberately ignored here – it is the player's
    job to bring the right gear. Ordering, flags and world state are *design*
    constraints, so a dead end there is a content bug.
    """
    objectives = list(definition.objectives)
    done: set[str] = set()
    world: dict[str, list[Any]] = {}
    flags: set[str] = set(CONSUMABLE_FLAGS)

    def blocked(objective: Objective) -> list[str]:
        reasons: list[str] = []
        if objective.require_previous:
            for previous in objectives:
                if previous is objective:
                    break
                if not previous.optional and previous.id not in done:
                    reasons.append(f"objective '{previous.id}' not reachable yet")
        for condition in objective.requires:
            if condition.type == "objective" and condition.id not in done:
                reasons.append(f"objective '{condition.id}' not reachable yet")
            elif condition.type == "flag":
                available = condition.id in flags or condition.id in global_flags
                if available != condition.value and condition.value:
                    reasons.append(f"flag '{condition.id}' is never set")
            elif condition.type == "world":
                written = world.get(condition.path, []) + global_world.get(condition.path, [])
                if not _satisfies(written, condition.op, condition.value):
                    reasons.append(
                        f"world path '{condition.path}' is never written with a value that satisfies "
                        f"{condition.op} {condition.value!r}"
                    )
        return reasons

    progressed = True
    while progressed:
        progressed = False
        for objective in objectives:
            if objective.id in done:
                continue
            if blocked(objective):
                continue
            done.add(objective.id)
            _harvest(objective.effects, world, flags)
            progressed = True

    for objective in objectives:
        if objective.id in done:
            continue
        reasons = blocked(objective)
        if objective.optional:
            report.add(WARNING, "optional_unreachable",
                       f"Optional objective can never be performed: {'; '.join(reasons)}.",
                       mission_id=definition.id, where=objective.id)
        else:
            report.add(ERROR, "objective_unreachable",
                       f"Mandatory objective can never be performed: {'; '.join(reasons)}.",
                       mission_id=definition.id, where=objective.id)

    if not all(o.id in done for o in definition.required_objectives()):
        report.add(ERROR, "mission_unwinnable",
                   "Mission cannot be completed with this content.",
                   mission_id=definition.id, where="objectives")


def _check_hazards(
    report: ValidationReport, definition: MissionDefinition, action_ids: set[str]
) -> None:
    for hazard in definition.hazards:
        if hazard.action not in action_ids:
            report.add(ERROR, "unknown_hazard_action",
                       f"Hazard references unknown action '{hazard.action}'.",
                       mission_id=definition.id, where="hazards")
            continue
        objective = definition.find_action(hazard.action)
        assert objective is not None
        # ``missing_any`` matches the ids reported in ``unmet``: condition ids
        # for items/flags/objectives, and *paths* for world/sim conditions.
        referenced = {getattr(c, "id", None) for c in objective.requires}
        referenced |= {getattr(c, "path", None) for c in objective.requires}
        referenced |= {o.id for o in definition.objectives}
        for missing in hazard.missing_any:
            if missing not in referenced:
                report.add(WARNING, "hazard_never_triggers",
                           f"Hazard for '{hazard.action}' watches '{missing}', which is not a "
                           "precondition of that action.",
                           mission_id=definition.id, where="hazards")


def _check_continuity(report: ValidationReport, definition: MissionDefinition, content: GameContent) -> None:
    known_metrics = {"score_pct", "mistakes", "elapsed_seconds", "objectives_pct"}
    for rule in definition.completion.continuity:
        metric = rule.metric
        ok = metric in known_metrics or metric.startswith(
            ("stat:", "equipment:", "resource:", "resource_pct:", "sim:", "flag:", "world:")
        )
        if not ok:
            report.add(ERROR, "unknown_metric", f"Continuity rule uses unknown metric '{metric}'.",
                       mission_id=definition.id, where=f"continuity.{rule.key}")
        if rule.kind == "resource" and rule.key.rsplit(":", 1)[-1] not in content.catalog.resources:
            report.add(ERROR, "continuity_resource",
                       f"Continuity rule '{rule.key}' does not resolve to a known resource.",
                       mission_id=definition.id, where=f"continuity.{rule.key}")
        if rule.kind == "equipment" and rule.key.rsplit(":", 1)[-1] not in content.catalog.items:
            report.add(ERROR, "continuity_item",
                       f"Continuity rule '{rule.key}' does not resolve to a known item.",
                       mission_id=definition.id, where=f"continuity.{rule.key}")
        if rule.kind == "unlock" and rule.target and rule.target not in content.missions.missions:
            report.add(ERROR, "continuity_unlock", f"Continuity rule unlocks unknown mission '{rule.target}'.",
                       mission_id=definition.id, where=f"continuity.{rule.key}")
        if not rule.bands:
            report.add(WARNING, "continuity_no_bands", f"Continuity rule '{rule.key}' has no bands.",
                       mission_id=definition.id, where=f"continuity.{rule.key}")


def _check_world_objects(report: ValidationReport, definition: MissionDefinition, action_ids: set[str]) -> None:
    seen: set[str] = set()
    for obj in definition.world_objects:
        if obj.id in seen:
            report.add(ERROR, "duplicate_world_object", f"Duplicate world object id '{obj.id}'.",
                       mission_id=definition.id, where="world_objects")
        seen.add(obj.id)
        if len(obj.position) != 3:
            report.add(ERROR, "bad_position", f"World object '{obj.id}' position must have 3 numbers.",
                       mission_id=definition.id, where="world_objects")
        for action in obj.interactions:
            if action not in action_ids:
                report.add(ERROR, "unknown_interaction",
                           f"World object '{obj.id}' exposes unknown action '{action}'.",
                           mission_id=definition.id, where="world_objects")
    covered = {a for obj in definition.world_objects for a in obj.interactions}
    for objective in definition.objectives:
        if objective.id not in covered:
            report.add(WARNING, "no_world_object",
                       f"Objective '{objective.id}' is not attached to any world object; the 3D client "
                       "cannot show a 'Press E' prompt for it.",
                       mission_id=definition.id, where=objective.id)


def _check_campaign_graph(report: ValidationReport, content: GameContent) -> None:
    missions = content.missions.missions
    state: dict[str, int] = {}

    def visit(mission_id: str, trail: list[str]) -> None:
        if state.get(mission_id) == 2:
            return
        if state.get(mission_id) == 1:
            report.add(ERROR, "dependency_cycle", f"Mission dependency cycle: {' → '.join([*trail, mission_id])}.",
                       mission_id=mission_id, where="requires_missions")
            return
        state[mission_id] = 1
        for dependency in missions[mission_id].requires_missions:
            if dependency in missions:
                visit(dependency, [*trail, mission_id])
        state[mission_id] = 2

    for mission_id in missions:
        visit(mission_id, [])

    entries = [m.id for m in missions.values() if not m.requires_missions]
    if not entries:
        report.add(ERROR, "no_entry_mission", "No mission is available at the start of the campaign.")

    reachable: set[str] = set(entries)
    changed = True
    while changed:
        changed = False
        for definition in missions.values():
            if definition.id in reachable:
                continue
            if all(dep in reachable for dep in definition.requires_missions):
                reachable.add(definition.id)
                changed = True
    for mission_id in missions:
        if mission_id not in reachable:
            report.add(ERROR, "unreachable_mission", "Mission can never be unlocked.", mission_id=mission_id)


def _check_text(report: ValidationReport, definition: MissionDefinition) -> None:
    if not definition.summary.ar or not definition.title.ar:
        report.add(WARNING, "missing_arabic", "Mission title/summary has no Arabic localisation.",
                   mission_id=definition.id, where="title")
    if not definition.briefing:
        report.add(WARNING, "no_briefing", "Mission has no briefing lines.", mission_id=definition.id)
    if definition.scoring.pass_score_pct > 100 or definition.scoring.pass_score_pct < 0:
        report.add(ERROR, "bad_pass_score", "pass_score_pct must be between 0 and 100.",
                   mission_id=definition.id, where="scoring")
    if definition.simulation == SimulationKind.crew_vitals and not definition.disclaimer:
        report.add(ERROR, "missing_disclaimer",
                   "Medical simulations must carry an explicit 'educational simulation' disclaimer.",
                   mission_id=definition.id, where="disclaimer")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def validate_content(content: GameContent) -> ValidationReport:
    """Validate the whole content pack. Never raises for content problems."""
    report = ValidationReport()
    definitions = content.missions.ordered()

    global_world: dict[str, list[Any]] = {}
    global_flags: set[str] = set()
    writers = {d.id: _mission_writers(d) for d in definitions}
    for world, flags in writers.values():
        for path, values in world.items():
            global_world.setdefault(path, []).extend(values)
        global_flags |= flags

    _check_campaign_graph(report, content)

    for definition in definitions:
        report.missions_checked += 1
        actions = (*definition.objectives, *definition.side_actions)
        action_ids = {o.id for o in actions}
        report.actions_checked += len(actions)

        for objective in actions:
            for condition in objective.requires:
                _check_condition_references(report, definition, objective, condition, content, action_ids)
            _check_effects(report, definition, objective, content)
            if not objective.hint.en:
                report.add(WARNING, "no_hint", f"Objective '{objective.id}' has no player hint.",
                           mission_id=definition.id, where=objective.id)

        _check_effects(report, definition, _completion_as_objective(definition), content)
        _check_hazards(report, definition, action_ids)
        _check_continuity(report, definition, content)
        _check_world_objects(report, definition, action_ids)
        _check_text(report, definition)

        other_world = {
            path: values
            for mission_id, (world, _) in writers.items()
            if mission_id != definition.id
            for path, values in world.items()
        }
        other_flags = set().union(
            *(flags for mission_id, (_, flags) in writers.items() if mission_id != definition.id)
        )
        _check_reachability(report, definition, other_world, other_flags)

    return report


def _completion_as_objective(definition: MissionDefinition) -> Objective:
    """Adapt completion effects so they can reuse the effect checker."""
    return Objective(
        id="__completion__",
        title=definition.title,
        hint=definition.summary,
        effects=definition.completion.effects,
    )


def world_snapshot(state_world: dict[str, Any], path: str) -> Any:
    """Public helper mirroring how the engine reads world conditions."""
    return deep_get(state_world, path)

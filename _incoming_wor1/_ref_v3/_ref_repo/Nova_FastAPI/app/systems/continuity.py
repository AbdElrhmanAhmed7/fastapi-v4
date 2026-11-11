"""Mission Continuity System.

Performance in one stage changes the *next* stage: equipment prep quality,
water/power discipline, neglected maintenance and training grades all produce
explicit, explainable modifiers.

Design rules:
* every modifier carries a human readable reason (shown in the mission report),
* resource/equipment modifiers are applied when the next mission starts,
* readiness/hazard/unlock modifiers apply immediately,
* nothing is hidden random – the same play produces the same carry-over.

The numbers are a **gameplay simplification**, not a real mission model.
"""

from __future__ import annotations

from typing import Any

from app.core.utils import clamp, deep_get, round_to
from app.domain.catalog import Catalog, LocalisedText
from app.domain.enums import MissionStatus
from app.domain.state import ContinuityModifier, GameState, MissionProgress
from app.missions.definitions import ContinuityRule, MissionDefinition
from app.systems.resource_manager import ResourceManager
from app.systems.scoring import grade_for, readiness_from_scores


class ContinuitySystem:
    def __init__(self, state: GameState, catalog: Catalog) -> None:
        self.state = state
        self.catalog = catalog
        self.resources = ResourceManager(state, catalog)

    # -- metric resolution --------------------------------------------------
    def resolve_metric(
        self, metric: str, definition: MissionDefinition, progress: MissionProgress, report: dict[str, Any]
    ) -> float:
        if metric == "score_pct":
            return float(report.get("score_pct", 0.0))
        if metric == "mistakes":
            return float(progress.mistakes)
        if metric == "elapsed_seconds":
            return float(progress.elapsed_seconds)
        if metric == "objectives_pct":
            required = len(definition.required_objectives()) or 1
            done = len([o for o in progress.completed_objectives if o in {x.id for x in definition.required_objectives()}])
            return round_to(100.0 * done / required, 2)
        if metric.startswith("flag:"):
            return 1.0 if self.state.has_flag(metric[5:]) else 0.0
        if metric.startswith("world:"):
            return float(deep_get(self.state.world, metric[6:], 0) or 0)
        if metric.startswith("stat:"):
            return float(getattr(self.state.stats, metric[5:], 0) or 0)
        if metric.startswith("equipment:"):
            equipment = self.state.equipment.get(metric[10:])
            return float(equipment.condition if equipment else 100)
        if metric.startswith("resource_pct:"):
            return round_to(100.0 * self.resources.ratio(metric[13:]), 2)
        if metric.startswith("resource:"):
            return float(self.resources.get(metric[9:]))
        if metric.startswith("sim:"):
            simulation = self.state.simulations.get(definition.id, {})
            value = deep_get(simulation, metric[4:], 0)
            return float(value if isinstance(value, (int, float)) else 0)
        return 0.0

    # -- evaluation ---------------------------------------------------------
    def evaluate(
        self, definition: MissionDefinition, progress: MissionProgress, report: dict[str, Any]
    ) -> list[ContinuityModifier]:
        produced: list[ContinuityModifier] = []
        for rule in definition.completion.continuity:
            metric_value = self.resolve_metric(rule.metric, definition, progress, report)
            band = next((b for b in rule.bands if metric_value >= b.min), None)
            if band is None or band.value == 0:
                continue
            modifier = ContinuityModifier(
                key=rule.key,
                label=rule.label.en,
                value=round_to(band.value, 2),
                source_mission=definition.id,
                reason=band.reason.en or rule.label.en,
                kind=rule.kind,
            )
            produced.append(modifier)
            self._apply_immediate(rule, modifier)

        # Keep only the newest modifier per key so effects never stack forever.
        merged: dict[str, ContinuityModifier] = {m.key: m for m in self.state.continuity.modifiers}
        for modifier in produced:
            merged[modifier.key] = modifier
        self.state.continuity.modifiers = list(merged.values())

        self.state.continuity.history.append(
            {
                "mission_id": definition.id,
                "score_pct": report.get("score_pct", 0),
                "grade": report.get("grade"),
                "modifiers": [m.model_dump(mode="json") for m in produced],
            }
        )
        self.recompute_readiness()
        return produced

    def _apply_immediate(self, rule: ContinuityRule, modifier: ContinuityModifier) -> None:
        if rule.kind == "readiness":
            self.state.continuity.readiness = clamp(
                self.state.continuity.readiness + modifier.value, 0.0, 100.0
            )
            modifier.applied = True
        elif rule.kind == "hazard":
            hazards = self.state.world.setdefault("hazards", {})
            hazards[rule.key] = modifier.value
            self.state.set_flag(rule.key, modifier.value > 0)
            modifier.applied = True
        elif rule.kind == "unlock" and rule.target and modifier.value > 0:
            progress = self.state.mission(rule.target)
            if progress.status == MissionStatus.locked:
                progress.status = MissionStatus.available
            modifier.applied = True

    # -- deferred application ----------------------------------------------
    def apply_pending(self, starting_mission: str) -> list[dict[str, Any]]:
        """Apply resource/equipment carry-over when a new mission begins."""
        applied: list[dict[str, Any]] = []
        for modifier in self.state.continuity.modifiers:
            if modifier.applied or modifier.kind not in {"resource", "equipment"}:
                continue
            if modifier.source_mission == starting_mission:
                continue
            if modifier.kind == "resource" and modifier.key:
                target = self._target_resource(modifier)
                if target is None:
                    continue
                changes = self.resources.apply({target: int(round(modifier.value))})
                applied.append({"kind": "resource", "key": modifier.key, "changes": changes, "reason": modifier.reason})
            elif modifier.kind == "equipment":
                target_item = self._target_item(modifier)
                if target_item is None:
                    continue
                equipment = self.state.equipment.get(target_item)
                if equipment is None:
                    continue
                before = equipment.condition
                equipment.condition = clamp(int(equipment.condition + modifier.value), 0, 100)
                applied.append(
                    {
                        "kind": "equipment",
                        "key": modifier.key,
                        "item": target_item,
                        "before": before,
                        "after": equipment.condition,
                        "reason": modifier.reason,
                    }
                )
            modifier.applied = True

        if starting_mission not in self.state.continuity.applied_missions:
            self.state.continuity.applied_missions.append(starting_mission)
        return applied

    def _target_resource(self, modifier: ContinuityModifier) -> str | None:
        # Convention: "<something>:<resource_id>" or a bare resource id.
        candidate = modifier.key.rsplit(":", 1)[-1]
        if candidate in self.catalog.resources:
            return candidate
        return None

    def _target_item(self, modifier: ContinuityModifier) -> str | None:
        candidate = modifier.key.rsplit(":", 1)[-1]
        return candidate if candidate in self.catalog.items else None

    # -- readiness ----------------------------------------------------------
    def recompute_readiness(self) -> float:
        scores = [
            float(entry.get("score_pct", 0.0))
            for entry in self.state.continuity.history
            if entry.get("score_pct") is not None
        ]
        blended = readiness_from_scores(scores, base=self.state.continuity.readiness)
        self.state.continuity.readiness = blended
        self.state.continuity.readiness_grade = grade_for(blended)
        return blended

    def summary(self) -> dict[str, Any]:
        return {
            "readiness": self.state.continuity.readiness,
            "readiness_grade": self.state.continuity.readiness_grade,
            "modifiers": [m.model_dump(mode="json") for m in self.state.continuity.modifiers],
            "pending": [m.model_dump(mode="json") for m in self.state.continuity.modifiers if not m.applied],
            "history": self.state.continuity.history,
            "note": LocalisedText(
                en="Carry-over effects are a simplified gameplay model, not real mission planning data.",
                ar="تأثيرات الانتقال بين المراحل نموذج لعب مبسط وليست بيانات تخطيط مهام حقيقية.",
            ).model_dump(),
        }

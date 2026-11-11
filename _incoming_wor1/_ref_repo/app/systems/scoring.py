"""Scoring, grading and readiness maths (pure functions, easy to unit test)."""

from __future__ import annotations

from typing import Any

from app.core.utils import clamp, round_to
from app.domain.enums import Grade
from app.missions.definitions import MissionDefinition, ScoringConfig


def grade_for(score_pct: float) -> str:
    if score_pct >= 95:
        return Grade.S.value
    if score_pct >= 85:
        return Grade.A.value
    if score_pct >= 70:
        return Grade.B.value
    if score_pct >= 50:
        return Grade.C.value
    return Grade.D.value


def score_mission(
    definition: MissionDefinition,
    *,
    earned_points: float,
    mistakes: int,
    elapsed_seconds: float,
    optional_done: int = 0,
) -> dict[str, Any]:
    """Combine objective points, penalties, time and efficiency into a score."""
    config: ScoringConfig = definition.scoring
    max_score = max(definition.max_score, 1.0)

    penalty = mistakes * config.mistake_penalty
    time_bonus = 0.0
    if config.time_target_seconds > 0 and elapsed_seconds > 0:
        ratio = clamp(1.0 - elapsed_seconds / config.time_target_seconds, 0.0, 1.0)
        time_bonus = round_to(ratio * config.time_bonus_max, 2)
    efficiency_bonus = round_to(optional_done * config.efficiency_bonus, 2)

    raw = earned_points - penalty + time_bonus + efficiency_bonus
    score_pct = round_to(clamp(100.0 * raw / max_score, 0.0, 100.0), 2)

    return {
        "earned_points": round_to(earned_points, 2),
        "max_points": round_to(max_score, 2),
        "mistake_penalty": round_to(penalty, 2),
        "time_bonus": time_bonus,
        "efficiency_bonus": efficiency_bonus,
        "score": round_to(clamp(raw, 0.0, max_score + config.time_bonus_max + efficiency_bonus), 2),
        "score_pct": score_pct,
        "grade": grade_for(score_pct),
        "passed": score_pct >= config.pass_score_pct,
    }


def readiness_from_scores(scores: list[float], *, base: float = 50.0) -> float:
    """Blend mission performance into a 0-100 mission-readiness index."""
    if not scores:
        return round_to(base, 2)
    weighted = sum(score * (index + 1) for index, score in enumerate(scores))
    total_weight = sum(range(1, len(scores) + 1))
    performance = weighted / total_weight
    return round_to(clamp(0.4 * base + 0.6 * performance, 0.0, 100.0), 2)

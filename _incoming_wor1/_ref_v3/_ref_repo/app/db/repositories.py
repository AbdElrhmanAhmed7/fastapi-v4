"""Repository layer – the only place that talks SQL.

Services stay database-agnostic; swapping SQLite for Postgres touches nothing
above this file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import utcnow
from app.db.models import (
    EarthReportRow,
    EventRow,
    GameSessionRow,
    IdempotencyRow,
    MissionResultRow,
    ScienceSampleRow,
    SimulationRunRow,
)
from app.domain.state import GameState


class GameRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, state: GameState) -> None:
        row = GameSessionRow(
            game_id=state.game_id,
            player_name=state.player_name,
            difficulty=state.difficulty.value,
            environment=state.environment.value,
            seed=state.seed,
            schema_version=state.schema_version,
            version=state.version,
            state=state.model_dump(mode="json"),
            readiness=state.continuity.readiness,
            missions_completed=state.stats.missions_completed,
            finished=state.finished,
            created_at=state.created_at,
            updated_at=state.updated_at,
            last_seen_at=utcnow(),
        )
        self.session.add(row)
        await self.session.flush()

    async def load(self, game_id: str) -> GameState | None:
        row = await self.session.get(GameSessionRow, game_id)
        if row is None:
            return None
        row.last_seen_at = utcnow()
        return GameState.model_validate(row.state)

    async def save(self, state: GameState, expected_version: int) -> bool:
        """Optimistic-concurrency save. Returns False on a version conflict."""
        state.version = expected_version + 1
        state.touch()
        result = await self.session.execute(
            update(GameSessionRow)
            .where(
                GameSessionRow.game_id == state.game_id,
                GameSessionRow.version == expected_version,
            )
            .values(
                state=state.model_dump(mode="json"),
                version=state.version,
                environment=state.environment.value,
                readiness=state.continuity.readiness,
                missions_completed=state.stats.missions_completed,
                finished=state.finished,
                updated_at=state.updated_at,
                last_seen_at=utcnow(),
            )
        )
        return bool(result.rowcount)

    async def exists(self, game_id: str) -> bool:
        stmt = select(GameSessionRow.game_id).where(GameSessionRow.game_id == game_id)
        return (await self.session.execute(stmt)).first() is not None

    async def list(
        self, *, player_name: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        stmt = select(GameSessionRow).order_by(desc(GameSessionRow.updated_at))
        if player_name:
            stmt = stmt.where(GameSessionRow.player_name == player_name)
        rows = (await self.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        return [
            {
                "game_id": row.game_id,
                "player_name": row.player_name,
                "difficulty": row.difficulty,
                "environment": row.environment,
                "readiness": row.readiness,
                "missions_completed": row.missions_completed,
                "finished": row.finished,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "last_seen_at": row.last_seen_at,
            }
            for row in rows
        ]

    async def delete(self, game_id: str) -> bool:
        result = await self.session.execute(
            delete(GameSessionRow).where(GameSessionRow.game_id == game_id)
        )
        return bool(result.rowcount)

    async def count(self) -> int:
        return int((await self.session.execute(select(func.count(GameSessionRow.game_id)))).scalar() or 0)

    async def purge_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(GameSessionRow).where(GameSessionRow.last_seen_at < cutoff)
        )
        return int(result.rowcount or 0)

    async def leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        stmt = (
            select(
                GameSessionRow.game_id,
                GameSessionRow.player_name,
                GameSessionRow.readiness,
                GameSessionRow.missions_completed,
                func.coalesce(func.avg(MissionResultRow.score_pct), 0.0).label("avg_score"),
            )
            .join(MissionResultRow, MissionResultRow.game_id == GameSessionRow.game_id, isouter=True)
            .group_by(GameSessionRow.game_id)
            .order_by(desc(GameSessionRow.missions_completed), desc("avg_score"))
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "rank": index + 1,
                "game_id": row.game_id,
                "player_name": row.player_name,
                "missions_completed": row.missions_completed,
                "readiness": round(float(row.readiness), 2),
                "average_score_pct": round(float(row.avg_score or 0.0), 2),
            }
            for index, row in enumerate(rows)
        ]


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, game_id: str, events: list[dict[str, Any]]) -> None:
        for event in events:
            self.session.add(
                EventRow(
                    game_id=game_id,
                    seq=int(event["seq"]),
                    type=str(event["type"]),
                    mission_id=event.get("mission_id"),
                    payload=event.get("payload", {}),
                )
            )
        if events:
            await self.session.flush()

    async def list(
        self, game_id: str, *, after_seq: int = 0, limit: int = 100, types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        stmt = (
            select(EventRow)
            .where(EventRow.game_id == game_id, EventRow.seq > after_seq)
            .order_by(EventRow.seq)
            .limit(limit)
        )
        if types:
            stmt = stmt.where(EventRow.type.in_(types))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            {
                "seq": row.seq,
                "type": row.type,
                "mission_id": row.mission_id,
                "payload": row.payload,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def trim(self, game_id: str, keep_last: int) -> int:
        newest = (
            await self.session.execute(
                select(func.max(EventRow.seq)).where(EventRow.game_id == game_id)
            )
        ).scalar()
        if not newest or newest <= keep_last:
            return 0
        result = await self.session.execute(
            delete(EventRow).where(EventRow.game_id == game_id, EventRow.seq <= newest - keep_last)
        )
        return int(result.rowcount or 0)


class MissionResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, game_id: str, report: dict[str, Any]) -> None:
        mission_id = report["mission_id"]
        existing = (
            await self.session.execute(
                select(MissionResultRow).where(
                    MissionResultRow.game_id == game_id, MissionResultRow.mission_id == mission_id
                )
            )
        ).scalar_one_or_none()

        values = {
            "stage": report.get("stage", 0),
            "score_pct": report.get("score_pct", 0.0),
            "grade": report.get("grade", "D"),
            "mistakes": report.get("mistakes", 0),
            "elapsed_seconds": report.get("elapsed_seconds", 0.0),
            "report": report,
            "completed_at": utcnow(),
        }
        if existing is None:
            self.session.add(MissionResultRow(game_id=game_id, mission_id=mission_id, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        await self.session.flush()

    async def list(self, game_id: str) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(MissionResultRow)
                .where(MissionResultRow.game_id == game_id)
                .order_by(MissionResultRow.stage)
            )
        ).scalars().all()
        return [row.report for row in rows]

    async def get(self, game_id: str, mission_id: str) -> dict[str, Any] | None:
        row = (
            await self.session.execute(
                select(MissionResultRow).where(
                    MissionResultRow.game_id == game_id, MissionResultRow.mission_id == mission_id
                )
            )
        ).scalar_one_or_none()
        return row.report if row else None


class ScienceSampleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: dict[str, Any]) -> None:
        self.session.add(ScienceSampleRow(**payload))
        await self.session.flush()

    async def get(self, sample_id: str) -> ScienceSampleRow | None:
        return await self.session.get(ScienceSampleRow, sample_id)

    async def list(self, game_id: str) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(ScienceSampleRow)
                .where(ScienceSampleRow.game_id == game_id)
                .order_by(ScienceSampleRow.collected_at)
            )
        ).scalars().all()
        return [self._serialise(row) for row in rows]

    async def analyse(self, sample_id: str, analysis: dict[str, Any], rock_type: str) -> bool:
        row = await self.get(sample_id)
        if row is None:
            return False
        row.analysis = analysis
        row.rock_type = rock_type
        row.analysed_at = utcnow()
        await self.session.flush()
        return True

    @staticmethod
    def _serialise(row: ScienceSampleRow) -> dict[str, Any]:
        return {
            "sample_id": row.sample_id,
            "game_id": row.game_id,
            "mission_id": row.mission_id,
            "site": row.site,
            "rock_type": row.rock_type,
            "composition": row.composition,
            "analysis": row.analysis,
            "collected_at": row.collected_at,
            "analysed_at": row.analysed_at,
            "notes": row.notes,
        }


class SimulationRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        run_id: str,
        game_id: str,
        mission_id: str,
        kind: str,
        status: str,
        score_pct: float,
        result: dict[str, Any],
    ) -> None:
        row = await self.session.get(SimulationRunRow, run_id)
        if row is None:
            self.session.add(
                SimulationRunRow(
                    run_id=run_id,
                    game_id=game_id,
                    mission_id=mission_id,
                    kind=kind,
                    status=status,
                    score_pct=score_pct,
                    result=result,
                    finished_at=utcnow() if status in {"succeeded", "failed"} else None,
                )
            )
        else:
            row.status = status
            row.score_pct = score_pct
            row.result = result
            if status in {"succeeded", "failed"}:
                row.finished_at = utcnow()
        await self.session.flush()

    async def list(self, game_id: str, mission_id: str | None = None) -> list[dict[str, Any]]:
        stmt = select(SimulationRunRow).where(SimulationRunRow.game_id == game_id)
        if mission_id:
            stmt = stmt.where(SimulationRunRow.mission_id == mission_id)
        rows = (await self.session.execute(stmt.order_by(SimulationRunRow.started_at))).scalars().all()
        return [
            {
                "run_id": row.run_id,
                "mission_id": row.mission_id,
                "kind": row.kind,
                "status": row.status,
                "score_pct": row.score_pct,
                "result": row.result,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
            }
            for row in rows
        ]


class EarthReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: dict[str, Any]) -> None:
        self.session.add(EarthReportRow(**payload))
        await self.session.flush()

    async def get(self, report_id: str) -> dict[str, Any] | None:
        row = await self.session.get(EarthReportRow, report_id)
        return self._serialise(row) if row else None

    async def list(self, game_id: str) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(EarthReportRow)
                .where(EarthReportRow.game_id == game_id)
                .order_by(EarthReportRow.created_at)
            )
        ).scalars().all()
        return [self._serialise(row) for row in rows]

    @staticmethod
    def _serialise(row: EarthReportRow) -> dict[str, Any]:
        return {
            "report_id": row.report_id,
            "game_id": row.game_id,
            "region_id": row.region_id,
            "layer_id": row.layer_id,
            "period_before": row.period_before,
            "period_after": row.period_after,
            "provenance": row.provenance,
            "verdict": row.verdict,
            "mean_delta": row.mean_delta,
            "analysis": row.analysis,
            "created_at": row.created_at,
        }


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self.session.get(IdempotencyRow, key)
        return row.response if row else None

    async def store(self, key: str, game_id: str, response: dict[str, Any]) -> None:
        self.session.add(IdempotencyRow(key=key, game_id=game_id, response=response))
        await self.session.flush()

    async def purge_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(IdempotencyRow).where(IdempotencyRow.created_at < cutoff)
        )
        return int(result.rowcount or 0)

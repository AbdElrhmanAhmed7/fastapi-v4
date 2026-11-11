"""Game orchestration: persistence + domain systems + mission engine.

Every mutating operation runs inside :meth:`GameService.transaction`, which
* serialises concurrent requests for the same game (async lock),
* loads and validates the save document,
* records events,
* and commits with optimistic-concurrency protection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import (
    InvalidInputError,
    SessionLimitError,
    SessionNotFoundError,
    StateConflictError,
)
from app.core.locks import KeyedLock
from app.core.logging import get_logger
from app.core.security import issue_session_token
from app.core.utils import clamp, new_id, round_to, seeded_rng, utcnow
from app.db.repositories import (
    EarthReportRepository,
    EventRepository,
    GameRepository,
    IdempotencyRepository,
    MissionResultRepository,
    ScienceSampleRepository,
    SimulationRunRepository,
)
from app.db.session import Database
from app.domain.enums import Activity, Difficulty, MissionStatus
from app.domain.state import EquipmentState, GameState
from app.missions.engine import MissionEngine
from app.missions.registry import GameContent
from app.systems.continuity import ContinuitySystem
from app.systems.events import EventRecorder
from app.systems.inventory_system import InventorySystem
from app.systems.oxygen_system import OxygenSystem
from app.systems.resource_manager import ResourceManager

logger = get_logger("nova.game")


@dataclass(slots=True)
class Transaction:
    """Everything a gameplay operation needs, scoped to one request."""

    session: AsyncSession
    state: GameState
    recorder: EventRecorder

    @property
    def games(self) -> GameRepository:
        return GameRepository(self.session)

    @property
    def events(self) -> EventRepository:
        return EventRepository(self.session)

    @property
    def results(self) -> MissionResultRepository:
        return MissionResultRepository(self.session)

    @property
    def samples(self) -> ScienceSampleRepository:
        return ScienceSampleRepository(self.session)

    @property
    def runs(self) -> SimulationRunRepository:
        return SimulationRunRepository(self.session)

    @property
    def earth(self) -> EarthReportRepository:
        return EarthReportRepository(self.session)

    @property
    def idempotency(self) -> IdempotencyRepository:
        return IdempotencyRepository(self.session)


def create_initial_state(
    content: GameContent,
    *,
    player_name: str,
    difficulty: Difficulty,
    seed: int | None = None,
    game_id: str | None = None,
) -> GameState:
    catalog = content.catalog
    resolved_seed = seed if seed is not None else seeded_rng(player_name, utcnow().isoformat()).randint(1, 2**31)
    state = GameState(
        game_id=game_id or new_id("nova"),
        player_name=player_name,
        difficulty=difficulty,
        seed=resolved_seed,
        resources=catalog.starting_resources(),
        resource_caps=catalog.resource_caps(),
        inventory={},
        equipment={},
    )
    state.world = {
        "leak_active": False,
        "fire_active": False,
        "comms_online": False,
        "base_site": None,
        "hazards": {},
        "_oxygen_fraction": 0.0,
    }
    return state


class GameService:
    def __init__(self, database: Database, content: GameContent, settings: Settings) -> None:
        self.db = database
        self.content = content
        self.settings = settings
        self.engine = MissionEngine(content)
        self.locks = KeyedLock()

    # ------------------------------------------------------------------
    # Transaction plumbing
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def transaction(self, game_id: str) -> AsyncIterator[Transaction]:
        async with self.locks.acquire(game_id), self.db.session() as session:
            repository = GameRepository(session)
            state = await repository.load(game_id)
            if state is None:
                raise SessionNotFoundError(
                    f"Game session not found: {game_id}", game_id=game_id
                )
            expected_version = state.version
            recorder = EventRecorder(state)
            transaction = Transaction(session=session, state=state, recorder=recorder)

            yield transaction

            saved = await repository.save(state, expected_version)
            if not saved:
                raise StateConflictError(
                    "The game state was modified by another request. Retry.",
                    game_id=game_id,
                    expected_version=expected_version,
                )
            events = recorder.drain()
            await transaction.events.append(game_id, events)
            if state.event_seq % 200 == 0 and events:
                await transaction.events.trim(game_id, self.settings.max_events_per_session)

    async def read_state(self, game_id: str) -> GameState:
        async with self.db.session() as session:
            state = await GameRepository(session).load(game_id)
        if state is None:
            raise SessionNotFoundError(f"Game session not found: {game_id}", game_id=game_id)
        return state

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    async def create_game(
        self,
        *,
        player_name: str,
        difficulty: Difficulty | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        async with self.db.session() as session:
            repository = GameRepository(session)
            if await repository.count() >= self.settings.max_sessions:
                raise SessionLimitError(
                    f"Server reached the {self.settings.max_sessions} session limit.",
                    limit=self.settings.max_sessions,
                )

            state = create_initial_state(
                self.content,
                player_name=player_name,
                difficulty=difficulty or Difficulty(self.settings.default_difficulty),
                seed=seed,
            )
            self.engine.bootstrap(state)
            recorder = EventRecorder(state)
            recorder.emit(
                "game.created",
                {
                    "player_name": state.player_name,
                    "difficulty": state.difficulty.value,
                    "seed": state.seed,
                    "missions_available": [
                        m["id"] for m in self.engine.catalogue_view(state) if m["status"] != "locked"
                    ],
                },
            )
            await repository.create(state)
            await EventRepository(session).append(state.game_id, recorder.drain())

        logger.info("game.created", extra={"game_id": state.game_id})
        return {
            "game_id": state.game_id,
            "session_token": issue_session_token(
                state.game_id, self.settings.secret_key, self.settings.token_ttl_hours
            ),
            "state": self.snapshot_payload(state),
        }

    async def list_games(
        self, *, player_name: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            return await GameRepository(session).list(
                player_name=player_name, limit=limit, offset=offset
            )

    async def delete_game(self, game_id: str) -> bool:
        async with self.locks.acquire(game_id), self.db.session() as session:
            deleted = await GameRepository(session).delete(game_id)
        if not deleted:
            raise SessionNotFoundError(f"Game session not found: {game_id}", game_id=game_id)
        return True

    async def resume(self, game_id: str) -> dict[str, Any]:
        state = await self.read_state(game_id)
        return {
            "game_id": state.game_id,
            "session_token": issue_session_token(
                state.game_id, self.settings.secret_key, self.settings.token_ttl_hours
            ),
            "state": self.snapshot_payload(state),
        }

    async def purge_expired(self) -> int:
        cutoff = utcnow() - timedelta(hours=self.settings.session_ttl_hours)
        async with self.db.session() as session:
            removed = await GameRepository(session).purge_older_than(cutoff)
            await IdempotencyRepository(session).purge_older_than(cutoff)
        return removed

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------
    def snapshot_payload(self, state: GameState) -> dict[str, Any]:
        inventory = InventorySystem(state, self.content.catalog)
        resources = ResourceManager(state, self.content.catalog)
        return {
            **state.public_snapshot(),
            "missions_catalogue": self.engine.catalogue_view(state),
            "hud": {
                "critical_resources": resources.critical(),
                "degraded_tools": inventory.degraded_tools(),
                "oxygen_alarm": state.suit.oxygen_alarm,
                "readiness": state.continuity.readiness,
                "readiness_grade": state.continuity.readiness_grade,
                "active_mission": next(
                    (
                        mission_id
                        for mission_id, progress in state.missions.items()
                        if progress.status == MissionStatus.active
                    ),
                    None,
                ),
            },
        }

    async def snapshot(self, game_id: str, *, event_limit: int = 25) -> dict[str, Any]:
        async with self.db.session() as session:
            state = await GameRepository(session).load(game_id)
            if state is None:
                raise SessionNotFoundError(f"Game session not found: {game_id}", game_id=game_id)
            events = await EventRepository(session).list(
                game_id, after_seq=max(0, state.event_seq - event_limit), limit=event_limit
            )
        payload = self.snapshot_payload(state)
        payload["recent_events"] = events
        return payload

    async def events(
        self, game_id: str, *, after_seq: int = 0, limit: int = 100, types: list[str] | None = None
    ) -> dict[str, Any]:
        if not await self._exists(game_id):
            raise SessionNotFoundError(f"Game session not found: {game_id}", game_id=game_id)
        async with self.db.session() as session:
            events = await EventRepository(session).list(
                game_id, after_seq=after_seq, limit=limit, types=types
            )
        return {
            "game_id": game_id,
            "after_seq": after_seq,
            "events": events,
            "next_seq": events[-1]["seq"] if events else after_seq,
            "has_more": len(events) == limit,
        }

    async def _exists(self, game_id: str) -> bool:
        async with self.db.session() as session:
            return await GameRepository(session).exists(game_id)

    # ------------------------------------------------------------------
    # Gameplay
    # ------------------------------------------------------------------
    async def tick(self, game_id: str, *, dt_seconds: float, activity: str = "idle") -> dict[str, Any]:
        dt = clamp(float(dt_seconds), 0.0, self.settings.max_tick_seconds)
        if activity not in {a.value for a in Activity}:
            raise InvalidInputError(f"Unknown activity '{activity}'.")

        async with self.transaction(game_id) as tx:
            state = tx.state
            oxygen = OxygenSystem(state, self.content.catalog)
            telemetry = oxygen.tick(dt, activity)
            state.stats.playtime_seconds = round_to(state.stats.playtime_seconds + dt, 2)
            state.last_tick_at = utcnow()

            active = [
                mission_id
                for mission_id, progress in state.missions.items()
                if progress.status == MissionStatus.active
            ]
            for mission_id in active:
                progress = state.mission(mission_id)
                progress.elapsed_seconds = round_to(progress.elapsed_seconds + dt, 2)

            if telemetry["alarm"] == "critical":
                tx.recorder.emit("oxygen.critical", telemetry)
            elif telemetry["alarm"] == "warning" and state.world.get("_oxygen_warned") is not True:
                state.world["_oxygen_warned"] = True
                tx.recorder.emit("oxygen.warning", telemetry)
            elif telemetry["alarm"] is None:
                state.world["_oxygen_warned"] = False

            return {"game_id": game_id, "telemetry": telemetry, "resources": dict(state.resources)}

    async def start_mission(self, game_id: str, mission_id: str) -> dict[str, Any]:
        async with self.transaction(game_id) as tx:
            view = self.engine.start(tx.state, mission_id, tx.recorder)
            self._ensure_simulation(tx, mission_id)
            view["simulation_state"] = tx.state.simulations.get(mission_id)
            return {"mission": view, "resources": dict(tx.state.resources)}

    def _ensure_simulation(self, tx: Transaction, mission_id: str) -> None:
        """Prepare (but do not run) the simulation attached to a mission."""
        from app.domain.enums import SimulationStatus
        from app.simulations import factory

        definition = self.content.missions.get(mission_id)
        if definition is None or definition.simulation is None:
            return
        if mission_id in tx.state.simulations:
            return
        simulation = factory.create(
            definition.simulation,
            mission_id,
            tx.state.seed,
            {"difficulty": tx.state.difficulty.value},
        )
        simulation.status = SimulationStatus.ready
        tx.state.simulations[mission_id] = factory.dump(simulation)
        tx.recorder.emit(
            "simulation.ready",
            {"mission_id": mission_id, "kind": definition.simulation.value},
            mission_id=mission_id,
        )

    async def execute_action(
        self,
        game_id: str,
        mission_id: str,
        action: str,
        *,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        async with self.transaction(game_id) as tx:
            key = f"{game_id}:{idempotency_key}" if idempotency_key else None
            if key:
                cached = await tx.idempotency.get(key)
                if cached is not None:
                    tx.recorder.events.clear()
                    return {**cached, "replayed": True}

            result = self.engine.execute(tx.state, mission_id, action, tx.recorder, payload)
            self._ensure_simulation(tx, mission_id)
            if result.get("report"):
                await tx.results.upsert(game_id, result["report"])
            if key:
                await tx.idempotency.store(key, game_id, result)
            return result

    async def mission_view(self, game_id: str, mission_id: str) -> dict[str, Any]:
        state = await self.read_state(game_id)
        return self.engine.mission_view(state, mission_id)

    async def mission_catalogue(self, game_id: str) -> list[dict[str, Any]]:
        state = await self.read_state(game_id)
        return self.engine.catalogue_view(state)

    async def interaction_map(self, game_id: str, mission_id: str) -> dict[str, Any]:
        state = await self.read_state(game_id)
        return self.engine.interaction_map(state, mission_id)

    async def mission_report(self, game_id: str, mission_id: str) -> dict[str, Any]:
        async with self.db.session() as session:
            if not await GameRepository(session).exists(game_id):
                raise SessionNotFoundError(f"Game session not found: {game_id}", game_id=game_id)
            report = await MissionResultRepository(session).get(game_id, mission_id)
        if report is None:
            from app.core.errors import ReportNotFoundError

            raise ReportNotFoundError(
                f"No report yet for mission '{mission_id}'. Complete it first.",
                game_id=game_id,
                mission_id=mission_id,
            )
        return report

    async def all_reports(self, game_id: str) -> dict[str, Any]:
        state = await self.read_state(game_id)
        async with self.db.session() as session:
            reports = await MissionResultRepository(session).list(game_id)
        completed = [r for r in reports if r.get("status") == "completed"]
        average = round_to(sum(r["score_pct"] for r in completed) / len(completed), 2) if completed else 0.0
        return {
            "game_id": game_id,
            "player_name": state.player_name,
            "missions_completed": len(completed),
            "missions_total": len(self.content.missions.missions),
            "average_score_pct": average,
            "readiness": state.continuity.readiness,
            "readiness_grade": state.continuity.readiness_grade,
            "total_playtime_seconds": state.stats.playtime_seconds,
            "reports": reports,
            "stats": state.stats.model_dump(mode="json"),
        }

    async def continuity(self, game_id: str) -> dict[str, Any]:
        state = await self.read_state(game_id)
        return ContinuitySystem(state, self.content.catalog).summary()

    # ------------------------------------------------------------------
    # Inventory / suit
    # ------------------------------------------------------------------
    async def use_item(
        self, game_id: str, item_id: str, *, target: str | None = None
    ) -> dict[str, Any]:
        async with self.transaction(game_id) as tx:
            inventory = InventorySystem(tx.state, self.content.catalog)
            outcome = inventory.use(item_id, target)
            tx.recorder.emit("item.used", outcome)
            return {
                **outcome,
                "inventory": dict(tx.state.inventory),
                "resources": dict(tx.state.resources),
                "equipment": {k: v.model_dump() for k, v in tx.state.equipment.items()},
            }

    async def maintain_equipment(self, game_id: str, item_id: str) -> dict[str, Any]:
        async with self.transaction(game_id) as tx:
            inventory = InventorySystem(tx.state, self.content.catalog)
            definition = self.content.catalog.item(item_id)
            if definition is None or not definition.durable:
                raise InvalidInputError(f"'{item_id}' is not a serviceable tool.")
            if not inventory.has(item_id):
                raise InvalidInputError(f"You do not carry '{item_id}'.")

            if inventory.has("maintenance_kit"):
                inventory.remove("maintenance_kit")
                method = "maintenance_kit"
                amount = 45
            else:
                resources = ResourceManager(tx.state, self.content.catalog)
                resources.apply({"power": -5})
                method = "service_station"
                amount = 25

            condition = inventory.restore(item_id, amount)
            tx.state.equipment.setdefault(item_id, EquipmentState())
            payload = {"item": item_id, "condition": condition, "method": method}
            tx.recorder.emit("equipment.maintained", payload)
            return {
                **payload,
                "inventory": dict(tx.state.inventory),
                "resources": dict(tx.state.resources),
            }

    async def set_eva(self, game_id: str, active: bool) -> dict[str, Any]:
        async with self.transaction(game_id) as tx:
            oxygen = OxygenSystem(tx.state, self.content.catalog)
            payload = oxygen.set_eva(active)
            tx.recorder.emit("suit.eva", payload)
            return payload

    async def leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            return await GameRepository(session).leaderboard(limit)

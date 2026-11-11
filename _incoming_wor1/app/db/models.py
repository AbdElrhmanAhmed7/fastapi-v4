"""Persistence model.

The mutable gameplay document lives in ``game_sessions.state`` (JSON). Anything
we want to *query* across games – results, events, samples, simulation runs,
Earth reports – gets its own normalised table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.db.base import Base


class GameSessionRow(Base):
    __tablename__ = "game_sessions"

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_name: Mapped[str] = mapped_column(String(80), index=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="standard")
    environment: Mapped[str] = mapped_column(String(32), default="training_facility")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    readiness: Mapped[float] = mapped_column(Float, default=50.0)
    missions_completed: Mapped[int] = mapped_column(Integer, default=0)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    events: Mapped[list[EventRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    results: Mapped[list[MissionResultRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class EventRow(Base):
    __tablename__ = "event_log"
    __table_args__ = (
        UniqueConstraint("game_id", "seq", name="uq_event_game_seq"),
        Index("ix_event_game_seq", "game_id", "seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("game_sessions.game_id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64), index=True)
    mission_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[GameSessionRow] = relationship(back_populates="events")


class MissionResultRow(Base):
    __tablename__ = "mission_results"
    __table_args__ = (UniqueConstraint("game_id", "mission_id", name="uq_result_game_mission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("game_sessions.game_id", ondelete="CASCADE"), index=True
    )
    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    score_pct: Mapped[float] = mapped_column(Float, default=0.0)
    grade: Mapped[str] = mapped_column(String(4), default="D")
    mistakes: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[GameSessionRow] = relationship(back_populates="results")


class ScienceSampleRow(Base):
    __tablename__ = "science_samples"

    sample_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("game_sessions.game_id", ondelete="CASCADE"), index=True
    )
    mission_id: Mapped[str] = mapped_column(String(64), default="martian_guide")
    site: Mapped[str] = mapped_column(String(80))
    rock_type: Mapped[str] = mapped_column(String(80), default="unknown")
    composition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    analysed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class SimulationRunRow(Base):
    __tablename__ = "simulation_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("game_sessions.game_id", ondelete="CASCADE"), index=True
    )
    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    score_pct: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EarthReportRow(Base):
    __tablename__ = "earth_reports"

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("game_sessions.game_id", ondelete="CASCADE"), index=True
    )
    region_id: Mapped[str] = mapped_column(String(64), index=True)
    layer_id: Mapped[str] = mapped_column(String(64), index=True)
    period_before: Mapped[str] = mapped_column(String(32))
    period_after: Mapped[str] = mapped_column(String(32))
    provenance: Mapped[str] = mapped_column(String(16), default="bundled")
    verdict: Mapped[str] = mapped_column(String(16), default="stable")
    mean_delta: Mapped[float] = mapped_column(Float, default=0.0)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRow(Base):
    """Replay protection for action requests retried by the client."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

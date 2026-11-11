"""Async engine / session management (SQLite by default, any async URL works)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger("nova.db")


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    # -- lifecycle ----------------------------------------------------------
    async def connect(self) -> None:
        if self._engine is not None:
            return

        url = self.settings.database_url
        if url.startswith("sqlite"):
            self._ensure_sqlite_path(url)

        kwargs: dict = {"echo": self.settings.db_echo, "future": True}
        if not url.startswith("sqlite"):
            kwargs.update(pool_size=self.settings.db_pool_size, pool_pre_ping=True)

        engine = create_async_engine(url, **kwargs)

        if url.startswith("sqlite"):
            @event.listens_for(engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        self._engine = engine
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        if self.settings.auto_create_tables:
            await self.create_all()
        logger.info("database.connected", extra={"url": self._safe_url(url)})

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            logger.info("database.disconnected")

    async def create_all(self) -> None:
        assert self._engine is not None
        # Import models so they are registered on the metadata.
        from app.db import models  # noqa: F401

        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def healthcheck(self) -> bool:
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:  # pragma: no cover - defensive
            logger.exception("database.healthcheck_failed")
            return False

    # -- sessions -----------------------------------------------------------
    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("Database.connect() was not called")
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database.connect() was not called")
        return self._engine

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _ensure_sqlite_path(url: str) -> None:
        tail = url.split("///")[-1]
        if not tail or tail == ":memory:":
            return
        Path(tail).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_url(url: str) -> str:
        if "@" in url:
            scheme, _, rest = url.partition("://")
            return f"{scheme}://***@{rest.split('@')[-1]}"
        return url

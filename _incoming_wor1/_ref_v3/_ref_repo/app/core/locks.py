"""Per-game async locks.

SQLite has no row level locking, and a browser can easily fire two overlapping
requests (e.g. a HUD tick and an interaction). Serialising mutations per game
id guarantees that read-modify-write cycles never interleave.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class KeyedLock:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiters: dict[str, int] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            async with self._guard:
                self._waiters[key] -= 1
                if self._waiters[key] <= 0:
                    self._waiters.pop(key, None)
                    self._locks.pop(key, None)

    @property
    def active(self) -> int:
        return len(self._locks)

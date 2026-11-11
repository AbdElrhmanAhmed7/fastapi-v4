"""Async-safe in-process TTL cache with stale-on-error support.

Used by the NASA integration so the game never hammers api.nasa.gov (DEMO_KEY
allows only a handful of requests per hour) and keeps working offline.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float
    stored_at: float


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float = 900.0, max_entries: int = 256) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, _Entry[T]] = {}
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str, *, allow_stale: bool = False) -> T | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at < time.monotonic() and not allow_stale:
                self.misses += 1
                return None
            self.hits += 1
            return entry.value

    async def set(self, key: str, value: T, ttl_seconds: float | None = None) -> None:
        async with self._lock:
            if len(self._entries) >= self.max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k].stored_at)
                self._entries.pop(oldest, None)
            now = time.monotonic()
            self._entries[key] = _Entry(
                value=value,
                expires_at=now + (self.ttl if ttl_seconds is None else ttl_seconds),
                stored_at=now,
            )

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, Any]:
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}

"""Sliding-window rate limiter (in-process, no external dependency).

Good enough for a single-node game server. Swap the storage for Redis if the
backend is ever scaled horizontally – the interface stays the same.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class RateLimitVerdict:
    allowed: bool
    remaining: int
    retry_after: float
    limit: int


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, *, cost: int = 1) -> RateLimitVerdict:
        now = time.monotonic()
        bucket = self._hits.setdefault(key, deque())
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) + cost > self.limit:
            retry_after = max(0.0, self.window - (now - bucket[0])) if bucket else self.window
            return RateLimitVerdict(False, 0, round(retry_after, 2), self.limit)

        for _ in range(cost):
            bucket.append(now)

        if not bucket:  # pragma: no cover - defensive
            self._hits.pop(key, None)
        return RateLimitVerdict(True, self.limit - len(bucket), 0.0, self.limit)

    def prune(self) -> None:
        """Drop idle buckets so memory does not grow without bound."""
        cutoff = time.monotonic() - self.window
        for key in list(self._hits):
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                self._hits.pop(key, None)

    def reset(self) -> None:
        self._hits.clear()

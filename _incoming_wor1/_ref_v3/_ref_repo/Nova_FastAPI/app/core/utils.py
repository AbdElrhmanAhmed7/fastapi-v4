"""Small, dependency-free helpers shared across the codebase."""

from __future__ import annotations

import hashlib
import random
import re
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

T = TypeVar("T", int, float)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (never use naive datetimes in storage)."""
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    """Short, URL-safe, collision-resistant identifier."""
    raw = uuid4().hex[:20]
    return f"{prefix}_{raw}" if prefix else raw


def clamp(value: T, low: T, high: T) -> T:
    return max(low, min(high, value))


def round_to(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def seeded_rng(*parts: Any) -> random.Random:
    """Deterministic RNG derived from arbitrary parts (seed, mission, run...)."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def deep_get(data: Any, path: str, default: Any = None) -> Any:
    """Read ``a.b.0.c`` style paths from nested dicts/lists without raising."""
    current = data
    for chunk in path.split("."):
        if isinstance(current, dict):
            if chunk not in current:
                return default
            current = current[chunk]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(chunk)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


def pct(part: float, whole: float) -> float:
    return round_to(100.0 * part / whole) if whole else 0.0


def merge_dicts(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge that never mutates its inputs."""
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result

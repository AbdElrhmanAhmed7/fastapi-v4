"""In-request event recorder.

Every meaningful gameplay change emits an event. Events are persisted with a
monotonic per-game sequence number so the frontend can poll
``GET /games/{id}/events?after_seq=N`` (or subscribe to the SSE stream) and
drive animations/HUD updates without re-reading the whole state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.utils import utcnow
from app.domain.state import GameState


class EventRecorder:
    def __init__(self, state: GameState) -> None:
        self.state = state
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        mission_id: str | None = None,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        self.state.event_seq += 1
        event = {
            "seq": self.state.event_seq,
            "type": event_type,
            "mission_id": mission_id,
            "payload": payload or {},
            "created_at": (created_at or utcnow()).isoformat(),
        }
        self.events.append(event)
        return event

    def extend(self, events: list[dict[str, Any]]) -> None:
        self.events.extend(events)

    def drain(self) -> list[dict[str, Any]]:
        drained = list(self.events)
        self.events.clear()
        return drained

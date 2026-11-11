"""Live event stream over WebSocket.

The HUD, mission panel and cinematic triggers can subscribe instead of polling
``GET /events``. The stream is a *read model*: it tails the persisted event log,
so a reconnect with ``after_seq`` never loses an event.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.errors import NovaError
from app.core.logging import get_logger
from app.core.security import constant_time_equals, verify_session_token
from app.services.game_service import GameService

logger = get_logger("nova.api.stream")
router = APIRouter(tags=["Stream"])

POLL_SECONDS = 0.35
HEARTBEAT_SECONDS = 15.0
CLOSE_AUTH_FAILED = 4401
CLOSE_NOT_FOUND = 4404


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    if websocket.client_state == WebSocketState.CONNECTED:
        await websocket.send_json(payload)


@router.websocket("/games/{game_id}/stream")
async def stream(
    websocket: WebSocket,
    game_id: str,
    after_seq: int = Query(default=0, ge=0),
    token: str | None = Query(default=None, description="X-Session-Token equivalent when auth is on."),
    api_key: str | None = Query(default=None),
) -> None:
    games: GameService = websocket.app.state.games
    settings = games.settings
    cursor = after_seq

    if settings.auth_enabled:
        trusted = bool(settings.api_key and api_key and constant_time_equals(api_key, settings.api_key))
        if not trusted:
            try:
                verify_session_token(token or "", settings.secret_key, game_id)
            except NovaError:
                await websocket.close(code=CLOSE_AUTH_FAILED, reason="Invalid session token")
                return

    try:
        state = await games.read_state(game_id)
    except NovaError:
        await websocket.close(code=CLOSE_NOT_FOUND, reason="Game session not found")
        return

    await websocket.accept()
    await _send(
        websocket,
        {
            "type": "stream.ready",
            "game_id": game_id,
            "after_seq": cursor,
            "event_seq": state.event_seq,
            "player_name": state.player_name,
        },
    )
    logger.info("stream.connected", extra={"game_id": game_id})

    heartbeat = 0.0
    try:
        while True:
            page = await games.events(game_id, after_seq=cursor, limit=200)
            events = page["events"]
            if events:
                cursor = page["next_seq"]
                await _send(websocket, {"type": "events", "events": events, "next_seq": cursor})

            # Drain any client message without blocking the tail loop.
            with contextlib.suppress(asyncio.TimeoutError):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=POLL_SECONDS)
                if message.strip().lower() == "ping":
                    await _send(websocket, {"type": "pong", "cursor": cursor})

            heartbeat += POLL_SECONDS
            if heartbeat >= HEARTBEAT_SECONDS:
                heartbeat = 0.0
                await _send(websocket, {"type": "heartbeat", "cursor": cursor})
    except WebSocketDisconnect:
        logger.info("stream.disconnected", extra={"game_id": game_id})
    except Exception:  # pragma: no cover - defensive, never leak a stack to the client
        logger.exception("stream.failed", extra={"game_id": game_id})
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="Stream failure")

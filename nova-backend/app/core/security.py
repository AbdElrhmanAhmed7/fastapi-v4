"""Lightweight authentication.

Two independent, optional mechanisms:

* **Session token** – an HMAC-signed, expiring token handed out when a game is
  created (``POST /api/v1/games``). Mutating endpoints require it in the
  ``X-Session-Token`` header when ``NOVA_AUTH_ENABLED=true``. This stops one
  player from mutating another player's save without introducing a full
  account system.
* **API key** – a static ``X-API-Key`` for trusted server-to-server callers.

No third-party JWT library is needed: the payload is tiny and we only ever
verify tokens we issued ourselves.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.errors import AuthenticationError


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload: str, secret: str) -> str:
    return _b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def issue_session_token(game_id: str, secret: str, ttl_hours: int = 72) -> str:
    body = {"gid": game_id, "exp": int(time.time()) + ttl_hours * 3600, "v": 1}
    payload = _b64encode(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload, secret)}"


def verify_session_token(token: str, secret: str, expected_game_id: str) -> dict[str, Any]:
    if not token or token.count(".") != 1:
        raise AuthenticationError("Malformed session token.")
    payload, signature = token.split(".", 1)

    if not hmac.compare_digest(signature, _sign(payload, secret)):
        raise AuthenticationError("Invalid session token signature.")

    try:
        body = json.loads(_b64decode(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Corrupted session token.") from exc

    if int(body.get("exp", 0)) < time.time():
        raise AuthenticationError("Session token expired. Create or resume a game.")
    if body.get("gid") != expected_game_id:
        raise AuthenticationError("Session token does not match this game.")
    return body


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())

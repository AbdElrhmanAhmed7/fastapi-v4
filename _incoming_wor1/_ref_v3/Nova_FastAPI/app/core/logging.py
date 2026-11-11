"""Structured logging with a per-request correlation id.

Logs are JSON in production (easy to ship to any log stack) and human readable
in dev. ``request_id`` is propagated through a contextvar so every log line
emitted while handling a request can be correlated with the response header.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

from app.core.utils import iso, utcnow

request_id_var: ContextVar[str] = ContextVar("nova_request_id", default="-")
game_id_var: ContextVar[str] = ContextVar("nova_game_id", default="-")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": iso(utcnow()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        game_id = game_id_var.get()
        if game_id != "-":
            payload["game_id"] = game_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        return super().format(record)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

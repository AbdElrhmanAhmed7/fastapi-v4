"""HTTP middleware: correlation ids, access logs, rate limiting, body guards."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import Settings
from app.core.logging import game_id_var, get_logger, request_id_var
from app.core.ratelimit import SlidingWindowRateLimiter
from app.core.utils import new_id

logger = get_logger("nova.http")

EXEMPT_PATHS = {"/health", "/api/v1/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, time the request and emit one structured log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or new_id("req")
        token = request_id_var.set(rid)
        game_id = request.path_params.get("game_id", "-") if request.path_params else "-"
        gid_token = game_id_var.set(str(game_id))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request.failed",
                extra={"path": request.url.path, "method": request.method, "duration_ms": elapsed_ms},
            )
            raise
        finally:
            request_id_var.reset(token)
            game_id_var.reset(gid_token)

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Process-Time-ms"] = str(elapsed_ms)
        response.headers.setdefault("X-NOVA-Backend", "nova-gameplay")

        if request.url.path not in EXEMPT_PATHS:
            logger.info(
                "request.completed",
                extra={
                    "path": request.url.path,
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": elapsed_ms,
                },
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding window limiter (mutations cost the same as reads)."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.limiter = SlidingWindowRateLimiter(
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self.settings.rate_limit_enabled or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        key = request.headers.get("x-session-token") or client
        verdict = self.limiter.check(key)
        if not verdict.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Slow down.",
                        "details": {"retry_after_seconds": verdict.retry_after},
                    }
                },
                headers={
                    "Retry-After": str(int(verdict.retry_after) + 1),
                    "X-RateLimit-Limit": str(verdict.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(verdict.limit)
        response.headers["X-RateLimit-Remaining"] = str(verdict.remaining)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized payloads before they reach the JSON parser."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "payload_too_large",
                        "message": f"Request body exceeds {self.max_bytes} bytes.",
                    }
                },
            )
        return await call_next(request)

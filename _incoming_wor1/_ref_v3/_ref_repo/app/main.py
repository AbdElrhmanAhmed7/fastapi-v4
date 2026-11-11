import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import games, inventory, missions, resources
from app.services.exceptions import GameNotFoundError
from app.services.game_service import GameService


def get_allowed_origins() -> list[str]:
    """Read comma-separated frontend origins from FRONTEND_ORIGINS."""
    raw_origins = os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


def create_app(game_service: GameService | None = None) -> FastAPI:
    app = FastAPI(
        title="NOVA Gameplay Backend",
        description=(
            "Prototype API for NOVA game sessions, player resources, inventory, "
            "and the Earth Analog water-leak mission."
        ),
        version="0.1.0",
    )

    # One service instance is shared by all requests handled by this app.
    app.state.game_service = game_service if game_service is not None else GameService()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(GameNotFoundError)
    async def game_not_found_handler(
        request: Request,
        exc: GameNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    @app.get("/health", tags=["Health"], summary="Check API health")
    def health_check() -> dict[str, str]:
        return {"status": "ok", "service": "NOVA Gameplay Backend"}

    app.include_router(games.router)
    app.include_router(resources.router)
    app.include_router(inventory.router)
    app.include_router(missions.router)

    return app


app = create_app()

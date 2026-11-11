"""Application settings (12-factor style, env driven).

All variables use the ``NOVA_`` prefix and can also live in a ``.env`` file.
Import :func:`get_settings` anywhere; it is cached so the environment is read
once per process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOVA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "NOVA – Beyond the Mission API"
    environment: Literal["dev", "test", "prod"] = "dev"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Persistence -------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/nova.db"
    db_echo: bool = False
    auto_create_tables: bool = True
    db_pool_size: int = 5

    # --- HTTP --------------------------------------------------------------
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_origin_regex: str | None = r"https://.*\.(e2b\.app|vercel\.app|netlify\.app)"
    max_request_bytes: int = 256_000

    # --- Security ----------------------------------------------------------
    auth_enabled: bool = False
    secret_key: str = "nova-dev-secret-change-me"
    token_ttl_hours: int = 72
    api_key: str = ""

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 600
    rate_limit_window_seconds: int = 60

    # --- Gameplay ----------------------------------------------------------
    default_difficulty: Literal["cadet", "standard", "veteran"] = "standard"
    max_tick_seconds: float = Field(default=5.0, gt=0, le=60)
    session_ttl_hours: int = 168
    max_events_per_session: int = 2000
    max_sessions: int = 5000
    content_dir: Path = PROJECT_ROOT / "content"

    # --- NASA open data ----------------------------------------------------
    nasa_api_key: str = "DEMO_KEY"
    nasa_enabled: bool = True
    nasa_base_url: str = "https://api.nasa.gov"
    nasa_timeout_seconds: float = 8.0
    nasa_cache_ttl_seconds: int = 900
    nasa_max_retries: int = 2

    @field_validator("log_level")
    @classmethod
    def _normalise_level(cls, value: str) -> str:
        return value.upper()

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that patch the environment between app builds."""
    get_settings.cache_clear()

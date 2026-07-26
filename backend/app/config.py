"""Application configuration, sourced from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:5173,http://localhost:8080"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Anthropic ---
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    # Reasoning depth, and therefore per-turn latency. `low` keeps a live demo
    # moving; raise it if agents feel shallow.
    anthropic_effort: Effort = "low"
    anthropic_max_tokens: int = 2048
    #: Force the scripted/random agents even when a key is present. Useful for
    #: rehearsing the demo without spending tokens.
    use_mock_agents: bool = False

    # --- CORS ---
    # Comma-separated rather than a JSON list: pydantic-settings parses complex
    # types as JSON, which makes a plain `A,B` env var a confusing hard error.
    allowed_origins: str = DEFAULT_ORIGINS
    cors_allow_all: bool = False

    # --- Negotiation ---
    rounds: int = 6
    turn_delay_seconds: float = 3.0
    pool_resource: str = "budget"
    pool_total: float = 100.0

    # --- Speech-to-text ---
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    whisper_preload: bool = False

    @property
    def cors_origins(self) -> list[str]:
        """`ALLOWED_ORIGINS` split into a list, blanks dropped."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

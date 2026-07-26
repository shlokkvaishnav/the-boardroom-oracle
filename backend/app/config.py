"""Application configuration, sourced from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:5173,http://localhost:8080"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Gemini ---
    gemini_api_key: str | None = None
    #: Free-tier eligibility moves; verify the current list in AI Studio
    #: (https://aistudio.google.com/rate-limit) before a demo.
    gemini_model: str = "gemini-3.6-flash"
    gemini_max_output_tokens: int = 2048

    #: Free-tier limits are per-minute and low (historically ~15 RPM on flash).
    #: Calls are serialized with at least this gap, so a round of three agents
    #: paces itself instead of bursting into a 429.
    llm_min_interval_seconds: float = 4.0
    #: Attempts per call when the API returns 429/5xx, including the first.
    llm_max_attempts: int = 3
    #: First backoff wait; doubles each attempt (2s, 4s, ...).
    llm_backoff_base_seconds: float = 2.0
    llm_timeout_seconds: float = 60.0

    #: Force the scripted/random agents even when a key is present. Useful for
    #: rehearsing the demo without spending quota.
    use_mock_agents: bool = False

    # --- Sessions ---
    #: How many negotiations may run at once. The cap exists because provider
    #: quota is per API *key*, not per session — N concurrent games burn the
    #: same Gemini budget N times as fast. Raising it does not make anything
    #: faster: every agent call from every session queues behind the same
    #: global slot (see `llm_client.py`), so more sessions means slower rounds.
    max_concurrent_sessions: int = 5
    #: A session nobody has touched for this long is swept, so an abandoned
    #: browser tab doesn't hold an engine and its round loop forever.
    session_ttl_seconds: float = 600.0
    session_sweep_interval_seconds: float = 60.0

    # --- CORS ---
    # Comma-separated rather than a JSON list: pydantic-settings parses complex
    # types as JSON, which makes a plain `A,B` env var a confusing hard error.
    allowed_origins: str = DEFAULT_ORIGINS
    cors_allow_all: bool = False

    # --- Negotiation ---
    rounds: int = 6
    #: Doubles as demo pacing and as rate-limit headroom: with a call every
    #: ~4s minimum, a deliberate gap between turns keeps the whole round under
    #: the free tier's per-minute budget and reads as agents "thinking".
    turn_delay_seconds: float = 2.5
    pool_resource: str = "budget"
    pool_total: float = 100.0

    # --- Speech-to-text ---
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    whisper_preload: bool = False

    # --- Live web search (agent tool) ---
    #: Without a key the `web_search` tool is never offered to agents and a
    #: session with a `context_topic` still runs — just without live facts.
    tavily_api_key: str | None = None
    #: Results per search. Kept small on purpose: every hit is fed back into the
    #: follow-up prompt, and a long tool result crowds out the negotiation state.
    tavily_max_results: int = 3
    tavily_timeout_seconds: float = 10.0

    @property
    def cors_origins(self) -> list[str]:
        """`ALLOWED_ORIGINS` split into a list, blanks dropped."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    @property
    def has_tavily_key(self) -> bool:
        return bool(self.tavily_api_key and self.tavily_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

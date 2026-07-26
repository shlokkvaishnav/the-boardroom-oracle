"""Application configuration, sourced from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

#: `localhost` and `127.0.0.1` are *different origins* to a browser, and Vite
#: prints both, so opening the one that wasn't listed produced a 400 on the CORS
#: preflight with nothing in the UI to explain it. Both are listed for every
#: dev port to remove that trap.
#: 8081/8082 are Vite's fallback ports. It increments *silently* when 8080 is
#: taken — a second `bun run dev` is enough — and the only symptom is a 400 on
#: the CORS preflight with nothing in the UI to explain it.
DEFAULT_ORIGINS = ",".join(
    f"http://{host}:{port}"
    for port in (3000, 5173, 8080, 8081, 8082)
    for host in ("localhost", "127.0.0.1")
)


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
    #: Raised from 2048 after `claims` was added to the response schema: longer
    #: responses started hitting the cap and coming back as truncated JSON
    #: ("Unterminated string"), which the agent's retry ladder recovered from at
    #: the cost of a whole extra call per occurrence. Headroom is cheaper than
    #: a retry, because a retry is billed as a second request.
    gemini_max_output_tokens: int = 4096

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

    #: Model for observer work — the scribe's claim linking. Extraction, not
    #: reasoning, so it goes to something small and fast; blank falls back to
    #: `gemini_model`. It shares the same client queue either way, because the
    #: quota being protected is per API key, not per model.
    #: Verify a change here with a real call. `gemini-3.6-flash-lite` looks
    #: like it should work by analogy with `gemini-3.6-flash` and 404s on
    #: v1beta — and because a failed scribe pass degrades to "no links", a bad
    #: name here is invisible except as a feature that quietly never fires.
    scribe_model: str = "gemini-3.5-flash-lite"
    #: One extra call per round with claims in it, spent only from the budget's
    #: surplus. Turn it off to run a session at exactly the old call count.
    enable_scribe: bool = True
    #: One call at the very end, reporting each party's settled position and
    #: where the room landed. Uses the main model rather than the cheap one:
    #: this is the last thing anyone reads, and it is one call per session.
    #: Off means the closing falls back to each party's last remark.
    enable_synthesis: bool = True
    #: How long the closing waits for a scribe pass still in flight. Bounded on
    #: purpose: a session's ending must never be hostage to a hung call, so an
    #: overrunning pass is cancelled and its links lost.
    scribe_settle_timeout_seconds: float = 10.0

    #: Most provider calls one session may spend, across every feature that
    #: makes them. 0 means unlimited.
    #:
    #: The arithmetic, at the defaults: three agents over six rounds is 18 calls
    #: to merely finish, or 36 when a topic is set and each turn also spends a
    #: search probe. 60 therefore never binds on a normal session — it is a
    #: ceiling on runaway, not a throttle on ordinary play. What it buys is the
    #: guarantee in `engine/budget.py`: the calls needed to reach the closing
    #: are reserved first, and optional enrichment only ever spends the surplus.
    session_call_budget: int = 60

    #: Let whoever was just named or challenged answer next, instead of
    #: fixed seating order. Costs nothing — it is a rule, not a model. Off
    #: restores strict round-robin.
    enable_chair: bool = True

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
    #: "tiny" over "base": on CPU it transcribes a one-line command in a
    #: fraction of the time, and for short spoken offers the accuracy
    #: difference is not worth the wait.
    whisper_model: str = "tiny"
    whisper_compute_type: str = "int8"
    #: Load at boot by default. The first transcription otherwise pays the
    #: whole model load, which reads as "the mic is broken".
    whisper_preload: bool = True
    #: Pin the language to skip auto-detection, which costs a pass over the
    #: audio. Empty means detect.
    whisper_language: str = "en"

    # --- Groq (hosted Whisper) ---
    #: With a key, voice goes to Groq's hosted Whisper — far faster and more
    #: accurate than anything that runs on this CPU. Without one, the local
    #: model handles it, and the local model also catches any Groq failure.
    groq_api_key: str | None = None
    #: `-turbo` is ~2x the throughput of plain large-v3 for a small accuracy
    #: cost; either is far better than the local `tiny`.
    groq_whisper_model: str = "whisper-large-v3-turbo"
    groq_timeout_seconds: float = 20.0

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
    def has_groq_key(self) -> bool:
        return bool(self.groq_api_key and self.groq_api_key.strip())

    @property
    def has_tavily_key(self) -> bool:
        return bool(self.tavily_api_key and self.tavily_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

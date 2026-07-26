"""FastAPI application entrypoint and composition root.

Everything shared — settings, the session store, the WebSocket connection
manager, the transcriber, the LLM client — is constructed once here and
hung on `app.state`. Nothing else in the codebase reaches for a global.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes, ws
from app.config import Settings, get_settings
from app.session.store import InMemorySessionStore
from app.speech.transcribe import WhisperTranscriber
from app.ws.broadcast import ConnectionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger("boardroom")

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "starting boardroom-oracle backend (model=%s, rounds=%s, turn_delay=%.1fs, agents=%s)",
        settings.gemini_model,
        settings.rounds,
        settings.turn_delay_seconds,
        "mock" if (settings.use_mock_agents or not settings.has_gemini_key) else "gemini",
    )
    if not settings.has_gemini_key:
        logger.warning(
            "GEMINI_API_KEY is not set — sessions will run with mock agents. "
            "Set it in backend/.env for real negotiation."
        )

    # Optional: pay the model-load cost at boot rather than mid-demo.
    if settings.whisper_preload:
        try:
            await app.state.transcriber.load()
        except Exception:
            logger.exception("whisper preload failed; will retry on first request")

    sweeper = asyncio.create_task(_sweep_expired_sessions(app))
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass
        await app.state.store.clear()
        logger.info("shutting down")


async def _sweep_expired_sessions(app: FastAPI) -> None:
    """Evict idle sessions on a timer for the whole process lifetime.

    `put()` also sweeps, so capacity is never refused on account of stale
    sessions. This loop exists for the other case: a box that has gone quiet
    should let go of its engines rather than hold them until someone knocks.
    """
    settings: Settings = app.state.settings
    interval = max(1.0, settings.session_sweep_interval_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            await app.state.store.sweep()
        except Exception:
            # A failed sweep must never kill the loop that does the sweeping.
            logger.exception("session sweep failed; continuing")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Boardroom Oracle API",
        description="Live multi-agent AI negotiation demo.",
        version=VERSION,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.store = InMemorySessionStore(
        max_sessions=settings.max_concurrent_sessions,
        ttl_seconds=settings.session_ttl_seconds,
    )
    app.state.manager = ConnectionManager()
    app.state.transcriber = WhisperTranscriber(settings)
    app.state.llm_client = None
    app.state.offer_parser = None
    app.state.search_tool = None

    # The web_search tool is only built when there's a key for it. Without one,
    # a session with a `context_topic` still runs — the agents simply reason
    # from the premise without being able to look anything up.
    if settings.has_tavily_key:
        from app.search import WebSearchTool

        app.state.search_tool = WebSearchTool(settings)

    # Only construct the provider client when there's a key to use; the
    # mock-agent path must work with no credentials at all.
    if settings.has_gemini_key:
        from app.llm_client import build_llm_client
        from app.speech.parse_offer import VoiceOfferParser

        app.state.llm_client = build_llm_client(settings)
        app.state.offer_parser = VoiceOfferParser(app.state.llm_client, settings)

    # `allow_credentials` must be False alongside a wildcard origin — the CORS
    # spec forbids the combination and Starlette silently drops the header.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.cors_allow_all else settings.cors_origins,
        allow_credentials=not settings.cors_allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "boardroom-oracle-backend", "version": VERSION}

    app.include_router(routes.router)
    app.include_router(routes.speech_router)
    app.include_router(ws.router)

    return app


app = create_app()

"""FastAPI application entrypoint and composition root.

Everything shared — settings, the session store, the WebSocket connection
manager, the transcriber, the Anthropic client — is constructed once here and
hung on `app.state`. Nothing else in the codebase reaches for a global.
"""

from __future__ import annotations

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
        "starting boardroom-oracle backend (model=%s, effort=%s, rounds=%s, agents=%s)",
        settings.anthropic_model,
        settings.anthropic_effort,
        settings.rounds,
        "mock" if (settings.use_mock_agents or not settings.has_anthropic_key) else "claude",
    )
    if not settings.has_anthropic_key:
        logger.warning(
            "ANTHROPIC_API_KEY is not set — sessions will run with mock agents. "
            "Set it in backend/.env for real negotiation."
        )

    # Optional: pay the model-load cost at boot rather than mid-demo.
    if settings.whisper_preload:
        try:
            await app.state.transcriber.load()
        except Exception:
            logger.exception("whisper preload failed; will retry on first request")

    try:
        yield
    finally:
        await app.state.store.clear()
        logger.info("shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Boardroom Oracle API",
        description="Live multi-agent AI negotiation demo.",
        version=VERSION,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.store = InMemorySessionStore()
    app.state.manager = ConnectionManager()
    app.state.transcriber = WhisperTranscriber(settings)
    app.state.anthropic_client = None
    app.state.offer_parser = None

    # Only construct the SDK client when there's a key to use; the mock-agent
    # path must work with no credentials at all.
    if settings.has_anthropic_key:
        from app.agents.llm_agent import build_anthropic_client
        from app.speech.parse_offer import VoiceOfferParser

        app.state.anthropic_client = build_anthropic_client(settings)
        app.state.offer_parser = VoiceOfferParser(app.state.anthropic_client, settings)

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
    app.include_router(ws.router)

    return app


app = create_app()

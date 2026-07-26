"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger("boardroom")

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    logger.info(
        "starting boardroom-oracle backend (model=%s, effort=%s, rounds=%s, api_key=%s)",
        settings.anthropic_model,
        settings.anthropic_effort,
        settings.rounds,
        "set" if settings.has_anthropic_key else "MISSING",
    )
    yield
    logger.info("shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Boardroom Oracle API",
        description="Live multi-agent AI negotiation demo.",
        version=VERSION,
        lifespan=lifespan,
    )

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

    return app


app = create_app()

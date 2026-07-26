"""Shared test fixtures.

Nothing here (or anywhere in the suite) may require a `GEMINI_API_KEY`,
network access, or downloaded model weights: every LLM and Whisper call is
mocked at the seam.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings, independent of the ambient environment."""
    return Settings(
        gemini_api_key=None,
        # Belt and braces: even if a key leaks in from the environment, no test
        # may ever reach the real API.
        use_mock_agents=True,
        allowed_origins="http://localhost:3000",
        cors_allow_all=False,
        rounds=3,
        turn_delay_seconds=0.0,
        pool_resource="budget",
        pool_total=100.0,
        whisper_preload=False,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))

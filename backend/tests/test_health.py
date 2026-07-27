"""Milestone 1: the service boots and answers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "four-chairs-backend",
        "version": "0.1.0",
    }


def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unconfigured_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_allow_all_toggle_opens_wildcard() -> None:
    """The dev escape hatch works, and correctly drops credentialed CORS."""
    permissive = Settings(cors_allow_all=True, _env_file=None)  # type: ignore[call-arg]
    permissive_client = TestClient(create_app(permissive))

    response = permissive_client.get("/health", headers={"Origin": "https://anything.example"})

    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_allowed_origins_parses_comma_separated_list() -> None:
    parsed = Settings(
        allowed_origins="http://a.test, http://b.test ,,http://c.test",
        _env_file=None,  # type: ignore[call-arg]
    )

    assert parsed.cors_origins == ["http://a.test", "http://b.test", "http://c.test"]

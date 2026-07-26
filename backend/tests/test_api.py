"""Milestones 8-10: the REST surface.

Sessions run with mock agents and a zero turn delay, so every request here is
deterministic and needs no API key, no network and no model weights.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.personas import HUMAN_ID
from app.config import Settings
from app.main import create_app
from app.models.schemas import OfferSchema
from app.speech.transcribe import Transcription

MAXI = "maximizer"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "gemini_api_key": None,
        "use_mock_agents": True,
        "rounds": 2,
        # Long enough that the session is still in progress when the test makes
        # its next request. With a zero delay, mock agents finish the entire
        # game between two HTTP calls and the engine (correctly) starts
        # rejecting offers as belonging to a finished session. Nothing here
        # ever waits out the delay — teardown cancels the loop.
        "turn_delay_seconds": 30.0,
        "pool_resource": "budget",
        "pool_total": 100.0,
        "allowed_origins": "http://localhost:3000",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def api() -> TestClient:
    return TestClient(create_app(make_settings()))


# --------------------------------------------------------------------------- #
# Fakes for the voice path
# --------------------------------------------------------------------------- #


class FakeTranscriber:
    def __init__(
        self,
        text: str = "give the maximizer twelve budget",
        avg_logprob: float = -0.2,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.avg_logprob = avg_logprob
        self.error = error
        self.calls: list[tuple[int, str]] = []

    async def transcribe(self, audio: bytes, filename: str) -> Transcription:
        self.calls.append((len(audio), filename))
        if self.error is not None:
            raise self.error
        return Transcription(text=self.text, avg_logprob=self.avg_logprob)


class FakeParser:
    def __init__(self, result: OfferSchema | None) -> None:
        self.result = result
        self.transcripts: list[str] = []

    async def parse(self, transcript: str, **kwargs: Any) -> OfferSchema | None:
        self.transcripts.append(transcript)
        return self.result


def audio_upload(data: bytes = b"fake-audio-bytes") -> dict[str, Any]:
    return {"file": ("clip.webm", data, "audio/webm")}


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_start_returns_a_session_id(api: TestClient) -> None:
    response = api.post("/api/session/start")

    assert response.status_code == 200
    assert response.json().keys() == {"session_id"}
    assert response.json()["session_id"]


def test_state_is_404_before_a_session_exists(api: TestClient) -> None:
    response = api.get("/api/session/state")

    assert response.status_code == 404
    assert "start" in response.json()["detail"]


def test_state_returns_the_contracted_shape_after_start(api: TestClient) -> None:
    api.post("/api/session/start")

    body = api.get("/api/session/state").json()

    assert set(body) == {
        "round",
        "pool",
        "agents",
        "trust_graph",
        "offer_log",
        "agent_thoughts",
        "revealed_objectives",
    }
    assert body["pool"] == {"resource": "budget", "total": 100.0}
    assert {agent["id"] for agent in body["agents"]} == {
        "cooperator",
        "maximizer",
        "titfortat",
        HUMAN_ID,
    }


def test_starting_twice_replaces_the_previous_session(api: TestClient) -> None:
    first = api.post("/api/session/start").json()["session_id"]
    second = api.post("/api/session/start").json()["session_id"]

    assert first != second


def test_reset_tears_down_the_session(api: TestClient) -> None:
    api.post("/api/session/start")

    response = api.post("/api/session/reset")

    assert response.status_code == 200
    assert response.json()["status"] == "reset"
    assert api.get("/api/session/state").status_code == 404


def test_reset_without_a_session_is_not_an_error(api: TestClient) -> None:
    response = api.post("/api/session/reset")

    assert response.status_code == 200
    assert response.json() == {"status": "no-session"}


def test_a_session_can_be_started_again_after_reset(api: TestClient) -> None:
    api.post("/api/session/start")
    api.post("/api/session/reset")

    assert api.post("/api/session/start").status_code == 200
    assert api.get("/api/session/state").status_code == 200


# --------------------------------------------------------------------------- #
# Typed injection
# --------------------------------------------------------------------------- #


def test_inject_offer_accepts_the_documented_body_and_returns_state(
    api: TestClient,
) -> None:
    api.post("/api/session/start")

    response = api.post(
        "/api/session/inject-offer",
        json={"from": HUMAN_ID, "to": MAXI, "resource": "budget", "amount": 12.0},
    )

    assert response.status_code == 200
    # Not necessarily log[0]: agents may already have moved this round.
    human_offers = [
        entry for entry in response.json()["offer_log"] if entry["from"] == HUMAN_ID
    ]
    assert len(human_offers) == 1
    assert human_offers[0]["to"] == MAXI
    assert human_offers[0]["amount"] == 12.0
    assert human_offers[0]["accepted"] is None


def test_inject_offer_is_404_without_a_session(api: TestClient) -> None:
    response = api.post(
        "/api/session/inject-offer",
        json={"from": HUMAN_ID, "to": MAXI, "resource": "budget", "amount": 12.0},
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"from": HUMAN_ID, "to": "ghost", "resource": "budget", "amount": 5}, "recipient"),
        ({"from": HUMAN_ID, "to": MAXI, "resource": "gold", "amount": 5}, "resource"),
        ({"from": HUMAN_ID, "to": MAXI, "resource": "budget", "amount": 0}, "greater than"),
        ({"from": HUMAN_ID, "to": MAXI, "resource": "budget", "amount": 9999}, "holds only"),
    ],
)
def test_invalid_offers_are_400_with_a_readable_reason(
    api: TestClient, body: dict[str, Any], expected: str
) -> None:
    api.post("/api/session/start")

    response = api.post("/api/session/inject-offer", json=body)

    assert response.status_code == 400
    assert expected in response.json()["detail"]


def test_a_malformed_body_is_422(api: TestClient) -> None:
    api.post("/api/session/start")

    response = api.post(
        "/api/session/inject-offer",
        json={"from": HUMAN_ID, "to": MAXI, "amount": "lots"},
    )

    assert response.status_code == 422


def test_unknown_fields_in_the_body_are_rejected(api: TestClient) -> None:
    """extra="forbid" makes contract drift visible rather than silent."""
    api.post("/api/session/start")

    response = api.post(
        "/api/session/inject-offer",
        json={
            "from": HUMAN_ID,
            "to": MAXI,
            "resource": "budget",
            "amount": 5,
            "urgent": True,
        },
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Voice
# --------------------------------------------------------------------------- #


def test_voice_offer_returns_transcript_and_parsed_offer() -> None:
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber()
    app.state.offer_parser = FakeParser(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )
    client = TestClient(app)
    client.post("/api/session/start")

    body = client.post("/api/session/voice-offer", files=audio_upload()).json()

    assert set(body) == {"transcript", "parsed_offer", "confidence"}
    assert body["transcript"] == "give the maximizer twelve budget"
    assert body["parsed_offer"] == {
        "from": HUMAN_ID,
        "to": MAXI,
        "resource": "budget",
        "amount": 12.0,
    }
    assert body["confidence"] == "high"


def test_voice_offer_is_low_confidence_when_it_cannot_be_parsed() -> None:
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber(text="uhh what were we doing")
    app.state.offer_parser = FakeParser(None)
    client = TestClient(app)

    body = client.post("/api/session/voice-offer", files=audio_upload()).json()

    assert body["parsed_offer"] is None
    assert body["confidence"] == "low"


def test_voice_offer_is_low_confidence_when_the_audio_was_unclear() -> None:
    """Both halves must hold: a parse off muddy audio is still not trustworthy."""
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber(avg_logprob=-2.5)
    app.state.offer_parser = FakeParser(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )
    client = TestClient(app)

    body = client.post("/api/session/voice-offer", files=audio_upload()).json()

    assert body["parsed_offer"] is not None
    assert body["confidence"] == "low"


def test_voice_offer_does_not_change_game_state() -> None:
    """Speech only previews; confirmation goes through /inject-offer."""
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber()
    app.state.offer_parser = FakeParser(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )
    client = TestClient(app)
    client.post("/api/session/start")

    client.post("/api/session/voice-offer", files=audio_upload())
    human_offers = [
        offer
        for offer in client.get("/api/session/state").json()["offer_log"]
        if offer["from"] == HUMAN_ID
    ]

    assert human_offers == []


def test_voice_offer_works_before_a_session_is_started() -> None:
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber()
    app.state.offer_parser = FakeParser(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )
    client = TestClient(app)

    response = client.post("/api/session/voice-offer", files=audio_upload())

    assert response.status_code == 200
    assert response.json()["parsed_offer"] is not None


def test_an_empty_upload_is_400() -> None:
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber()
    client = TestClient(app)

    response = client.post("/api/session/voice-offer", files=audio_upload(b""))

    assert response.status_code == 400


def test_a_transcription_failure_is_503_not_a_crash() -> None:
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber(error=RuntimeError("model unavailable"))
    client = TestClient(app)

    response = client.post("/api/session/voice-offer", files=audio_upload())

    assert response.status_code == 503
    assert "model unavailable" in response.json()["detail"]


def test_voice_offer_with_no_parser_configured_still_returns_the_transcript() -> None:
    """No API key means no parser — the transcript is still useful on its own."""
    app = create_app(make_settings())
    app.state.transcriber = FakeTranscriber()
    client = TestClient(app)

    body = client.post("/api/session/voice-offer", files=audio_upload()).json()

    assert body["transcript"] == "give the maximizer twelve budget"
    assert body["parsed_offer"] is None
    assert body["confidence"] == "low"


def test_a_missing_file_field_is_422(api: TestClient) -> None:
    assert api.post("/api/session/voice-offer").status_code == 422


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def test_no_llm_client_is_built_without_a_key() -> None:
    app = create_app(make_settings(gemini_api_key=None))

    assert app.state.llm_client is None
    assert app.state.offer_parser is None


def test_a_key_builds_the_client_and_the_voice_parser() -> None:
    app = create_app(make_settings(gemini_api_key="fake-key", use_mock_agents=True))

    assert app.state.llm_client is not None
    assert app.state.offer_parser is not None


def test_cors_headers_are_present_on_api_routes(api: TestClient) -> None:
    api.post("/api/session/start")

    response = api.get("/api/session/state", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

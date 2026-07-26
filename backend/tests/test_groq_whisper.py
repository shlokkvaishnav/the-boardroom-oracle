"""Hosted transcription, and the fallback to the local model.

No network: a fake transport stands in for Groq. The property that matters is
that a Groq failure of *any* kind costs latency, never the recording.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.config import Settings
from app.speech.groq_whisper import (
    FallbackTranscriber,
    GroqTranscriber,
    GroqTranscriptionError,
)
from app.speech.transcribe import CLEAR_AUDIO_LOGPROB, Transcription


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "groq_api_key": "fake",
        "groq_whisper_model": "whisper-large-v3-turbo",
        "whisper_language": "en",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def client_returning(status: int, body: Any) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        handler.seen = request  # type: ignore[attr-defined]
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


VERBOSE = {
    "text": "  let's talk about the protest  ",
    "language": "en",
    "segments": [
        {"avg_logprob": -0.2, "text": "let's talk"},
        {"avg_logprob": -0.4, "text": "about the protest"},
    ],
}


class LocalStub:
    def __init__(self, text: str = "local fallback") -> None:
        self.text = text
        self.calls = 0

    async def load(self) -> None: ...

    async def transcribe(self, audio: bytes, filename: str) -> Transcription:
        self.calls += 1
        return Transcription(text=self.text, avg_logprob=-0.3)


class Exploding:
    async def transcribe(self, audio: bytes, filename: str) -> Transcription:
        raise GroqTranscriptionError("groq unreachable: boom")


# --------------------------------------------------------------------------- #
# Groq
# --------------------------------------------------------------------------- #


async def test_a_verbose_json_response_becomes_a_transcription() -> None:
    t = GroqTranscriber(make_settings(), client=client_returning(200, VERBOSE))

    result = await t.transcribe(b"audio", "clip.webm")

    assert result.text == "let's talk about the protest"
    assert result.language == "en"


async def test_segment_logprobs_are_averaged_so_confidence_survives() -> None:
    """`is_clear` and the API's `confidence` field are computed from this."""
    t = GroqTranscriber(make_settings(), client=client_returning(200, VERBOSE))

    result = await t.transcribe(b"audio", "clip.webm")

    assert result.avg_logprob == pytest.approx(-0.3)
    assert result.is_clear is True


async def test_a_response_without_segments_falls_back_to_the_threshold() -> None:
    t = GroqTranscriber(
        make_settings(), client=client_returning(200, {"text": "hi", "segments": []})
    )

    result = await t.transcribe(b"audio", "clip.webm")

    assert result.avg_logprob == CLEAR_AUDIO_LOGPROB


async def test_verbose_json_is_requested_because_plain_json_has_no_logprobs() -> None:
    client = client_returning(200, VERBOSE)
    t = GroqTranscriber(make_settings(), client=client)

    await t.transcribe(b"audio", "clip.webm")

    body = client._transport.handler.seen.content  # type: ignore[attr-defined]
    assert b"verbose_json" in body
    assert b"whisper-large-v3-turbo" in body


async def test_a_non_200_is_an_error_not_a_silent_empty_transcript() -> None:
    t = GroqTranscriber(make_settings(), client=client_returning(401, "bad key"))

    with pytest.raises(GroqTranscriptionError, match="401"):
        await t.transcribe(b"audio", "clip.webm")


# --------------------------------------------------------------------------- #
# Fallback
# --------------------------------------------------------------------------- #


async def test_groq_is_used_when_it_works() -> None:
    local = LocalStub()
    t = FallbackTranscriber(
        GroqTranscriber(make_settings(), client=client_returning(200, VERBOSE)), local
    )

    result = await t.transcribe(b"audio", "clip.webm")

    assert result.text == "let's talk about the protest"
    assert local.calls == 0


async def test_a_groq_failure_falls_back_to_the_local_model() -> None:
    """A slow transcription beats a 503 for someone holding a microphone."""
    local = LocalStub()
    t = FallbackTranscriber(Exploding(), local)

    result = await t.transcribe(b"audio", "clip.webm")

    assert result.text == "local fallback"
    assert local.calls == 1


async def test_an_unexpected_error_also_falls_back() -> None:
    class Weird:
        async def transcribe(self, audio: bytes, filename: str) -> Transcription:
            raise ValueError("something nobody predicted")

    local = LocalStub()

    assert (await FallbackTranscriber(Weird(), local).transcribe(b"a", "c.webm")).text == (
        "local fallback"
    )


async def test_load_preloads_the_local_model() -> None:
    """The local model is the slow one; preloading it is the point."""
    loaded: list[str] = []

    class Tracker(LocalStub):
        async def load(self) -> None:
            loaded.append("local")

    await FallbackTranscriber(GroqTranscriber(make_settings()), Tracker()).load()

    assert loaded == ["local"]

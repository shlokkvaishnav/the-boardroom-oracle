"""Groq-hosted Whisper, with the local model as a fallback.

Local faster-whisper on CPU forces a choice between fast and accurate: `tiny`
transcribes a short clip in ~1.7s and mangles proper nouns, `small` gets them
right and takes several seconds. Groq runs `whisper-large-v3` at roughly two
hundred times realtime, so the same clip comes back in a fraction of a second
*and* more accurately than anything that fits on this CPU.

It is not a hard dependency. `FallbackTranscriber` tries Groq and drops to the
local model whenever there is no key, the network is down, or Groq answers with
anything other than success — so the voice path keeps working on a laptop with
no credentials, which is the same principle as the mock-agent fallback.

Both classes satisfy the `Transcriber` protocol, so nothing downstream — the
`is_clear` threshold, the `confidence` field, the endpoints — changes at all.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.speech.transcribe import CLEAR_AUDIO_LOGPROB, Transcriber, Transcription

logger = logging.getLogger("boardroom.speech")

__all__ = ["GroqTranscriber", "FallbackTranscriber", "GroqTranscriptionError"]

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqTranscriptionError(RuntimeError):
    """Groq could not be reached, or refused the request."""


class GroqTranscriber:
    """Hosted Whisper over Groq's OpenAI-compatible audio endpoint."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def load(self) -> None:
        """Nothing to load — the model lives on Groq's side.

        Present because `main.py` preloads the transcriber and the protocol
        does not require this method; a no-op keeps the wrapper interchangeable
        with the local one.
        """
        return None

    async def transcribe(self, audio: bytes, filename: str) -> Transcription:
        payload = {
            "model": (None, self._settings.groq_whisper_model),
            # verbose_json is what carries per-segment avg_logprob, which is
            # what `Transcription.is_clear` and the `confidence` field are
            # computed from. Plain json would silently flatten confidence.
            "response_format": (None, "verbose_json"),
            "temperature": (None, "0"),
        }
        if self._settings.whisper_language:
            payload["language"] = (None, self._settings.whisper_language)

        files: dict[str, Any] = {
            "file": (filename or "audio.webm", audio, "application/octet-stream"),
            **payload,
        }

        try:
            client = self._client or httpx.AsyncClient()
            try:
                response = await client.post(
                    GROQ_TRANSCRIBE_URL,
                    headers={"Authorization": f"Bearer {self._settings.groq_api_key}"},
                    files=files,
                    timeout=self._settings.groq_timeout_seconds,
                )
            finally:
                if self._client is None:
                    await client.aclose()
        except Exception as exc:  # network, DNS, timeout
            raise GroqTranscriptionError(f"groq unreachable: {exc}") from exc

        if response.status_code != 200:
            raise GroqTranscriptionError(
                f"groq returned {response.status_code}: {response.text[:200]}"
            )

        return self._to_transcription(response.json())

    @staticmethod
    def _to_transcription(body: dict[str, Any]) -> Transcription:
        text = str(body.get("text") or "").strip()
        segments = body.get("segments") or []
        logprobs = [
            seg["avg_logprob"]
            for seg in segments
            if isinstance(seg, dict) and isinstance(seg.get("avg_logprob"), (int, float))
        ]
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else CLEAR_AUDIO_LOGPROB
        return Transcription(
            text=text,
            avg_logprob=avg_logprob,
            language=body.get("language"),
        )


class FallbackTranscriber:
    """Try `primary`, fall back to `secondary` on any failure.

    Deliberately catches broadly. A transcription that succeeds slowly is a far
    better outcome than a 503, and there is no failure from a remote provider
    worth surfacing to someone holding a microphone.
    """

    def __init__(self, primary: Transcriber, secondary: Transcriber) -> None:
        self._primary = primary
        self._secondary = secondary

    async def load(self) -> None:
        # Only the local model has anything to load, and preloading it is the
        # point: it is the thing that is slow when it is cold.
        for transcriber in (self._primary, self._secondary):
            loader = getattr(transcriber, "load", None)
            if loader is not None:
                await loader()

    async def transcribe(self, audio: bytes, filename: str) -> Transcription:
        try:
            return await self._primary.transcribe(audio, filename)
        except Exception as exc:
            logger.warning("hosted transcription failed, using local model: %s", exc)
            return await self._secondary.transcribe(audio, filename)

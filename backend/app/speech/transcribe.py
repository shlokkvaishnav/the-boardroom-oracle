"""Speech-to-text for the voice-offer endpoint, via faster-whisper.

Two things matter operationally:

1. The model is loaded lazily and exactly once, behind a lock. Loading is
   slow and downloads weights on first use, so `WHISPER_PRELOAD=true` moves
   that cost to application startup rather than into the middle of a demo.
2. Transcription is CPU-bound and blocking, so it runs in a worker thread.
   Doing it inline would freeze the negotiation loop and every open WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings

logger = logging.getLogger("boardroom.speech")

__all__ = ["Transcription", "Transcriber", "WhisperTranscriber"]

#: Whisper reports an average log-probability per segment. Below this, the
#: audio was probably unclear, and the endpoint reports low confidence
#: regardless of whether the text happened to parse.
CLEAR_AUDIO_LOGPROB = -1.0


@dataclass
class Transcription:
    text: str
    #: Mean segment log-probability; higher (closer to 0) is more confident.
    avg_logprob: float
    language: str | None = None

    @property
    def is_clear(self) -> bool:
        return bool(self.text.strip()) and self.avg_logprob >= CLEAR_AUDIO_LOGPROB


class Transcriber(Protocol):
    """The seam that lets tests skip Whisper entirely."""

    async def transcribe(self, audio: bytes, filename: str) -> Transcription: ...


class WhisperTranscriber:
    """faster-whisper, loaded once and called off the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    async def load(self) -> None:
        """Load the model if it isn't already. Safe to call concurrently."""
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return
            logger.info(
                "loading whisper model %r (compute_type=%s)",
                self._settings.whisper_model,
                self._settings.whisper_compute_type,
            )
            self._model = await asyncio.to_thread(self._build_model)
            logger.info("whisper model ready")

    def _build_model(self) -> Any:
        # Imported here rather than at module scope so that importing the app
        # doesn't pull in the ML stack when speech isn't being used.
        from faster_whisper import WhisperModel

        return WhisperModel(
            self._settings.whisper_model,
            device="cpu",
            compute_type=self._settings.whisper_compute_type,
        )

    async def transcribe(self, audio: bytes, filename: str) -> Transcription:
        await self.load()
        return await asyncio.to_thread(self._transcribe_sync, audio, filename)

    def _transcribe_sync(self, audio: bytes, filename: str) -> Transcription:
        assert self._model is not None

        # A real file on disk is the most robust input: PyAV sniffs the
        # container, and browser recordings arrive as webm/ogg/mp4 as often as
        # wav. The suffix is preserved to help that sniffing along.
        suffix = Path(filename).suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
            handle.write(audio)
            handle.flush()
            segments, info = self._model.transcribe(handle.name, beam_size=5)
            collected = list(segments)

        text = " ".join(segment.text.strip() for segment in collected).strip()
        logprobs = [
            segment.avg_logprob
            for segment in collected
            if getattr(segment, "avg_logprob", None) is not None
        ]
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else CLEAR_AUDIO_LOGPROB

        return Transcription(
            text=text,
            avg_logprob=avg_logprob,
            language=getattr(info, "language", None),
        )

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
import os
import tempfile
from contextlib import suppress
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
        #
        # The file is written, CLOSED, and only then handed to the model.
        # Windows opens NamedTemporaryFile exclusively, so PyAV reopening it by
        # name while our handle is still open fails with "Permission denied" —
        # on Linux the same code works, which is how this survived being
        # developed in Docker. `delete=False` plus an explicit unlink is the
        # portable shape.
        suffix = Path(filename).suffix or ".webm"
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            handle.write(audio)
            handle.close()
            # Tuned for short spoken commands on CPU, where latency is the
            # whole experience:
            #   beam_size=1  — greedy. Beam search costs several times the time
            #                  for accuracy that a one-line instruction doesn't
            #                  need.
            #   vad_filter   — strips leading/trailing silence. Browser
            #                  recordings start before you do, and a clip that is
            #                  mostly silence is what came back empty.
            #   condition_on_previous_text=False — no prior context to carry, and
            #                  it stops the model inventing continuations.
            segments, info = self._model.transcribe(
                handle.name,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                language=self._settings.whisper_language or None,
            )
            # `segments` is a generator that reads the file lazily, so it must
            # be drained before the file goes away.
            collected = list(segments)
        finally:
            handle.close()
            with suppress(OSError):
                os.unlink(handle.name)

        text = " ".join(segment.text.strip() for segment in collected).strip()
        logprobs = [
            segment.avg_logprob
            for segment in collected
            if getattr(segment, "avg_logprob", None) is not None
        ]
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else CLEAR_AUDIO_LOGPROB

        logger.info(
            "transcribed %d bytes -> %d segment(s), %d chars (avg_logprob %.2f)",
            len(audio),
            len(collected),
            len(text),
            avg_logprob,
        )
        return Transcription(
            text=text,
            avg_logprob=avg_logprob,
            language=getattr(info, "language", None),
        )

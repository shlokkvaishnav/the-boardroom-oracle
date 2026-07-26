"""The temp-file handoff to the speech model.

`WhisperTranscriber` writes the upload to disk and hands the *path* to
faster-whisper, which reopens it. That handoff is platform-sensitive and is the
one part of the class worth testing directly — the model itself is mocked
everywhere else.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.speech.transcribe import CLEAR_AUDIO_LOGPROB, WhisperTranscriber


class Segment:
    def __init__(self, text: str, avg_logprob: float) -> None:
        self.text = text
        self.avg_logprob = avg_logprob


class ReopeningModel:
    """Stands in for faster-whisper, reopening the path exactly as PyAV does."""

    def __init__(self, *segments: Segment) -> None:
        self._segments = segments
        self.seen_bytes: bytes | None = None
        self.path: str | None = None

    def transcribe(self, path: str, **_: Any) -> tuple[Any, Any]:
        # The real decoder opens the file by name while our writer is done with
        # it. On Windows this raises PermissionError if the writing handle is
        # still open — which is the bug this file exists to prevent.
        self.path = path
        self.seen_bytes = Path(path).read_bytes()
        return iter(self._segments), type("Info", (), {"language": "en"})()


def build(*segments: Segment) -> tuple[WhisperTranscriber, ReopeningModel]:
    transcriber = WhisperTranscriber(Settings(_env_file=None))  # type: ignore[call-arg]
    model = ReopeningModel(*segments)
    transcriber._model = model  # already "loaded"
    return transcriber, model


async def test_the_model_can_reopen_the_temp_file_by_path() -> None:
    """The Windows regression: an open write handle made this a 503."""
    transcriber, model = build(Segment("hello there", -0.2))

    result = await transcriber.transcribe(b"fake-audio-bytes", "clip.webm")

    assert model.seen_bytes == b"fake-audio-bytes"
    assert result.text == "hello there"


async def test_the_temp_file_is_cleaned_up() -> None:
    transcriber, model = build(Segment("hi", -0.2))

    await transcriber.transcribe(b"bytes", "clip.webm")

    assert model.path is not None
    assert not os.path.exists(model.path)


async def test_the_temp_file_is_cleaned_up_even_when_the_model_raises() -> None:
    transcriber = WhisperTranscriber(Settings(_env_file=None))  # type: ignore[call-arg]
    seen: dict[str, str] = {}

    class Exploding:
        def transcribe(self, path: str, **_: Any) -> Any:
            seen["path"] = path
            raise RuntimeError("model exploded")

    transcriber._model = Exploding()

    with pytest.raises(RuntimeError, match="model exploded"):
        await transcriber.transcribe(b"bytes", "clip.webm")

    assert not os.path.exists(seen["path"])


async def test_the_upload_suffix_is_preserved_for_container_sniffing() -> None:
    """PyAV sniffs the container; the extension helps it."""
    transcriber, model = build(Segment("hi", -0.2))

    await transcriber.transcribe(b"bytes", "recording.ogg")

    assert model.path is not None and model.path.endswith(".ogg")


async def test_a_nameless_upload_still_gets_a_usable_suffix() -> None:
    transcriber, model = build(Segment("hi", -0.2))

    await transcriber.transcribe(b"bytes", "noextension")

    assert model.path is not None and model.path.endswith(".webm")


async def test_segment_logprobs_are_averaged() -> None:
    transcriber, _ = build(Segment("a", -0.2), Segment("b", -0.6))

    result = await transcriber.transcribe(b"bytes", "clip.webm")

    assert result.text == "a b"
    assert result.avg_logprob == pytest.approx(-0.4)


async def test_no_segments_falls_back_to_the_clarity_threshold() -> None:
    transcriber, _ = build()

    result = await transcriber.transcribe(b"bytes", "clip.webm")

    assert result.text == ""
    assert result.avg_logprob == CLEAR_AUDIO_LOGPROB
    assert result.is_clear is False  # blank text is never "clear"

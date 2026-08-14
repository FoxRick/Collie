"""Collie's single, local-only English dictation backend."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import threading
import wave
from pathlib import Path
from typing import Any

from collie_core.db import collie_home

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 60


class VoiceInputError(ValueError):
    """A safe, user-facing local dictation error."""


def _decode_wav_data_url(data_url: str) -> tuple[list[float], int]:
    prefix = "data:audio/wav;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise VoiceInputError("I need a WAV recording from the microphone.")

    try:
        raw = base64.b64decode(data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VoiceInputError("That recording did not arrive clearly. Try once more?") from exc
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise VoiceInputError("Keep each recording under one minute.")

    try:
        with wave.open(io.BytesIO(raw), "rb") as recording:
            channels = recording.getnchannels()
            sample_width = recording.getsampwidth()
            sample_rate = recording.getframerate()
            frame_count = recording.getnframes()
            if channels not in {1, 2} or sample_width != 2 or sample_rate < 8_000:
                raise VoiceInputError("That microphone format is not supported yet.")
            if frame_count / sample_rate > MAX_AUDIO_SECONDS + 1:
                raise VoiceInputError("Keep each recording under one minute.")
            pcm = recording.readframes(frame_count)
    except (wave.Error, EOFError) as exc:
        raise VoiceInputError("I could not read that recording. Try once more?") from exc

    values = memoryview(pcm).cast("h")
    if channels == 1:
        audio = [sample / 32768.0 for sample in values]
    else:
        audio = [
            (values[index] + values[index + 1]) / 65536.0 for index in range(0, len(values) - 1, 2)
        ]
    if not audio:
        raise VoiceInputError("I did not hear anything in that recording.")
    return audio, sample_rate


class LocalVoiceService:
    """Lazily downloads and reuses one Moonshine transcriber."""

    def __init__(self, cache_root: Path | None = None) -> None:
        self.cache_root = cache_root or collie_home() / "models" / "moonshine"
        self._transcriber: Any = None
        self._load_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()

    def _get_transcriber(self) -> Any:
        if self._transcriber is not None:
            return self._transcriber
        with self._load_lock:
            if self._transcriber is not None:
                return self._transcriber
            try:
                from moonshine_voice import (
                    ModelArch,
                    Transcriber,
                    get_model_for_language,
                )
            except ImportError as exc:
                raise VoiceInputError(
                    "Local voice is not installed in this build of Collie yet."
                ) from exc

            self.cache_root.mkdir(parents=True, exist_ok=True)
            try:
                model_path, model_arch = get_model_for_language(
                    "en",
                    ModelArch.TINY_STREAMING,
                    cache_root=self.cache_root,
                )
                self._transcriber = Transcriber(
                    model_path=model_path,
                    model_arch=model_arch,
                    options={"return_audio_data": "false"},
                )
            except Exception as exc:
                raise VoiceInputError(
                    "I could not prepare the local voice model. Check your connection once, "
                    "then try again."
                ) from exc
        return self._transcriber

    def _transcribe(self, audio: list[float], sample_rate: int) -> str:
        transcriber = self._get_transcriber()
        with self._transcribe_lock:
            transcript = transcriber.transcribe_without_streaming(audio, sample_rate)
        return " ".join(
            str(line.text).strip()
            for line in getattr(transcript, "lines", [])
            if str(getattr(line, "text", "")).strip()
        ).strip()

    async def transcribe_data_url(self, data_url: str) -> str:
        audio, sample_rate = _decode_wav_data_url(data_url)
        text = await asyncio.to_thread(self._transcribe, audio, sample_rate)
        if not text:
            raise VoiceInputError("I did not catch any words. Try speaking a little closer?")
        return text

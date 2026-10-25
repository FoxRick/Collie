from __future__ import annotations

import asyncio
import base64
import io
import sys
import threading
import wave
from types import SimpleNamespace

import pytest

from collie_core.voice import LocalVoiceService, VoiceInputError, _decode_wav_data_url


def _wav_data_url(samples: list[int], *, rate: int = 16_000) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(
            b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples)
        )
    return "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_decode_wav_data_url_returns_normalized_mono() -> None:
    audio, rate = _decode_wav_data_url(_wav_data_url([-32768, 0, 32767]))
    assert rate == 16_000
    assert audio == pytest.approx([-1.0, 0.0, 32767 / 32768])


def test_decode_rejects_non_wav_data_url() -> None:
    with pytest.raises(VoiceInputError, match="WAV"):
        _decode_wav_data_url("data:audio/webm;base64,AAAA")


@pytest.mark.asyncio
async def test_service_returns_backend_transcript(monkeypatch, tmp_path) -> None:
    service = LocalVoiceService(cache_root=tmp_path)
    service._transcriber = object()  # No model download in focused unit checks.
    monkeypatch.setattr(service, "_transcribe", lambda audio, rate: "hello collie")
    result = await service.transcribe_data_url(_wav_data_url([0] * 8_000))
    assert result == "hello collie"


@pytest.mark.asyncio
async def test_service_rejects_empty_transcript(monkeypatch, tmp_path) -> None:
    service = LocalVoiceService(cache_root=tmp_path)
    service._transcriber = object()  # No model download in focused unit checks.
    monkeypatch.setattr(service, "_transcribe", lambda audio, rate: "")
    with pytest.raises(VoiceInputError, match="did not catch"):
        await service.transcribe_data_url(_wav_data_url([0] * 8_000))


@pytest.mark.asyncio
async def test_preparation_reuse_and_cancelled_inference_keeps_bounded_slot(monkeypatch, tmp_path):
    """Public prepare/transcribe pipeline, controlled native API; no model download."""
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    loads = []
    calls = []

    class Transcriber:
        def __init__(self, **kwargs):
            loads.append(kwargs)

        def transcribe_without_streaming(self, audio, sample_rate):
            calls.append((len(audio), sample_rate))
            entered.set()
            assert release.wait(5), "test did not release controlled inference"
            finished.set()
            return SimpleNamespace(lines=[SimpleNamespace(text=" local words ")])

    monkeypatch.setitem(
        sys.modules,
        "moonshine_voice",
        SimpleNamespace(
            ModelArch=SimpleNamespace(TINY_STREAMING="tiny"),
            Transcriber=Transcriber,
            get_model_for_language=lambda *args, **kwargs: (tmp_path, "tiny"),
        ),
    )
    service = LocalVoiceService(cache_root=tmp_path)
    await asyncio.gather(service.prepare(), service.prepare())
    assert len(loads) == 1
    audio = _wav_data_url([0] * 8_000)
    pending = asyncio.create_task(service.transcribe_data_url(audio))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(VoiceInputError, match="previous recording"):
            await service.transcribe_data_url(audio)
        assert calls == [(8_000, 16_000)]
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 5)
    # Yield until the retained native task has released its slot, with a deadline.
    async with asyncio.timeout(5):
        while True:
            try:
                result = await service.transcribe_data_url(audio)
                break
            except VoiceInputError as error:
                assert "previous recording" in str(error)
                await asyncio.sleep(0.001)
    assert result == "local words"
    assert len(loads) == 1

from __future__ import annotations

import base64
import io
import wave

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
    monkeypatch.setattr(service, "_transcribe", lambda audio, rate: "hello collie")
    result = await service.transcribe_data_url(_wav_data_url([0] * 8_000))
    assert result == "hello collie"


@pytest.mark.asyncio
async def test_service_rejects_empty_transcript(monkeypatch, tmp_path) -> None:
    service = LocalVoiceService(cache_root=tmp_path)
    monkeypatch.setattr(service, "_transcribe", lambda audio, rate: "")
    with pytest.raises(VoiceInputError, match="did not catch"):
        await service.transcribe_data_url(_wav_data_url([0] * 8_000))

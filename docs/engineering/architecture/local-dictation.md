# Local dictation

Collie has one speech-to-text path: **Moonshine Tiny Streaming English Q8**.
It does not call the active chat provider or any cloud transcription service.

## Why this model

- English model and runtime are MIT-licensed.
- The shipped model is 8-bit quantized and designed for CPU inference.
- At 34 million parameters, it offers a better English accuracy/latency balance
  than Whisper Tiny for live voice input without committing Collie to a large model.
- The same Moonshine runtime can support true streaming later, so this baseline
  does not need to be replaced when the voice experience matures.

## Flow

1. The Electron renderer captures mono microphone PCM and creates a 16-bit WAV.
2. The WAV travels only over Collie's localhost WebSocket.
3. `collie_core.voice.LocalVoiceService` validates and decodes it.
4. On first use, Moonshine downloads the pinned English model into
   `~/.collie/models/moonshine`.
5. The cached transcriber returns text to the composer. The user reviews and
   sends it through the normal agent flow.

Recordings are capped at 60 seconds. The model is loaded lazily and reused for
the lifetime of the core process.

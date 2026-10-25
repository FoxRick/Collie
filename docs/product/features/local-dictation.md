# Local dictation

**Status:** implemented in source; packaged microphone/model acceptance pending

Dictation records up to one minute from the microphone selected in Settings →
Audio. Stop produces local English text in the editable composer. It never sends
a message automatically. Microphone selection and the settings microphone test
continue to use their existing paths.

On a microphone gesture, `prepare_voice` begins preparing the reused Moonshine
model while the renderer opens the microphone and records. Previously the first
model preparation began only after Stop. This overlaps preparation with recording;
it does not establish a measured hardware latency or recognition-quality gain.
The first preparation can download model assets; recordings are transcribed
locally, without a paid service or mandatory account.

The composer shows microphone startup immediately. Its microphone control stops
recording, or cancels while opening/transcribing. Sending, changing conversation,
and unmounting invalidate pending results. Final text appends to the current draft,
preserving edits made while waiting. No partial text or live streaming is exposed
in this increment: the existing `transcribe_without_streaming` API remains in use.

`DictationSession` owns renderer operation order and cancellation. `audio.ts` stops
tracks and closes the audio context on completion, cancellation and setup failure;
a late microphone grant after cancellation is released. Capture storage is capped
at one minute even if a browser timer is delayed. Only one microphone acquisition
may remain pending across composers. A pending OS permission prompt cannot be
forcibly dismissed by the browser API.

`LocalVoiceService` shares one preparation task and admits one native transcription
at a time. Further requests receive a retry message rather than queuing inference.
Cancellation discards output but cannot interrupt a native model load/inference
already running. That operation retains the slot until it finishes. A failed model
preparation can be retried; one model remains reused for the process lifetime.
Voice commands run as background IPC tasks so preparation/transcription do not
hold up normal chat, stop, or other commands on the same connection.

Focused acceptance uses controlled microphone buffers and a controlled native
transcriber, without model downloads or an inference service. Renderer checks
cover state order, selected device, draft edits, no auto-send, chat-switch/cancel
isolation, late permission grants, and track/context/timer cleanup. Backend checks
cover shared preparation, model reuse and admission after cancelled inference.
Real Windows microphone latency, packaged Moonshine loading and recognition quality
remain hardware/package acceptance work.

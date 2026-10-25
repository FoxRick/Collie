import { startLocalDictation, type LocalDictationRecorder } from './audio'

export type DictationState = 'idle' | 'starting' | 'recording' | 'transcribing'

/** One composer capture. Cancel invalidates every pending continuation. */
export class DictationSession {
  private generation = 0
  private recorder: LocalDictationRecorder | null = null
  private abort: AbortController | null = null
  state: DictationState = 'idle'

  constructor(private readonly callbacks: {
    prepare: () => Promise<unknown>
    transcribe: (audio: string) => Promise<string>
    state: (state: DictationState) => void
    transcript: (text: string) => void
    error: (message: string) => void
  }) {}

  private change(state: DictationState): void {
    this.state = state
    this.callbacks.state(state)
  }

  async start(deviceId?: string): Promise<void> {
    if (this.state !== 'idle') return
    const generation = ++this.generation
    this.abort = new AbortController()
    this.change('starting')
    // Start only on a microphone gesture, never on app boot. A failed preparation
    // is reported by the transcription request, and cannot block microphone access.
    void this.callbacks.prepare().catch(() => undefined)
    try {
      const recorder = await startLocalDictation(
        () => void this.stop(), deviceId, this.abort.signal
      )
      if (generation !== this.generation) {
        await recorder.cancel()
        return
      }
      this.recorder = recorder
      this.change('recording')
    } catch (error) {
      if (generation !== this.generation) return
      this.callbacks.error(error instanceof Error ? error.message : 'I could not open the microphone.')
      this.change('idle')
    }
  }

  async stop(): Promise<void> {
    if (this.state !== 'recording' || !this.recorder) return
    const generation = this.generation
    const recorder = this.recorder
    this.recorder = null
    this.change('transcribing')
    try {
      const audio = await recorder.stop()
      if (generation !== this.generation) return
      const text = (await this.callbacks.transcribe(audio)).trim()
      if (generation === this.generation && text) this.callbacks.transcript(text)
    } catch (error) {
      if (generation === this.generation) {
        this.callbacks.error(error instanceof Error ? error.message : 'I could not hear that clearly.')
      }
    } finally {
      if (generation === this.generation) this.change('idle')
    }
  }

  cancel(): void {
    ++this.generation
    this.abort?.abort()
    this.abort = null
    void this.recorder?.cancel().catch(() => undefined)
    this.recorder = null
    this.change('idle')
  }
}

import { afterEach, expect, it, vi } from 'vitest'
import { DictationSession } from './dictation'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('captures selected mic, prepares during capture, preserves edits, and discards cancelled results', async () => {
  vi.useFakeTimers()
  const trackStop = vi.fn()
  const stream = { getTracks: () => [{ stop: trackStop }] }
  const mic = deferred<typeof stream>()
  const getUserMedia = vi.fn().mockReturnValueOnce(mic.promise).mockResolvedValue(stream)
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
  vi.stubGlobal('window', globalThis)
  const processors: Array<{ onaudioprocess: null | ((event: unknown) => void) }> = []
  const contextClose = vi.fn().mockResolvedValue(undefined)
  class Context {
    state = 'running'
    sampleRate = 16_000
    destination = {}
    close = contextClose
    createMediaStreamSource = () => ({ connect: vi.fn(), disconnect: vi.fn() })
    createGain = () => ({ gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() })
    createScriptProcessor = () => {
      const processor = { onaudioprocess: null, connect: vi.fn(), disconnect: vi.fn() }
      processors.push(processor)
      return processor
    }
  }
  vi.stubGlobal('AudioContext', Context)
  let draft = 'original'
  const states: string[] = []
  const firstText = deferred<string>()
  const secondText = deferred<string>()
  const transcribe = vi.fn().mockReturnValueOnce(firstText.promise).mockReturnValueOnce(secondText.promise)
  const prepare = vi.fn().mockResolvedValue({ ready: true })
  const error = vi.fn()
  const session = new DictationSession({
    prepare, transcribe, state: (state) => states.push(state),
    transcript: (text) => { draft += ` ${text}` }, error
  })
  const starting = session.start('chosen-mic')
  await session.start('duplicate')
  expect(prepare).toHaveBeenCalledTimes(1)
  expect(session.state).toBe('starting')
  expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({
    audio: expect.objectContaining({ deviceId: { exact: 'chosen-mic' } })
  }))
  mic.resolve(stream)
  await starting
  const capture = () => processors.at(-1)!.onaudioprocess!({
    inputBuffer: { getChannelData: () => new Float32Array(8_000).fill(0.25) }
  })
  capture()
  const stopping = session.stop()
  await vi.waitFor(() => expect(transcribe).toHaveBeenCalledTimes(1))
  expect(transcribe.mock.calls[0][0]).toMatch(/^data:audio\/wav;base64,UklGR/)
  draft = 'edited while transcribing'
  firstText.resolve('local words')
  await stopping
  expect(draft).toBe('edited while transcribing local words')
  expect(states).toEqual(['starting', 'recording', 'transcribing', 'idle'])
  expect(trackStop).toHaveBeenCalledTimes(1)
  expect(contextClose).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)

  await session.start()
  capture()
  const stale = session.stop()
  await vi.waitFor(() => expect(transcribe).toHaveBeenCalledTimes(2))
  session.cancel() // Same invalidation used by chat switch, send and unmount.
  draft = 'another chat'
  secondText.resolve('must not arrive')
  await stale
  expect(draft).toBe('another chat')
  expect(session.state).toBe('idle')
  expect(trackStop).toHaveBeenCalledTimes(2)
  expect(error).not.toHaveBeenCalled()

  const lateMic = deferred<typeof stream>()
  getUserMedia.mockReturnValueOnce(lateMic.promise)
  const late = session.start()
  session.cancel()
  lateMic.resolve(stream)
  await late
  expect(trackStop).toHaveBeenCalledTimes(3)
  expect(processors).toHaveLength(2)
  expect(vi.getTimerCount()).toBe(0)
})

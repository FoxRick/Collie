export interface LocalDictationRecorder {
  stop: () => Promise<string>
  cancel: () => Promise<void>
}

export const MICROPHONE_STORAGE_KEY = 'collie.microphoneDeviceId'

const MAX_RECORDING_MS = 60_000
let openingMicrophone = false

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index))
  }
}

export function encodeMonoWav(samples: Float32Array, sampleRate: number): Uint8Array {
  const bytes = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(bytes)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    view.setInt16(44 + index * 2, sample < 0 ? sample * 32768 : sample * 32767, true)
  }
  return new Uint8Array(bytes)
}

function toDataUrl(bytes: Uint8Array): string {
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return `data:audio/wav;base64,${btoa(binary)}`
}

export async function startLocalDictation(
  onLimitReached: () => void,
  deviceId?: string,
  signal?: AbortSignal
): Promise<LocalDictationRecorder> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('This computer does not expose a microphone to Collie.')
  }
  if (openingMicrophone) {
    throw new Error('I am still opening the previous microphone. Try again in a moment.')
  }
  openingMicrophone = true
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    }
  }).finally(() => { openingMicrophone = false })
  let context: AudioContext | undefined
  try {
    if (signal?.aborted) throw new DOMException('Dictation cancelled', 'AbortError')
    context = new AudioContext()
    if (context.state === 'suspended') await context.resume()
    if (signal?.aborted) throw new DOMException('Dictation cancelled', 'AbortError')
    const source = context.createMediaStreamSource(stream)
    const processor = context.createScriptProcessor(4096, 1, 1)
    const silentOutput = context.createGain()
    silentOutput.gain.value = 0
    const chunks: Float32Array[] = []
    let closed = false
    let capturedSamples = 0
    const sampleRate = context.sampleRate
    const maxSamples = sampleRate * MAX_RECORDING_MS / 1000

    processor.onaudioprocess = (event) => {
      if (closed || capturedSamples >= maxSamples) return
      const input = event.inputBuffer.getChannelData(0)
      const chunk = new Float32Array(input.subarray(0, maxSamples - capturedSamples))
      chunks.push(chunk)
      capturedSamples += chunk.length
    }
    source.connect(processor)
    processor.connect(silentOutput)
    silentOutput.connect(context.destination)
    const limitTimer = window.setTimeout(onLimitReached, MAX_RECORDING_MS)

    const close = async (): Promise<void> => {
      if (closed) return
      closed = true
      window.clearTimeout(limitTimer)
      signal?.removeEventListener('abort', abort)
      processor.onaudioprocess = null
      processor.disconnect()
      source.disconnect()
      silentOutput.disconnect()
      stream.getTracks().forEach((track) => track.stop())
      await context!.close()
    }

    const abort = (): void => { void close().catch(() => undefined); chunks.length = 0 }
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) {
      await close()
      throw new DOMException('Dictation cancelled', 'AbortError')
    }
    let stopped = false
    return {
      cancel: async () => { await close(); chunks.length = 0 },
      stop: async () => {
        if (stopped || closed) throw new Error('That recording has already ended.')
        stopped = true
        await close()
        const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
        const samples = new Float32Array(total)
        let offset = 0
        for (const chunk of chunks) {
          samples.set(chunk, offset)
          offset += chunk.length
        }
        chunks.length = 0
        if (samples.length < sampleRate / 4) {
          throw new Error('Hold the microphone button a little longer so I can hear you.')
        }
        return toDataUrl(encodeMonoWav(samples, sampleRate))
      }
    }
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop())
    if (context && context.state !== 'closed') await context.close().catch(() => undefined)
    throw error
  }
}

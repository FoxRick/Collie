export interface LocalDictationRecorder {
  stop: () => Promise<string>
  cancel: () => Promise<void>
}

export const MICROPHONE_STORAGE_KEY = 'collie.microphoneDeviceId'

const MAX_RECORDING_MS = 60_000

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
  deviceId?: string
): Promise<LocalDictationRecorder> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('This computer does not expose a microphone to Collie.')
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    }
  })
  const context = new AudioContext()
  if (context.state === 'suspended') await context.resume()
  const source = context.createMediaStreamSource(stream)
  const processor = context.createScriptProcessor(4096, 1, 1)
  const silentOutput = context.createGain()
  silentOutput.gain.value = 0
  const chunks: Float32Array[] = []
  let closed = false

  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
  }
  source.connect(processor)
  processor.connect(silentOutput)
  silentOutput.connect(context.destination)
  const limitTimer = window.setTimeout(onLimitReached, MAX_RECORDING_MS)

  const close = async (): Promise<void> => {
    if (closed) return
    closed = true
    window.clearTimeout(limitTimer)
    processor.onaudioprocess = null
    processor.disconnect()
    source.disconnect()
    silentOutput.disconnect()
    stream.getTracks().forEach((track) => track.stop())
    await context.close()
  }

  return {
    cancel: close,
    stop: async () => {
      await close()
      const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
      const samples = new Float32Array(total)
      let offset = 0
      for (const chunk of chunks) {
        samples.set(chunk, offset)
        offset += chunk.length
      }
      if (samples.length < context.sampleRate / 4) {
        throw new Error('Hold the microphone button a little longer so I can hear you.')
      }
      return toDataUrl(encodeMonoWav(samples, context.sampleRate))
    }
  }
}

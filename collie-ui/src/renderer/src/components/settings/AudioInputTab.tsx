import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Mic2, RefreshCw, TriangleAlert } from 'lucide-react'
import { MICROPHONE_STORAGE_KEY } from '../../lib/audio'

interface MicrophoneOption {
  deviceId: string
  label: string
}

export default function AudioInputTab(): React.JSX.Element {
  const [microphones, setMicrophones] = useState<MicrophoneOption[]>([])
  const [selectedId, setSelectedId] = useState(
    () => localStorage.getItem(MICROPHONE_STORAGE_KEY) || ''
  )
  const [testing, setTesting] = useState(false)
  const [level, setLevel] = useState(0)
  const [notice, setNotice] = useState('Choose a microphone, then start a live test.')
  const cleanupRef = useRef<(() => void) | null>(null)

  const loadMicrophones = async (): Promise<void> => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setNotice('Microphone selection is not available on this computer.')
      return
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const inputs = devices
        .filter((device) => device.kind === 'audioinput')
        .map((device, index) => ({
          deviceId: device.deviceId,
          label: device.label || `Microphone ${index + 1}`
        }))
      setMicrophones(inputs)
      const storedId = localStorage.getItem(MICROPHONE_STORAGE_KEY) || ''
      if (storedId && !inputs.some((input) => input.deviceId === storedId)) {
        setSelectedId('')
        localStorage.removeItem(MICROPHONE_STORAGE_KEY)
      }
    } catch {
      setNotice('I could not read the available microphones.')
    }
  }

  useEffect(() => {
    void loadMicrophones()
    const handleChange = (): void => { void loadMicrophones() }
    navigator.mediaDevices?.addEventListener?.('devicechange', handleChange)
    return () => {
      cleanupRef.current?.()
      navigator.mediaDevices?.removeEventListener?.('devicechange', handleChange)
    }
  }, [])

  const stopTest = (): void => {
    cleanupRef.current?.()
    cleanupRef.current = null
    setTesting(false)
    setLevel(0)
  }

  const startTest = async (): Promise<void> => {
    stopTest()
    if (!navigator.mediaDevices?.getUserMedia) {
      setNotice('This computer does not expose a microphone to Collie.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: selectedId ? { deviceId: { exact: selectedId } } : true
      })
      await loadMicrophones()
      const context = new AudioContext()
      if (context.state === 'suspended') await context.resume()
      const source = context.createMediaStreamSource(stream)
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      analyser.smoothingTimeConstant = 0.75
      source.connect(analyser)
      const samples = new Uint8Array(analyser.fftSize)
      let frame = 0
      let heardInput = false
      const readLevel = (): void => {
        analyser.getByteTimeDomainData(samples)
        let sum = 0
        for (const sample of samples) {
          const normalized = (sample - 128) / 128
          sum += normalized * normalized
        }
        const nextLevel = Math.min(100, Math.round(Math.sqrt(sum / samples.length) * 320))
        heardInput ||= nextLevel > 3
        setLevel(nextLevel)
        setNotice(
          heardInput
            ? 'Microphone is working. I can hear the input.'
            : 'Listening… speak normally to test the input.'
        )
        frame = requestAnimationFrame(readLevel)
      }
      readLevel()
      cleanupRef.current = () => {
        cancelAnimationFrame(frame)
        source.disconnect()
        analyser.disconnect()
        stream.getTracks().forEach((track) => track.stop())
        void context.close()
      }
      setTesting(true)
    } catch (error) {
      setNotice(
        error instanceof Error && error.name === 'NotAllowedError'
          ? 'Microphone access is blocked. Allow it in system settings, then try again.'
          : 'I could not start that microphone. Try another input.'
      )
    }
  }

  return (
    <section className="settings-card audio-input-card">
      <div className="audio-input-heading">
        <span className="settings-card-icon"><Mic2 size={19} /></span>
        <div>
          <h3>Microphone</h3>
          <p>Select the input Collie should use for voice messages.</p>
        </div>
      </div>
      <label className="settings-field">
        <span>Input device</span>
        <div className="audio-device-row">
          <select
            value={selectedId}
            onChange={(event) => {
              const next = event.target.value
              setSelectedId(next)
              if (next) localStorage.setItem(MICROPHONE_STORAGE_KEY, next)
              else localStorage.removeItem(MICROPHONE_STORAGE_KEY)
              if (testing) stopTest()
            }}
          >
            <option value="">System default</option>
            {microphones.map((microphone) => (
              <option key={microphone.deviceId} value={microphone.deviceId}>
                {microphone.label}
              </option>
            ))}
          </select>
          <button type="button" className="settings-icon-button" onClick={() => void loadMicrophones()} aria-label="Refresh microphones">
            <RefreshCw size={15} />
          </button>
        </div>
      </label>
      <div className="audio-meter-block">
        <div className="audio-meter-label">
          <span>Input level</span>
          <span>{testing ? `${level}%` : 'Not testing'}</span>
        </div>
        <div className="audio-meter" role="meter" aria-label="Microphone input level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={level}>
          <span style={{ width: `${level}%` }} />
        </div>
      </div>
      <div className={`audio-test-status ${testing && level > 3 ? 'is-working' : ''}`}>
        {testing && level > 3 ? <CheckCircle2 size={16} /> : <TriangleAlert size={16} />}
        <span>{notice}</span>
      </div>
      <button
        type="button"
        className={`settings-button ${testing ? '' : 'is-primary'}`}
        onClick={() => testing ? stopTest() : void startTest()}
      >
        {testing ? 'Stop test' : 'Test microphone'}
      </button>
    </section>
  )
}

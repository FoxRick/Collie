import { useEffect, useRef, useState } from 'react'

interface Props {
  onNotice: (msg: string) => void
}

export default function PetTab({ onNotice }: Props): React.JSX.Element {
  const [behavior, setBehavior] = useState<'roam' | 'stay' | 'sleep'>('roam')
  const [size, setSize] = useState(1.0)
  const [enabled, setEnabled] = useState(false)
  const [petReady, setPetReady] = useState(false)
  const [petBusy, setPetBusy] = useState(false)
  const sizeTimerRef = useRef(0)

  useEffect(() => {
    void window.collie?.petStatus?.()
      .then((status) => setEnabled(status.enabled))
      .finally(() => setPetReady(true))
    return () => window.clearTimeout(sizeTimerRef.current)
  }, [])

  const togglePet = async (): Promise<void> => {
    if (!window.collie?.setPetEnabled) {
      onNotice('Desktop pet controls are unavailable.')
      return
    }
    setPetBusy(true)
    try {
      const status = await window.collie.setPetEnabled(!enabled)
      setEnabled(status.enabled)
      onNotice(status.enabled ? 'Desktop pet is out and ready to play.' : 'Desktop pet is resting.')
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'Could not change the desktop pet.')
    } finally {
      setPetBusy(false)
    }
  }

  const trigger = (command: string) => {
    try {
      void window.collie?.petCommand?.(command)
      onNotice(`Sent "${command}" to Collie!`)
    } catch {
      onNotice('Pet is not running')
    }
  }

  return (
    <div>
      <h3 className="mb-1 font-semibold">Desktop Pet</h3>
      <p className="mb-3 text-sm" style={{ color: 'var(--collie-paw)' }}>
        Your Border Collie lives on your desktop. See how she responds while Collie works.
      </p>

      <div className="pet-enable-row">
        <div>
          <strong>Show desktop pet</strong>
          <p>Spawn her now, or close her and keep her tucked away after restart.</p>
        </div>
        <button
          type="button"
          className={`pet-toggle ${enabled ? 'is-on' : ''}`}
          role="switch"
          aria-checked={enabled}
          aria-label="Show desktop pet"
          disabled={!petReady || petBusy}
          onClick={() => void togglePet()}
        >
          <span />
        </button>
      </div>

      <div className="mb-4">
        <h4 className="mb-2 text-sm font-medium">Quick actions</h4>
        <div className="flex gap-2">
          <button
            onClick={() => trigger('happy')}
            disabled={!enabled}
            className="settings-button is-primary"
          >
            Happy
          </button>
          <button
            onClick={() => trigger('working')}
            disabled={!enabled}
            className="settings-button"
          >
            Focus
          </button>
          <button
            onClick={() => trigger('walk')}
            disabled={!enabled}
            className="settings-button"
          >
            Walk
          </button>
          <button
            onClick={() => trigger('sleep')}
            disabled={!enabled}
            className="settings-button"
          >
            Sleep
          </button>
        </div>
      </div>

      <div className="mb-4">
        <h4 className="mb-2 text-sm font-medium">Behavior</h4>
        <div className="flex gap-2">
          {(['roam', 'stay', 'sleep'] as const).map((b) => (
            <button
              key={b}
              onClick={() => {
                setBehavior(b)
                trigger(b)
              }}
              disabled={!enabled}
              className={`settings-button ${behavior === b ? 'is-selected' : ''}`}
            >
              {b === 'roam' ? 'Roam free' : b === 'stay' ? 'Stay nearby' : 'Sleep when idle'}
            </button>
          ))}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-medium">Size</h4>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min="0.25"
            max="3.0"
            step="0.25"
            value={size}
            onChange={(e) => {
              const v = parseFloat(e.target.value)
              setSize(v)
              // Debounce: a drag fires dozens of onChange events — the pet
              // only needs the final size, not a command per tick.
              window.clearTimeout(sizeTimerRef.current)
              sizeTimerRef.current = window.setTimeout(() => {
                try {
                  void window.collie?.petCommand?.(`size:${v}`)
                } catch {
                  // pet not running — size applies next time it wakes
                }
              }, 250)
            }}
            disabled={!enabled}
            className="flex-1"
            style={{ accentColor: 'var(--collie-amber)' }}
          />
          <span className="text-sm font-mono" style={{ color: 'var(--collie-paw)' }}>
            {size}x
          </span>
        </div>
      </div>
    </div>
  )
}

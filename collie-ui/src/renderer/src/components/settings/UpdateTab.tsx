import { useEffect, useState } from 'react'
import { CheckCircle2, Download, RefreshCw, RotateCcw, TriangleAlert } from 'lucide-react'

type RendererUpdateStatus = Awaited<ReturnType<Window['collie']['updateStatus']>>

const INITIAL_STATUS: RendererUpdateStatus = {
  phase: 'idle',
  currentVersion: '',
  failedUpdate: null
}

const FAILED_UPDATE_COPY =
  "The last update didn't start properly. Your chats and settings are safe, but this version may be unstable."

export default function UpdateTab(): React.JSX.Element {
  const [status, setStatus] = useState<RendererUpdateStatus>(INITIAL_STATUS)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    // The bridge can be absent (dev tools, odd embeds) — an unguarded call
    // here throws during render and blanked the entire Settings screen.
    if (typeof window.collie?.updateStatus !== 'function') {
      setStatus((current) => ({
        ...current,
        phase: 'failed',
        message: 'Updates are not available in this window.'
      }))
      return
    }
    void window.collie.updateStatus().then(setStatus).catch(() => undefined)
    return window.collie.onUpdateStatus(setStatus)
  }, [])

  const run = async (action: () => Promise<RendererUpdateStatus>): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      setStatus(await action())
    } catch {
      setStatus((current) => ({ ...current, phase: 'failed' }))
    } finally {
      setBusy(false)
    }
  }

  const dismiss = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      setStatus(await window.collie.dismissUpdateFailure())
    } catch {
      setNotice('The notice could not be dismissed. Try again in a moment.')
    } finally {
      setBusy(false)
    }
  }

  const restart = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const result = await window.collie.restartAndInstallUpdate()
      if (!result.installed) {
        setNotice(`Finish ${result.blockedBy.join(', ')} before restarting to update.`)
      }
    } catch {
      setNotice('The update could not be installed. Try again in a moment.')
    } finally {
      setBusy(false)
    }
  }

  const version = status.availableVersion ? ` ${status.availableVersion}` : ''
  const statusCopy: Record<RendererUpdateStatus['phase'], string> = {
    idle: 'Ready to check the private alpha release channel.',
    checking: 'Checking the alpha release channel…',
    available: `Collie${version} is available. Download it when you are ready.`,
    downloading: `Downloading Collie${version}… ${Math.round(status.percent ?? 0)}%`,
    ready: `Collie${version} is downloaded and ready to install.`,
    current: status.message || 'Collie is up to date.',
    failed: status.message || 'The update check failed. Try again when you are online.',
    rollback: status.message || FAILED_UPDATE_COPY
  }

  const bridgeReady = typeof window.collie?.checkForUpdate === 'function'

  return (
    <section className="settings-card settings-control-card">
      <div className="settings-card-icon">
        {status.failedUpdate || status.phase === 'failed' || status.phase === 'rollback' ? (
          <TriangleAlert size={19} />
        ) : status.phase === 'current' ? (
          <CheckCircle2 size={19} />
        ) : (
          <RefreshCw size={19} />
        )}
      </div>
      <div>
        <h3>Collie updates</h3>
        {status.failedUpdate ? (
          <div className="update-failure-banner" role="alert">
            <TriangleAlert size={16} className="update-failure-banner-icon" />
            <p>{FAILED_UPDATE_COPY}</p>
            <button
              className="settings-button update-failure-banner-action"
              disabled={busy}
              onClick={() => void dismiss()}
            >
              Keep this version
            </button>
          </div>
        ) : (
          <p>{statusCopy[status.phase]}</p>
        )}
        <p className="mt-2 text-sm" style={{ color: 'var(--collie-text-muted)' }}>
          Installed version: {status.currentVersion || 'development build'}
        </p>
        {notice && <p className="inline-notice mt-3" role="alert">{notice}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {bridgeReady && (
          <>
            {(status.phase === 'idle' ||
              status.phase === 'current' ||
              status.phase === 'failed' ||
              status.phase === 'rollback') && (
              <button
                className="settings-button"
                disabled={busy}
                onClick={() => void run(() => window.collie.checkForUpdate())}
              >
                <RefreshCw size={15} /> Check for updates
              </button>
            )}
            {status.phase === 'available' && (
              <button
                className="settings-button is-primary"
                disabled={busy}
                onClick={() => void run(() => window.collie.downloadUpdate())}
              >
                <Download size={15} /> Download update
              </button>
            )}
            {status.phase === 'ready' && (
              <button
                className="settings-button is-primary"
                disabled={busy}
                onClick={() => void restart()}
              >
                <RotateCcw size={15} /> Restart and install
              </button>
            )}
          </>
        )}
      </div>
      <p className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
        Downloads and restarts are always your choice. Collie will refuse to restart while chats,
        approvals, routines, or external actions are active.
      </p>
    </section>
  )
}

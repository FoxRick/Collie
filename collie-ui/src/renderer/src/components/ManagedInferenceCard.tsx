import { useEffect, useState } from 'react'

type Status = Awaited<ReturnType<NonNullable<Window['account']>['inferenceStatus']>>

export default function ManagedInferenceCard({ onActivated }: {
  onActivated: () => void | Promise<void>
}): React.JSX.Element | null {
  const [status, setStatus] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const refresh = async (): Promise<void> => {
    try { setStatus(await window.account.inferenceStatus()) }
    catch { setNotice('Collie AI is unavailable. Your own providers still work.') }
  }
  useEffect(() => { void refresh() }, [])
  const connect = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      if (!status?.signedIn) {
        // Signing in must not silently switch the active provider — the
        // user chooses "Use Collie AI" as an explicit next step.
        await window.account.startSignIn()
        const after = await window.account.inferenceStatus()
        setStatus(after)
        return
      }
      const result = await window.account.useInference()
      if (!result.configured) throw new Error(result.error || 'Collie AI could not connect.')
      await onActivated()
    } catch (e) { setNotice(e instanceof Error ? e.message : 'Please try again.') }
    finally { setBusy(false) }
  }
  // A build without an endpoint cannot offer Collie AI — don't show a dead
  // card in front of the onboarding options that actually work.
  if (status && !status.configured) return null
  return (
    <section className="settings-card" aria-label="Collie AI">
      <h3>Collie AI</h3>
      <p>{status?.message ?? 'Checking included AI availability…'}</p>
      {status?.signedIn && status.limit > 0 && (
        <p>{status.remaining.toLocaleString()} of {status.limit.toLocaleString()} included allowance left
          {status.resetsAt ? ' · Resets ' + new Date(status.resetsAt).toLocaleDateString() : ''}</p>
      )}
      <p className="text-sm">Your own provider connections stay saved. Collie AI never switches to a paid provider automatically.</p>
      <div className="flex gap-2">
        <button type="button" className="secondary-button" disabled={busy || !status?.configured ||
          (status.signedIn && !status.available)} onClick={() => void connect()}>
          {busy ? 'Connecting…' : status?.signedIn ? 'Use Collie AI' : 'Sign in to Collie'}
        </button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void refresh()}>
          Refresh allowance
        </button>
      </div>
      {notice && <p role="status">{notice}</p>}
    </section>
  )
}

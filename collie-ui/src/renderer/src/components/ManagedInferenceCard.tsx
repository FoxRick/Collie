import { useEffect, useState } from 'react'

type Status = Awaited<ReturnType<NonNullable<Window['account']>['inferenceStatus']>>

export default function ManagedInferenceCard({ onActivated }: {
  onActivated: () => void | Promise<void>
}): React.JSX.Element {
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
      if (!status?.signedIn) await window.account.startSignIn()
      const next = await window.account.inferenceStatus()
      setStatus(next)
      if (!next.available) return
      const result = await window.account.useInference()
      if (!result.configured) throw new Error(result.error || 'Collie AI could not connect.')
      await onActivated()
    } catch (e) { setNotice(e instanceof Error ? e.message : 'Please try again.') }
    finally { setBusy(false) }
  }
  return (
    <section className="settings-card" aria-label="Collie AI">
      <h3>Collie AI</h3>
      <p>{status?.message ?? 'Checking included AI availability…'}</p>
      {status?.signedIn && status.limit > 0 && (
        <p>{status.remaining.toLocaleString()} / {status.limit.toLocaleString()} allowance units remaining
          {status.resetsAt ? ' · Resets ' + new Date(status.resetsAt).toLocaleDateString() : ''}</p>
      )}
      <p className="text-sm">Your own provider connections stay saved. Collie AI never switches to a paid provider automatically.</p>
      <div className="flex gap-2">
        <button type="button" className="secondary-button" disabled={busy || !status?.configured ||
          (status.signedIn && !status.available)} onClick={() => void connect()}>
          {busy ? 'Connecting…' : status?.signedIn ? 'Use Collie AI' : 'Sign in to Collie'}
        </button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void refresh()}>Refresh allowance</button>
      </div>
      {notice && <p role="status">{notice}</p>}
    </section>
  )
}

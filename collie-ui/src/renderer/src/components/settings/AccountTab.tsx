import { CloudUpload, History, LogIn, LogOut, RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'

/** Display-only account state, typed from the preload bridge (`window.account`). */
type AccountState = Awaited<ReturnType<typeof window.account.getState>>
type SyncStatus = Awaited<ReturnType<typeof window.account.syncStatus>>
type SnapshotSummary = Awaited<ReturnType<typeof window.account.syncList>>[number]

function formatWhen(iso: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit'
    })
  } catch {
    return ''
  }
}

const ACCESS_COPY: Record<AccountState['access'], { title: string; detail: string } | null> = {
  granted: {
    title: 'Early access: active',
    detail: 'Your account has full access to this alpha.'
  },
  waiting: {
    title: "You're on the list",
    detail:
      'Your spot is reserved. Collie works while you wait — access features unlock automatically.'
  },
  unknown: null
}

/**
 * Collie account card (account-system-spec.md §6): sign in via the system
 * browser (PKCE + localhost callback handled in the main process), show the
 * signed-in email, early-access status, and sign out. Below that, the
 * opt-in cloud backup (account-cloud-sync.md): each computer keeps one
 * snapshot online, and restoring from another computer is always an
 * explicit choice. Sessions and snapshot contents never reach the
 * renderer — display state only.
 */
export default function AccountTab(): React.JSX.Element {
  const [state, setState] = useState<AccountState>({
    signedIn: false,
    email: null,
    expiresAt: null,
    access: 'unknown'
  })
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([])
  const [busy, setBusy] = useState(false)
  const [syncBusy, setSyncBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (typeof window.account?.getState !== 'function') return
    void window.account.getState().then(setState).catch(() => undefined)
    void window.account.syncStatus().then(setSync).catch(() => undefined)
  }, [])

  const refreshSnapshots = (): void => {
    if (typeof window.account?.syncList !== 'function') return
    void window.account
      .syncList()
      .then(setSnapshots)
      .catch(() => setSnapshots([]))
  }

  useEffect(() => {
    if (state.signedIn && sync?.enabled) refreshSnapshots()
  }, [state.signedIn, sync?.enabled])

  const handleSignIn = (): void => {
    if (typeof window.account?.startSignIn !== 'function') return
    setBusy(true)
    setError('')
    void window.account
      .startSignIn()
      .then((next) => {
        setState(next)
        return window.account.syncStatus().then(setSync)
      })
      .catch((signInError) =>
        setError(
          signInError instanceof Error
            ? signInError.message
            : "Sign-in didn't finish. Please try again."
        )
      )
      .finally(() => setBusy(false))
  }

  const handleSignOut = (): void => {
    if (typeof window.account?.signOut !== 'function') return
    setBusy(true)
    setError('')
    void window.account
      .signOut()
      .then((next) => {
        setState(next)
        return window.account.syncStatus().then(setSync)
      })
      .catch((signOutError) =>
        setError(
          signOutError instanceof Error
            ? signOutError.message
            : 'Could not sign out right now.'
        )
      )
      .finally(() => setBusy(false))
  }

  const accessCopy = ACCESS_COPY[state.access]

  const handleToggleSync = (): void => {
    if (!sync) return
    const turningOn = !sync.enabled
    if (
      turningOn &&
      !window.confirm(
        'Back up your memories online?\n\n' +
          'Collie saves a copy of what she remembers — facts, people, ' +
          'important dates, About Me, and her personality — to your account, ' +
          'so another computer with Collie can pick them up.\n\n' +
          'Chats, files, and API keys are never uploaded.'
      )
    ) {
      return
    }
    setSyncBusy(true)
    setError('')
    setNotice('')
    void window.account
      .syncEnable(turningOn)
      .then((next) => {
        setSync(next)
        setNotice(
          turningOn
            ? 'Backup is on — this computer’s copy is saved to your account.'
            : 'Backup is off. Nothing else will be uploaded.'
        )
        if (turningOn) refreshSnapshots()
      })
      .catch((syncError) =>
        setError(syncError instanceof Error ? syncError.message : 'Could not change that.')
      )
      .finally(() => setSyncBusy(false))
  }

  const handleBackupNow = (): void => {
    if (typeof window.account?.syncUpload !== 'function') return
    setSyncBusy(true)
    setError('')
    setNotice('')
    void window.account
      .syncUpload()
      .then(() => {
        setNotice('Saved. This computer’s copy is up to date.')
        refreshSnapshots()
      })
      .catch((uploadError) =>
        setError(uploadError instanceof Error ? uploadError.message : 'Backup didn’t go through.')
      )
      .finally(() => setSyncBusy(false))
  }

  const handleRestore = (snapshot: SnapshotSummary): void => {
    if (
      !window.confirm(
        `Bring ${snapshot.deviceName}'s copy to this computer?\n\n` +
          'This replaces what Collie remembers here — facts, people, dates, ' +
          'About Me, and personality. You can undo file changes afterwards, ' +
          'and nothing on your other computer is touched.'
      )
    ) {
      return
    }
    setSyncBusy(true)
    setError('')
    setNotice('')
    void window.account
      .syncRestore(snapshot.deviceId)
      .then(() => setNotice(`Done — this Collie now matches ${snapshot.deviceName}.`))
      .catch((restoreError) =>
        setError(
          restoreError instanceof Error ? restoreError.message : 'Restore didn’t finish.'
        )
      )
      .finally(() => setSyncBusy(false))
  }

  return (
    <section className="settings-card">
      <h3>
        <LogIn size={16} /> Collie account
      </h3>
      {state.signedIn ? (
        <>
          <p className="settings-lead">
            Signed in as <strong>{state.email}</strong>. Your chats and files
            stay on this computer — the account is just your identity.
          </p>
          {accessCopy && (
            <p className="account-access-line">
              <strong>{accessCopy.title}.</strong> {accessCopy.detail}
            </p>
          )}


          <div style={{ marginTop: 12 }}>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={Boolean(sync?.enabled)}
                disabled={syncBusy || !sync?.configured}
                onChange={handleToggleSync}
              />
              Back up my memories online
            </label>
            <p className="settings-lead" style={{ fontSize: '0.85em' }}>
              Saves what Collie remembers — facts, people, important dates,
              About Me, and personality — to your account. Chats, files, and
              keys are never uploaded, and you can turn this off anytime.
            </p>
          </div>

          {sync?.enabled && (
            <div style={{ marginTop: 10 }}>
              <button
                type="button"
                className="settings-button"
                onClick={handleBackupNow}
                disabled={syncBusy}
              >
                <CloudUpload size={14} /> {syncBusy ? 'Working…' : 'Back up now'}
              </button>

              {snapshots.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div
                    className="mb-2 text-xs font-medium uppercase tracking-wide"
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                  >
                    <History size={12} /> Copies saved by your computers
                  </div>
                  {snapshots.map((snapshot) => (
                    <div
                      key={snapshot.deviceId}
                      className="flex items-center justify-between gap-2 py-1"
                    >
                      <span style={{ fontSize: '0.9em' }}>
                        <strong>{snapshot.isThisDevice ? 'This computer' : snapshot.deviceName}</strong>
                        {snapshot.createdAt && (
                          <span style={{ opacity: 0.7 }}> · {formatWhen(snapshot.createdAt)}</span>
                        )}
                      </span>
                      {!snapshot.isThisDevice && (
                        <button
                          type="button"
                          className="rounded-lg border px-3 py-1.5 text-xs font-medium"
                          style={{ borderColor: 'var(--collie-border)' }}
                          disabled={syncBusy}
                          onClick={() => handleRestore(snapshot)}
                        >
                          <RotateCcw size={11} /> Bring here
                        </button>
                      )}
                    </div>
                  ))}
                  <p className="settings-lead" style={{ fontSize: '0.8em', opacity: 0.7 }}>
                    Restoring never changes your other computers.
                  </p>
                </div>
              )}
            </div>
          )}

          <button
            type="button"
            className="settings-button"
            style={{ marginTop: 12 }}
            onClick={handleSignOut}
            disabled={busy}
          >
            <LogOut size={14} /> Sign out
          </button>
        </>
      ) : (
        <>
          <p className="settings-lead">
            One click with your email — a browser window opens, you tap the
            link, and you're back. No password to remember. Your chats stay on
            this computer. Don't have an account yet? This creates one and
            holds your spot on the early-access list.
          </p>
          <button
            type="button"
            className="settings-button is-primary"
            onClick={handleSignIn}
            disabled={busy}
          >
            <LogIn size={14} /> {busy ? 'Opening your browser…' : 'Continue with email'}
          </button>
        </>
      )}
      <p className="settings-lead">
        To count active installs, Collie sends a random install ID, app version,
        and operating system at launch and every four minutes while running,
        even when signed out or backup is off. This contains no chats, files,
        email address, or API keys.
      </p>
      {(error || notice) && (
        <p className="inline-notice" role="status">
          {error || notice}
        </p>
      )}
    </section>
  )
}

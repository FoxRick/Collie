import { LogIn, LogOut } from 'lucide-react'
import { useEffect, useState } from 'react'

/** Display-only account state, typed from the preload bridge (`window.account`). */
type AccountState = Awaited<ReturnType<typeof window.account.getState>>

/**
 * Collie account card (account-system-spec.md §6): sign in via the system
 * browser (PKCE + localhost callback handled in the main process), show the
 * signed-in email, and sign out. The session itself never reaches the
 * renderer — only the display state does.
 */
export default function AccountTab(): React.JSX.Element {
  const [state, setState] = useState<AccountState>({
    signedIn: false,
    email: null,
    expiresAt: null
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (typeof window.account?.getState !== 'function') return
    void window.account.getState().then(setState).catch(() => undefined)
  }, [])

  const handleSignIn = (): void => {
    if (typeof window.account?.startSignIn !== 'function') return
    setBusy(true)
    setError('')
    void window.account
      .startSignIn()
      .then(setState)
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
      .then(setState)
      .catch((signOutError) =>
        setError(
          signOutError instanceof Error
            ? signOutError.message
            : 'Could not sign out right now.'
        )
      )
      .finally(() => setBusy(false))
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
          <button
            type="button"
            className="settings-button"
            onClick={handleSignOut}
            disabled={busy}
          >
            <LogOut size={14} /> Sign out
          </button>
        </>
      ) : (
        <>
          <p className="settings-lead">
            Sign in with your Collie account — a browser window opens and brings
            you right back. No data leaves this computer.
          </p>
          <button
            type="button"
            className="settings-button is-primary"
            onClick={handleSignIn}
            disabled={busy}
          >
            <LogIn size={14} /> {busy ? 'Opening your browser…' : 'Sign in'}
          </button>
        </>
      )}
      {error && (
        <p className="inline-notice" role="status">
          {error}
        </p>
      )}
    </section>
  )
}

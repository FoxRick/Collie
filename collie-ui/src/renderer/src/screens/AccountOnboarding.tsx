import { useEffect, useRef, useState, type ReactNode } from 'react'
import CollieFace from '../components/CollieFace'

const CHOICE_KEY = 'collie.account-onboarding.v1'

function hasChosen(): boolean {
  try {
    return localStorage.getItem(CHOICE_KEY) === 'done'
  } catch {
    return false
  }
}

/** Optional identity step. Local data and cloud-backup preferences are untouched. */
export default function AccountOnboarding({ children }: { children: ReactNode }): React.JSX.Element {
  const [done, setDone] = useState(hasChosen)
  const [checking, setChecking] = useState(!done)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(false)
  const continued = useRef(done)

  function continueSetup(): void {
    if (!mounted.current || continued.current) return
    continued.current = true
    try {
      localStorage.setItem(CHOICE_KEY, 'done')
    } catch {
      // Storage restrictions must never prevent guest use.
    }
    setDone(true)
  }

  useEffect(() => {
    mounted.current = true
    let cancelled = false
    if (!continued.current) {
      void (async () => {
        try {
          const state = await window.account?.getState()
          if (!cancelled && state?.signedIn) continueSetup()
        } catch {
          // An unavailable account service must not block local setup.
        } finally {
          if (!cancelled) setChecking(false)
        }
      })()
    }
    return () => { cancelled = true; mounted.current = false }
  }, [])

  async function signIn(): Promise<void> {
    setBusy(true)
    setError('')
    try {
      const state = await window.account?.startSignIn()
      if (!mounted.current || continued.current) return
      if (state?.signedIn) continueSetup()
      else setError("Sign-in didn't finish. Try again or continue as a guest.")
    } catch {
      if (mounted.current && !continued.current) {
        setError("Sign-in didn't finish. Try again or continue as a guest.")
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  if (done) return <>{children}</>

  return (
    <div className="onboarding-shell flex h-full flex-col overflow-y-auto">
      <div className="collie-header h-14 shrink-0" />
      <main className="onboarding-panel mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-4 px-6 py-10">
        <div className="onboarding-hero mb-2 text-center">
          <div className="flex justify-center"><CollieFace size={80} /></div>
          <h1 className="mt-3 text-2xl font-semibold">Make yourself at home</h1>
          <p className="mt-2" style={{ color: 'var(--collie-text-muted)' }}>
            Sign in with the Collie account you use on our website, or create one in your browser.
          </p>
        </div>
        <button
          type="button"
          className="rounded-xl px-5 py-3 font-medium text-white disabled:opacity-50"
          style={{ background: 'var(--collie-btn-primary-bg)' }}
          disabled={checking || busy}
          onClick={() => void signIn()}
        >
          {checking ? 'Checking your account…' : busy ? 'Finish signing in in your browser…' : 'Sign in or create an account'}
        </button>
        <button
          type="button"
          className="rounded-xl border px-5 py-3 font-medium"
          style={{ borderColor: 'var(--collie-border)', background: 'var(--collie-surface)' }}
          onClick={continueSetup}
        >
          Continue as a guest
        </button>
        {error && <p role="alert" className="text-center text-sm">{error}</p>}
        {busy && <p role="status" className="text-center text-sm">You can continue as a guest while you wait.</p>}
        <p className="text-center text-sm" style={{ color: 'var(--collie-text-muted)' }}>
          An account is optional. Your chats and settings are saved on this computer.
          You can sign in later in Settings → Account. Cloud backup is a separate, optional choice.
        </p>
      </main>
    </div>
  )
}

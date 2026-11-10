import { useEffect, useRef, useState } from 'react'
import { collieClient } from './lib/ipc'
import { initI18n, useT } from './lib/i18n'
import { initTheme } from './lib/theme'
import WelcomeScreen from './screens/WelcomeScreen'
import AccountOnboarding from './screens/AccountOnboarding'
import ChatScreen from './screens/ChatScreen'
import CollieFace from './components/CollieFace'
import type { AppView } from './lib/navigation'
import { BootProbeController, type BootScreen } from './lib/boot-probe'

export type Screen = BootScreen

const GUEST_KEY = 'collie.guest-onboarding.v1'

function hasGuestChoice(): boolean {
  try {
    return localStorage.getItem(GUEST_KEY) === 'done'
  } catch {
    return false
  }
}

export default function App(): React.JSX.Element {
  const [screen, setScreen] = useState<Screen>('loading')
  const [view, setView] = useState<AppView>('chat')
  const [replayingOnboarding, setReplayingOnboarding] = useState(false)
  const [autoOpenStarter, setAutoOpenStarter] = useState(false)
  const [offlineMessage, setOfflineMessage] = useState('')
  const guest = useRef(hasGuestChoice())
  const t = useT()

  function continueAsGuest(): void {
    guest.current = true
    try {
      localStorage.setItem(GUEST_KEY, 'done')
    } catch {
      // The guest choice still applies for this session.
    }
    setScreen('app')
  }

  useEffect(() => {
    initI18n()
    return initTheme()
  }, [])

  useEffect(() => {
    let cancelled = false

    const injectSecrets = async (): Promise<number> => {
      // Stored secrets are pushed to the core by the main process over its
      // own connection (core-client.ts). The renderer only learns how many
      // exist for boot decisions — never the values.
      return (await window.collie?.storedSecretCount()) ?? 0
    }

    const probeController = new BootProbeController({
      getStatus: () => collieClient.getStatus(10_000),
      injectSecrets,
      configure: () => collieClient.configure(),
      wakeMessengers: () => collieClient.setMessenger('', {}),
      isConnected: () => collieClient.connected,
      applyState: (next) => {
        if (next.offlineMessage) setOfflineMessage(next.offlineMessage)
        setScreen(next.screen === 'welcome' && guest.current ? 'app' : next.screen)
      }
    })

    // A ready event advances the socket/core generation. Rapid events request
    // a rerun on the one active probe instead of starting concurrent work.
    const offReady = collieClient.on((event) => {
      if (event.type === 'ready' && !cancelled) {
        void probeController.requestProbe(true)
      }
    })

    const boot = async (): Promise<void> => {
      // The core binds its own port; learn it from the main process. The
      // per-boot token is handled entirely in main (core-client.ts broker) —
      // it is never handed to the renderer.
      try {
        const core = await window.collie?.coreState()
        if (core) collieClient.applyEndpoint(core.port)
      } catch {
        // main process unavailable — keep the default endpoint
      }
      if (cancelled) return
      collieClient.connect()
      // The UI-verification preview flag lives in sessionStorage, NOT the URL:
      // renderer security treats any query-string URL as untrusted (see
      // renderer-security.test.ts), so a ?preview=1 URL would break IPC auth.
      // sessionStorage is renderer-side state that survives navigation and
      // keeps the trusted URL untouched.
      if (sessionStorage.getItem('collie.ui-ux-preview') === '1') {
        setScreen('app')
        return
      }
      void probeController.requestProbe()
    }

    void boot()
    return () => {
      cancelled = true
      probeController.dispose()
      offReady()
    }
  }, [])

  useEffect(() => {
    // Automations fire in the core; surface them as OS notifications so
    // briefings reach the user even when Collie is in the background.
    const off = collieClient.on((event) => {
      if (event.type !== 'automation') return
      try {
        if (Notification.permission === 'granted' || Notification.permission === 'default') {
          const body = (event.content || '').slice(0, 180)
          new Notification(`🔔 ${event.name}`, { body, silent: false })
        }
      } catch {
        // notifications unavailable — the message still lands in the chat
      }
    })
    return off
  }, [])

  if (screen === 'loading') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4">
        <CollieFace size={80} />
        <div className="text-lg font-medium">{t('app.loading')}</div>
        <div className="collie-thinking text-sm" style={{ color: 'var(--collie-text-muted)' }}>
          {t('app.loadingSub')}
        </div>
      </div>
    )
  }

  if (screen === 'offline') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6">
        <CollieFace size={80} />
        <div className="text-lg font-medium">Collie can't connect</div>
        <div className="max-w-sm text-center text-sm" style={{ color: 'var(--collie-text-muted)' }}>
          {offlineMessage || "Collie's engine didn't start. Restart the app and give it another try."}
        </div>
        <button
          onClick={() => {
            setScreen(guest.current ? 'app' : 'welcome')
            collieClient.connect()
          }}
          className="rounded-lg px-4 py-2 font-medium text-white transition"
          style={{ background: 'var(--collie-btn-primary-bg)' }}
        >
          Try again
        </button>
      </div>
    )
  }

  if (screen === 'welcome' || replayingOnboarding) {
    const welcome = (
      <WelcomeScreen
        onDone={() => {
          setScreen('app')
          setReplayingOnboarding(false)
          // Straight to chat: ChatScreen opens the starter conversation.
          setAutoOpenStarter(true)
        }}
        onCancel={replayingOnboarding ? () => setReplayingOnboarding(false) : undefined}
        onSkip={replayingOnboarding ? undefined : continueAsGuest}
      />
    )
    return replayingOnboarding ? welcome : (
      <AccountOnboarding onGuest={continueAsGuest}>{welcome}</AccountOnboarding>
    )
  }

  return (
    <ChatScreen
      activeView={view}
      onNavigate={setView}
      autoOpenStarter={autoOpenStarter}
      onRedoOnboarding={() => {
        setReplayingOnboarding(true)
      }}
    />
  )
}

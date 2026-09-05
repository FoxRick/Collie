import { useEffect, useState } from 'react'
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

export default function App(): React.JSX.Element {
  const [screen, setScreen] = useState<Screen>('loading')
  const [view, setView] = useState<AppView>('chat')
  const [replayingOnboarding, setReplayingOnboarding] = useState(false)
  const [autoOpenStarter, setAutoOpenStarter] = useState(false)
  const [offlineMessage, setOfflineMessage] = useState('')
  const t = useT()

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
        setScreen(next.screen)
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
      // The core binds its own port and issues a per-boot token; learn both
      // from the main process before speaking to the socket.
      try {
        const core = await window.collie?.coreState()
        if (core) collieClient.applyEndpoint(core.port, core.token)
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
            setScreen('welcome')
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

  if (screen === 'welcome') {
    const welcome = (
      <WelcomeScreen
        onDone={() => {
          setScreen('app')
          setReplayingOnboarding(false)
          // Straight to chat: ChatScreen opens the starter conversation.
          setAutoOpenStarter(true)
        }}
        onCancel={replayingOnboarding ? () => {
          setScreen('app')
          setReplayingOnboarding(false)
        } : undefined}
      />
    )
    return replayingOnboarding ? welcome : <AccountOnboarding>{welcome}</AccountOnboarding>
  }

  return (
    <ChatScreen
      activeView={view}
      onNavigate={setView}
      autoOpenStarter={autoOpenStarter}
      onRedoOnboarding={() => {
        setReplayingOnboarding(true)
        setScreen('welcome')
      }}
    />
  )
}

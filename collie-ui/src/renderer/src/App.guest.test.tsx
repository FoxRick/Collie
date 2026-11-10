// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CollieEvent } from './lib/ipc'
import App from './App'

const bridge = vi.hoisted(() => {
  const listeners = new Set<(event: CollieEvent) => void>()
  return {
    listeners,
    client: {
      applyEndpoint: vi.fn(),
      connect: vi.fn(),
      connected: true,
      getStatus: vi.fn(),
      configure: vi.fn(),
      setMessenger: vi.fn(async () => undefined),
      on: (listener: (event: CollieEvent) => void) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
      listConversations: async () => ({ conversations: [] }),
      listCommands: async () => ({ commands: [], agents: [], skills: [] }),
      listPendingApprovals: async () => ({ approvals: [] }),
      getSettings: async () => ({ settings: {} }),
      detectLocalModels: async () => ({ available: false, models: [] }),
      getProviderCatalogue: async () => ({ providers: [] }),
      authStatus: async () => ({ signed_in: false }),
      chat: vi.fn()
    },
    getAccount: vi.fn(),
    startSignIn: vi.fn(),
    secretCount: vi.fn()
  }
})

vi.mock('./lib/ipc', () => ({ collieClient: bridge.client }))
vi.mock('./lib/theme', async (importOriginal) => ({
  ...await importOriginal<typeof import('./lib/theme')>(),
  initTheme: () => undefined
}))
vi.mock('./components/CollieFace', () => ({ default: () => null }))
vi.mock('./components/InteractiveColliePortrait', () => ({ default: () => null }))
vi.mock('./components/MessageList', () => ({ default: () => null }))
vi.mock('./components/ChatInput', () => ({
  default: ({ onSend }: { onSend: (message: string, attachments: []) => Promise<boolean> }) => (
    <button type="button" onClick={() => void onSend('Hello', [])}>Send test message</button>
  )
}))

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  bridge.client.getStatus.mockResolvedValue({ configured: false })
  bridge.getAccount.mockResolvedValue({ signedIn: false })
  bridge.secretCount.mockResolvedValue(0)
  vi.stubGlobal('account', { getState: bridge.getAccount, startSignIn: bridge.startSignIn })
  vi.stubGlobal('collie', {
    storedSecretCount: bridge.secretCount,
    petCommand: vi.fn(),
    updateActiveWork: vi.fn(),
    setDesktopMenu: vi.fn().mockResolvedValue(undefined)
  })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function launch(): Promise<void> {
  await act(async () => root.render(<App />))
}

async function click(label: string): Promise<void> {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === label)
  expect(button, label).toBeDefined()
  await act(async () => button!.click())
}

async function coreReady(): Promise<void> {
  await act(async () => {
    for (const listener of bridge.listeners) listener({ type: 'ready', protocol: 1, phrase: '' })
  })
}

describe('desktop guest onboarding', () => {
  it('opens local chat without credentials and survives core reconnect and relaunch', async () => {
    await launch()
    expect(container.textContent).toContain('Make yourself at home')
    await click('Continue as a guest')
    expect(container.textContent).toContain('New conversation')
    expect(container.textContent).toContain('Connect a model to get AI replies.')
    expect(container.textContent).not.toContain('Welcome to Collie')
    await coreReady()
    expect(container.textContent).toContain('New conversation')

    await act(async () => root.unmount())
    root = createRoot(container)
    await launch()
    expect(container.textContent).toContain('New conversation')
    expect(container.textContent).not.toContain('Make yourself at home')
    expect(bridge.startSignIn).not.toHaveBeenCalled()
    expect(bridge.client.configure).not.toHaveBeenCalled()
  })

  it('explains unavailable AI replies and opens model settings only when requested', async () => {
    await launch()
    await click('Continue as a guest')
    await click('Send test message')
    expect(bridge.client.chat).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Connect a model to get AI replies.')
    expect(container.textContent).not.toContain('Welcome to Collie')
    expect(bridge.startSignIn).not.toHaveBeenCalled()

    await click('Open model settings')
    expect(container.querySelector('h1')?.textContent).toContain('Settings')
    expect(container.querySelector('h2')?.textContent).toBe('Models & API keys')
    expect(container.textContent).not.toContain('Make yourself at home')
  })

  it('lets people resume model setup without an account or leave it for later', async () => {
    localStorage.setItem('collie.account-onboarding.v1', 'done')
    await launch()
    expect(container.textContent).toContain('Welcome to Collie')
    expect(bridge.getAccount).not.toHaveBeenCalled()
    await click('Set up a model later')
    expect(container.textContent).toContain('New conversation')
  })

  it('opens existing configured profiles directly', async () => {
    bridge.client.getStatus.mockResolvedValue({ configured: true, model: 'existing-model' })
    bridge.secretCount.mockResolvedValue(1)
    await launch()
    expect(container.textContent).toContain('New conversation')
    expect(container.textContent).not.toContain('Connect a model to get AI replies.')
    expect(container.textContent).not.toContain('Make yourself at home')
    expect(bridge.startSignIn).not.toHaveBeenCalled()
    expect(bridge.client.setMessenger).toHaveBeenCalledWith('', {})
  })
})

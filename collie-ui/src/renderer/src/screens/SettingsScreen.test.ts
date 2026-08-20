// @vitest-environment jsdom
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import SettingsScreen, { clearAllDataNotice } from './SettingsScreen'

const hooks = vi.hoisted(() => {
  const client = {
    on: vi.fn(() => () => undefined),
    getStatus: vi.fn(async () => ({})),
    getSettings: vi.fn(async () => ({ settings: {} })),
    authStatus: vi.fn(async () => ({ signed_in: false }))
  }
  return { client }
})

vi.mock('lucide-react', () => ({
  ArrowLeft: () => null,
  Brain: () => null,
  CircleUserRound: () => null,
  Cloud: () => null,
  Dog: () => null,
  KeyRound: () => null,
  Mic2: () => null,
  Palette: () => null,
  RefreshCw: () => null,
  RotateCcw: () => null,
  Send: () => null,
  ShieldCheck: () => null,
  UserRound: () => null
}))
vi.mock('../components/settings/ProviderManager', () => ({ default: () => null }))
vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))
vi.mock('../components/CollieFace', () => ({ default: () => null }))

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('clear-all result reporting', () => {
  it('reports a complete clear only when every phase succeeded', () => {
    expect(clearAllDataNotice({
      cleared: true,
      partial: false,
      database_cleared: true,
      filesystem_cleared: true,
      warnings: []
    })).toContain('All clear')
  })

  it('reports filesystem warnings as a partial clear', () => {
    const notice = clearAllDataNotice({
      cleared: false,
      partial: true,
      database_cleared: true,
      filesystem_cleared: false,
      warnings: [
        {
          scope: 'filesystem',
          target: 'pairing.json',
          error: 'PermissionError: file is locked'
        }
      ]
    })

    expect(notice).toContain("cleared Collie's saved records")
    expect(notice).toContain("couldn't remove 1 local file or folder")
    expect(notice).not.toContain('All clear')
  })

  it('explains that files were preserved when the database clear failed', () => {
    const notice = clearAllDataNotice({
      cleared: false,
      partial: false,
      database_cleared: false,
      filesystem_cleared: false,
      warnings: [
        {
          scope: 'database',
          target: 'collie.db',
          error: 'RuntimeError: database is busy'
        }
      ]
    })

    expect(notice).toContain("couldn't clear Collie's database")
    expect(notice).toContain('left local files in place')
    expect(notice).not.toContain('All clear')
  })
})

describe('SettingsScreen back navigation', () => {
  it('renders a Back to chat button that navigates to chat', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const onNavigate = vi.fn()
    await act(async () => {
      root.render(createElement(SettingsScreen, { initialTab: 'onboarding', onNavigate }))
    })
    const button = container.querySelector<HTMLButtonElement>('.settings-back')
    expect(button).not.toBeNull()
    expect(button?.textContent).toContain('Back to chat')
    await act(async () => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onNavigate).toHaveBeenCalledWith('chat')
    act(() => root.unmount())
  })
})

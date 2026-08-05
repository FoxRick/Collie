// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import WelcomeScreen from './WelcomeScreen'
import type { CatalogueProvider } from '../lib/ipc'

const hooks = vi.hoisted(() => {
  const client = {
    connected: true,
    on: vi.fn(() => () => undefined),
    getProviderCatalogue: vi.fn(),
    detectLocalModels: vi.fn(),
    detectProviderForKey: vi.fn(),
    detectModels: vi.fn(),
    configure: vi.fn(),
    oauthLogin: vi.fn(),
    cancelOAuthLogin: vi.fn()
  }
  const configureApiKeyProvider = vi.fn()
  return { client, configureApiKeyProvider }
})

vi.mock('lucide-react', () => ({
  ArrowLeft: () => null,
  ChevronDown: () => null,
  ChevronUp: () => null,
  Search: () => null,
  Sparkles: () => null
}))
vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))
vi.mock('../lib/providerConfiguration', () => ({
  configureApiKeyProvider: hooks.configureApiKeyProvider
}))
vi.mock('../components/BrandLogo', () => ({ default: () => <span /> }))
vi.mock('../components/CollieFace', () => ({ default: () => <span /> }))

const providers: CatalogueProvider[] = [
  {
    id: 'openai',
    name: 'OpenAI',
    auth_type: 'api-key',
    protocol: 'openai',
    api_base: 'https://api.openai.com/v1',
    default_model: 'gpt-5.5',
    key_prefixes: ['sk-proj-', 'sk-'],
    tested: true,
    models: [{ id: 'gpt-5.5', name: 'GPT-5.5' }]
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    auth_type: 'api-key',
    protocol: 'openai',
    api_base: 'https://api.deepseek.com',
    default_model: 'deepseek-v4-flash',
    key_prefixes: ['sk-'],
    tested: true,
    models: [{ id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' }]
  },
  {
    id: 'groq',
    name: 'Groq',
    auth_type: 'api-key',
    protocol: 'openai',
    api_base: 'https://api.groq.com/openai/v1',
    default_model: 'llama-3.3-70b-versatile',
    key_prefixes: ['gsk_'],
    tested: true,
    models: [{ id: 'llama-3.3-70b-versatile', name: 'Llama 3.3 70B' }]
  }
]

const roots: Root[] = []

function renderWelcome(onDone = vi.fn()): { container: HTMLElement; onDone: ReturnType<typeof vi.fn> } {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => {
    root.render(<WelcomeScreen onDone={onDone} />)
  })
  return { container, onDone }
}

function typeInto(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function expandApiKeyForm(container: HTMLElement): Promise<void> {
  const card = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
    (button) => button.textContent?.includes('I have an API key')
  )!
  act(() => card.click())
  // Let the mocked catalogue promise resolve so the picker shows real names.
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

beforeEach(() => {
  hooks.client.getProviderCatalogue.mockReset().mockResolvedValue({ providers, snapshot: {}, refresh: {} })
  hooks.client.detectLocalModels.mockReset().mockResolvedValue({ available: false, models: [] })
  hooks.client.detectProviderForKey.mockReset().mockResolvedValue({ detected: false, provider_id: null })
  hooks.configureApiKeyProvider.mockReset()
  Object.defineProperty(window, 'collie', {
    configurable: true,
    value: { openExternal: vi.fn() }
  })
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('WelcomeScreen first-run choices', () => {
  it('shows the three choice cards, the help card, and the corrected footer', () => {
    const { container } = renderWelcome()
    const text = container.textContent!
    expect(text).toContain('I have a ChatGPT subscription')
    expect(text).toContain('I have a Claude subscription')
    expect(text).toContain('I have an API key')
    expect(text).toContain("I don't have anything yet")
    // The footer now points at the real tab (review P1-5).
    expect(text).toContain('Settings → Models & API keys')
  })

  it('points the "nothing yet" help at heycollie.com/get-started', () => {
    const { container } = renderWelcome()
    const card = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes("I don't have anything yet")
    )!
    act(() => card.click())
    expect(window.collie.openExternal).toHaveBeenCalledWith('https://heycollie.com/get-started')
  })
})

describe('WelcomeScreen API-key form', () => {
  it('collapses connection name / model / base URL behind Advanced', async () => {
    const { container } = renderWelcome()
    await expandApiKeyForm(container)

    const keyField = container.querySelector<HTMLInputElement>('input[placeholder="Paste your API key"]')
    expect(keyField).not.toBeNull()
    const connectButton = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Connect'
    )
    expect(connectButton).not.toBeNull()
    // Normie fields only: no jargon up front.
    expect(container.querySelector('input[placeholder="Connection name (optional)"]')).toBeNull()
    expect(container.querySelector('input[placeholder="Custom base URL (optional)"]')).toBeNull()

    const advanced = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Advanced'
    )!
    act(() => advanced.click())
    expect(container.querySelector('input[placeholder="Connection name (optional)"]')).not.toBeNull()
    expect(container.querySelector('input[placeholder="Custom base URL (optional)"]')).not.toBeNull()
  })

  it('offers a searchable provider picker and switches provider on selection', async () => {
    const { container } = renderWelcome()
    await expandApiKeyForm(container)

    const picker = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'OpenAI'
    )!
    act(() => picker.click())
    const search = container.querySelector<HTMLInputElement>('input[placeholder="Search providers…"]')
    expect(search).not.toBeNull()

    typeInto(search!, 'deepseek')
    const dropdown = container.querySelector<HTMLElement>('[class*="max-h-56"]')!
    const rows = Array.from(dropdown.querySelectorAll<HTMLButtonElement>('button'))
    // The search filters the catalogue; Custom stays pinned as the escape hatch.
    expect(rows.map((row) => row.textContent)).toEqual(['DeepSeek', 'Custom (OpenAI-compatible)'])
    act(() => rows[0].click())
    expect(container.textContent).toContain('DeepSeek')
  })

  it('auto-selects the provider from an unambiguous key prefix on paste', async () => {
    const { container } = renderWelcome()
    await expandApiKeyForm(container)
    const keyField = container.querySelector<HTMLInputElement>('input[placeholder="Paste your API key"]')!
    typeInto(keyField, 'gsk_abc123')
    await act(async () => {
      await Promise.resolve()
    })
    expect(hooks.client.detectProviderForKey).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Groq')
  })

  it('shows "Connected — using …" on a validated connect and finishes', async () => {
    vi.useFakeTimers()
    hooks.configureApiKeyProvider.mockResolvedValue({
      configured: true,
      transaction_id: 'tx-1',
      validated: true,
      model_label: 'DeepSeek V4 Flash'
    })
    const { container, onDone } = renderWelcome()
    await expandApiKeyForm(container)
    const keyField = container.querySelector<HTMLInputElement>('input[placeholder="Paste your API key"]')!
    typeInto(keyField, 'sk-test-123')
    const connectButton = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Connect'
    )!
    act(() => connectButton.click())
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('Connected — using DeepSeek V4 Flash')
    expect(onDone).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_200)
    })
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('shows the warm bad-key copy (not a network error) when the probe rejects', async () => {
    hooks.configureApiKeyProvider.mockRejectedValue(
      new Error(
        "That key didn't work. Double-check it, or get help here → https://heycollie.com/get-started"
      )
    )
    const { container } = renderWelcome()
    await expandApiKeyForm(container)
    const keyField = container.querySelector<HTMLInputElement>('input[placeholder="Paste your API key"]')!
    typeInto(keyField, 'sk-wrong')
    const connectButton = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Connect'
    )!
    act(() => connectButton.click())
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(container.textContent).toContain("That key didn't work")
    expect(container.textContent).not.toContain('could not reach that provider')
    const helpLink = container.querySelector<HTMLAnchorElement>('a[href="https://heycollie.com/get-started"]')
    expect(helpLink).not.toBeNull()
  })
})

describe('WelcomeScreen local-model card', () => {
  it('hides the local-model card when no Ollama install is detected', async () => {
    hooks.client.detectLocalModels.mockResolvedValue({ available: false, models: [] })
    const { container } = renderWelcome()
    await act(async () => {
      await Promise.resolve()
    })
    expect(container.textContent).not.toContain('Use a model on this computer')
  })

  it('offers detected local models as a fourth choice', async () => {
    hooks.client.detectLocalModels.mockResolvedValue({ available: true, models: ['llama3.2'] })
    const { container } = renderWelcome()
    await act(async () => {
      await Promise.resolve()
    })
    expect(container.textContent).toContain('Use a model on this computer')
    expect(container.querySelector<HTMLSelectElement>('select')?.textContent).toContain('llama3.2')
  })
})

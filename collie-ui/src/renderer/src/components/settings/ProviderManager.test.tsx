// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProviderInfo } from '../../lib/ipc'
import ProviderManager from './ProviderManager'

const configureApiKeyProvider = vi.hoisted(() => vi.fn())

vi.mock('../../lib/ipc', () => ({
  collieClient: {
    getProviderCatalogue: vi.fn().mockResolvedValue({ providers: [] })
  }
}))
vi.mock('../../lib/providerConfiguration', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/providerConfiguration')>()),
  configureApiKeyProvider
}))
vi.mock('../ManagedInferenceCard', () => ({ default: () => null }))

let root: Root
let container: HTMLDivElement

async function renderManager(providers: ProviderInfo[] = []): Promise<void> {
  await act(async () => {
    root.render(
      <ProviderManager
        status={{ providers }}
        settings={{}}
        oauth={{ chatgpt: false }}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onNotice={vi.fn()}
      />
    )
  })
}

function button(label: string): HTMLButtonElement {
  return [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === label)!
}

function input(label: string): HTMLInputElement {
  return container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!
}

function fill(label: string, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    const field = input(label)
    setter.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  Object.defineProperty(window, 'collie', {
    configurable: true,
    value: { listSecrets: vi.fn().mockResolvedValue(['local qa model', 'legacy key']) }
  })
  configureApiKeyProvider.mockReset().mockResolvedValue({ configured: true })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

describe('ProviderManager custom setup', () => {
  it('shows the required custom fields and accepts a keyless loopback model', async () => {
    await renderManager()
    act(() => button('Add AI connection').click())
    const provider = container.querySelector<HTMLSelectElement>('select[aria-label="API provider"]')!
    act(() => {
      provider.value = 'custom'
      provider.dispatchEvent(new Event('change', { bubbles: true }))
    })

    for (const label of ['Connection name', 'Model ID', 'API base URL']) {
      expect(input(label).closest('details')).toBeNull()
      expect(input(label).required).toBe(true)
    }
    expect(button('Connect').disabled).toBe(true)
    fill('Connection name', 'Local QA model')
    fill('API base URL', 'http://127.0.0.1:19399/v1')
    expect(button('Connect').disabled).toBe(true)
    fill('Model ID', 'collie-qwen')
    expect(button('Connect').disabled).toBe(false)
    await act(async () => button('Connect').click())
    expect(configureApiKeyProvider).toHaveBeenCalledWith({
      provider: 'custom',
      displayName: 'Local QA model',
      protocol: 'openai',
      apiKey: '',
      model: 'collie-qwen',
      baseUrl: 'http://127.0.0.1:19399/v1'
    })
  })

  it('only shows a legacy key card when it has no configured provider', async () => {
    await renderManager([{
      id: 'api-Local QA model',
      name: 'Local QA model',
      auth_type: 'api-key',
      is_default: 1,
      model: 'collie-qwen',
      secret_name: 'Local QA model'
    }])
    expect(container.textContent?.match(/local qa model/gi)).toHaveLength(1)
    expect(container.textContent).toContain('Legacy key')
  })
})

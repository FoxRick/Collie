// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import ConnectorDetail from './ConnectorDetail'

beforeAll(() => { ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true })
let root: Root
afterEach(() => { if (root) act(() => root.unmount()); document.body.innerHTML = '' })

function renderDetail(overrides: Record<string, unknown> = {}): void {
  const container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  act(() => root.render(<ConnectorDetail connection={{ id: 'c1', provider_id: 'custom', provider_name: 'Notes', driver: 'custom_mcp', auth_type: 'headers', status: 'auth_required', granted_scopes: [], enabled_capabilities: [], enabled_tools: ['search', 'hidden_enabled'], tool_policy: {}, permissions: [], capabilities: [], route: 'Direct', failure: { code: 'expired', message: 'Expired.', recovery_action: 'Enter a new value.', stage: 'authentication', retryable: true } }} busy={false} onBack={vi.fn()} onRename={vi.fn()} onPreference={vi.fn()} onTest={vi.fn()} onReconnect={vi.fn()} onReconnectWithSecret={vi.fn().mockResolvedValue(undefined)} onInspectTools={vi.fn().mockResolvedValue([{ name: 'search', enabled: true, review_status: 'reviewed' }, { name: 'publish', enabled: false, review_status: 'new' }])} onSaveTools={vi.fn().mockResolvedValue(undefined)} onRemove={vi.fn()} {...overrides} />))
}

function button(text: string): HTMLButtonElement { return [...document.querySelectorAll('button')].find((item) => item.textContent?.includes(text))! }

it('clears replacement credentials before reconnect settles and allows cancel', () => {
  const reconnect = vi.fn(() => new Promise<void>(() => undefined))
  renderDetail({ onReconnectWithSecret: reconnect })
  act(() => button('Reconnect').click())
  const input = document.querySelector<HTMLInputElement>('input[type=password]')!
  act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'replacement-secret'); input.dispatchEvent(new Event('input', { bubbles: true })) })
  act(() => button('Sign in again').click())
  expect(reconnect).toHaveBeenCalledWith('replacement-secret')
  expect(input.value).toBe('')
  expect(document.body.textContent).not.toContain('replacement-secret')
})

it('marks new tools disabled until explicitly selected and saves the selection', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  renderDetail({ onSaveTools: save })
  await act(async () => button('Inspect tools').click())
  const boxes = [...document.querySelectorAll<HTMLInputElement>('input[type=checkbox]')]
  expect(boxes.map((item) => item.checked)).toEqual([true, false])
  expect(document.body.textContent).toContain('New — review first')
  act(() => boxes[1].click())
  act(() => button('Save tool access').click())
  expect(save).toHaveBeenCalledWith(['search', 'hidden_enabled', 'publish'])
  expect(document.body.textContent).toContain('still ask for approval')
})

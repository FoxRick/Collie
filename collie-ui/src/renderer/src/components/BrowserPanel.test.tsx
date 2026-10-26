// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ command: vi.fn() }))
vi.mock('../lib/ipc', () => ({ collieClient: mocks }))
import BrowserPanel from './BrowserPanel'
let root: Root
let host: HTMLDivElement
beforeEach(() => {
  vi.clearAllMocks()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  mocks.command.mockImplementation(async (_kind, frame) => frame.command.operation === 'list'
    ? { sessions: [{ session_id: 's', origin: 'http://localhost', handed_off: false, task_id: null, destinations: [] }] }
    : { session_id: 's', observation_id: 'fresh', elements: [{ ref: 'opaque', tag: 'input', type: 'text', label: 'Draft', role: '' }], text: 'Local page' })
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
async function click(label: string): Promise<void> {
  const button = [...host.querySelectorAll('button')].find(b => b.textContent === label)
  if (!button) throw new Error(`Missing ${label}`)
  await act(async () => button.click())
}
it('uses observed control references and clears content on chat switch', async () => {
  await act(async () => root.render(<BrowserPanel conversationId="a" />))
  await click('Browser · preview'); await click('Open browser')
  const control = host.querySelector('select[aria-label="Page control"]') as HTMLSelectElement
  await act(async () => { control.value = 'opaque'; control.dispatchEvent(new Event('change', { bubbles: true })) })
  await click('Fill field')
  expect(mocks.command).toHaveBeenCalledWith('browser_action', expect.objectContaining({ conversation_id: 'a', command: expect.objectContaining({ ref: 'opaque', observation_id: 'fresh', operation: 'fill' }) }), 360_000)
  expect(host.textContent).toContain('Local page')
  expect(host.innerHTML).not.toContain('CSS selector')
  await act(async () => root.render(<BrowserPanel conversationId="b" />))
  expect(host.textContent).not.toContain('Local page')
})
it('keeps stop and takeover available while approval is pending', async () => {
  await act(async () => root.render(<BrowserPanel conversationId="a" />))
  await click('Browser · preview'); await click('Open browser')
  mocks.command.mockImplementation((_kind, frame) => frame.command.operation === 'list' ? Promise.resolve({ sessions: [] }) : new Promise(() => {}))
  await click('Read page')
  for (const label of ['Close / stop browser', 'Take over']) {
    const button = [...host.querySelectorAll('button')].find(b => b.textContent === label)
    expect(button?.disabled).toBe(false)
  }
  await click('Close / stop browser')
  expect(mocks.command).toHaveBeenCalledWith('browser_action', expect.objectContaining({ command: expect.objectContaining({ operation: 'close', session_id: 's' }) }), 360_000)
})

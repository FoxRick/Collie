// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ComputerPanel from './ComputerPanel'

const mock = vi.hoisted(() => ({ command: vi.fn() }))
vi.mock('../lib/ipc', () => ({ collieClient: mock }))
let host: HTMLDivElement
let root: Root
beforeEach(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  mock.command.mockReset(); mock.command.mockResolvedValue({})
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
function button(text: string): HTMLButtonElement {
  const value = [...host.querySelectorAll('button')].find(b => b.textContent === text)
  if (!value) throw new Error(`Missing button ${text}`)
  return value
}
async function click(text: string): Promise<void> { await act(async () => button(text).click()) }

it('keeps owning-chat Stop available while a controller request waits', async () => {
  let finish: (value: unknown) => void = () => {}
  mock.command.mockImplementation((_type, payload) => payload.command.operation === 'windows'
    ? new Promise(resolve => { finish = resolve }) : Promise.resolve({ stopped: true }))
  await act(async () => root.render(<ComputerPanel conversationId="one" />))
  await click('Computer · local preview')
  await click('Find open windows')
  expect(button('Stop / take over').disabled).toBe(false)
  await click('Stop / take over')
  expect(mock.command).toHaveBeenLastCalledWith('computer_action', { conversation_id: 'one', command: { operation: 'stop' } }, 360_000)
  await act(async () => finish({ windows: [{ pid: 7, window_id: 8, title: 'Late target', app_name: 'Fixture' }] }))
  expect(host.textContent).not.toContain('Late target')
})

it('drops pending target data when switching chats and does no idle polling', async () => {
  let finish: (value: unknown) => void = () => {}
  mock.command.mockImplementation((_type, payload) => payload.command.operation === 'windows'
    ? new Promise(resolve => { finish = resolve }) : Promise.resolve({}))
  await act(async () => root.render(<ComputerPanel conversationId="one" />))
  expect(mock.command).not.toHaveBeenCalled()
  await click('Computer · local preview')
  await click('Find open windows')
  await act(async () => root.render(<ComputerPanel conversationId="two" />))
  await act(async () => finish({ windows: [{ pid: 7, window_id: 8, title: 'Other chat target', app_name: 'Fixture' }] }))
  await click('Computer · local preview')
  expect(host.textContent).not.toContain('Other chat target')
  expect(mock.command).toHaveBeenLastCalledWith('computer_action', { conversation_id: 'two', command: { operation: 'status' } }, 360_000)
})

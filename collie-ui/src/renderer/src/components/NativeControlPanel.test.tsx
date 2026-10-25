// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import NativeControlPanel from './NativeControlPanel'

const client = vi.hoisted(() => ({ nativeControl: vi.fn() }))
vi.mock('../lib/ipc', () => ({ collieClient: client }))

it('clears selection after an unavailable editor and shows a readable error', async () => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  client.nativeControl
    .mockResolvedValueOnce({ windows: [{ hwnd: 1, started: '1', title: 'Untitled - Notepad' }] })
    .mockResolvedValueOnce({ selected: true })
    .mockRejectedValueOnce(new Error("Error invoking remote method 'collie:core-send': Error: This editor is unavailable."))
  const container = document.createElement('div')
  const root = createRoot(container)
  const click = async (text: string): Promise<void> => {
    const button = [...container.querySelectorAll('button')].find(b => b.textContent?.startsWith(text))!
    await act(async () => button.click())
  }
  try {
    await act(async () => root.render(<NativeControlPanel conversationId="qa" />))
    await click('Find supported windows')
    await click('Select Untitled')
    expect(container.textContent).toContain('Focus editor')
    await click('Check editor')
    expect(container.textContent).toContain('This editor is unavailable.')
    expect(container.textContent).not.toContain('remote method')
    expect(container.textContent).not.toContain('Focus editor')
  } finally {
    await act(async () => root.unmount())
  }
})

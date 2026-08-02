// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { TaskState } from '../lib/ipc'
import MessageBubble from './MessageBubble'

const roots: Root[] = []

function render(element: React.ReactNode): HTMLElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(element))
  return container
}

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

describe('MessageBubble terminal task summary', () => {
  it('renders a persisted terminal task state as a collapsed, read-only summary', () => {
    const task: TaskState = {
      id: 'terminal-task', source: 'checklist', status: 'completed', revision: 4,
      title: 'Plan a trip', completed_count: 3, total_count: 3, steps: []
    }
    const container = render(<MessageBubble role="assistant" content="All set." taskState={task} />)
    expect(container.textContent).toContain('All set.')
    expect(container.querySelector('[aria-label="Task progress"]')?.textContent).toContain('Task complete')
    expect(container.querySelector('[aria-label="Task progress"] button')).toBeNull()
  })
})

describe('MessageBubble attachments', () => {
  const previewDataUrl = 'data:image/png;base64,iVBORw0KGgo='

  it('opens an image thumbnail in an accessible preview and closes it with Escape', async () => {
    const container = render(
      <MessageBubble
        role="user"
        content="Here it is"
        attachments={[{
          name: 'screen.png',
          mime: 'image/png',
          size: 128,
          preview_data_url: previewDataUrl
        }]}
      />
    )
    const trigger = container.querySelector<HTMLButtonElement>(
      '[aria-label="Open screen.png preview"]'
    )!
    expect(trigger.querySelector('img')?.getAttribute('src')).toBe(previewDataUrl)

    act(() => trigger.click())
    const dialog = container.querySelector<HTMLElement>('[role="dialog"]')!
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.querySelector('img')?.getAttribute('alt')).toBe('screen.png')
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close image preview')

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    await act(async () => new Promise((resolve) => window.setTimeout(resolve, 0)))
    expect(document.activeElement).toBe(trigger)
  })

  it('keeps non-images and unsafe image preview sources filename-only', () => {
    const container = render(
      <MessageBubble
        role="user"
        content="Files"
        attachments={[
          { name: 'notes.txt', mime: 'text/plain', size: 12 },
          {
            name: 'unsafe.png',
            mime: 'image/png',
            size: 12,
            preview_data_url: 'javascript:alert(1)'
          }
        ]}
      />
    )
    expect(container.querySelectorAll('.message-attachment')).toHaveLength(2)
    expect(container.querySelector('.message-attachment--image')).toBeNull()
    expect(container.textContent).toContain('notes.txt')
    expect(container.textContent).toContain('unsafe.png')
  })
})

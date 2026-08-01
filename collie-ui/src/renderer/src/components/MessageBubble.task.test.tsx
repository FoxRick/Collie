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

// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TaskState } from '../../lib/ipc'
import TaskProgress from './TaskProgress'

const roots: Root[] = []

function render(element: React.ReactNode): HTMLElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(element))
  return container
}

const activeTask: TaskState = {
  id: 'task-1',
  source: 'checklist',
  status: 'active',
  revision: 2,
  title: 'Plan a weekend trip',
  completed_count: 1,
  total_count: 3,
  current_step_key: 'compare',
  steps: [
    { key: 'shortlist', title: 'Make a shortlist', status: 'completed', summary: 'Three places found.' },
    { key: 'compare', title: 'Compare options', status: 'in_progress', summary: 'Checking travel times.' },
    { key: 'book', title: 'Choose a favourite', status: 'pending' }
  ]
}

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

describe('TaskProgress', () => {
  it('expands an accessible checklist and lets the user stop active work', () => {
    const onStop = vi.fn()
    const container = render(<TaskProgress task={activeTask} onStop={onStop} />)
    const toggle = container.querySelector<HTMLButtonElement>('button[aria-expanded]')
    expect(toggle?.textContent).toContain('Compare options')
    expect(toggle?.getAttribute('aria-expanded')).toBe('false')
    const liveRegion = container.querySelector('[role="status"]')
    expect(liveRegion?.getAttribute('aria-live')).toBe('polite')
    expect(liveRegion?.getAttribute('aria-atomic')).toBe('true')

    act(() => toggle?.click())
    expect(toggle?.getAttribute('aria-expanded')).toBe('true')
    expect(container.querySelector('ol')?.textContent).toContain('Make a shortlist')
    expect(container.querySelector('[aria-live="polite"]')).toBeNull()

    const stop = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Stop'))
    act(() => stop?.click())
    expect(onStop).toHaveBeenCalledOnce()
  })

  it('keeps terminal work as a collapsed history summary without a stop control', () => {
    const completed: TaskState = { ...activeTask, status: 'completed', revision: 3, completed_count: 3 }
    const container = render(<TaskProgress task={completed} onStop={vi.fn()} />)
    expect(container.querySelector('[aria-expanded]')?.textContent).toContain('Task complete')
    expect(Array.from(container.querySelectorAll('button')).some((button) => button.textContent?.includes('Stop'))).toBe(false)
  })

  it('renders persisted history as a non-interactive collapsed summary', () => {
    const completed: TaskState = { ...activeTask, status: 'completed', revision: 3, completed_count: 3 }
    const container = render(<TaskProgress task={completed} readOnly />)
    expect(container.textContent).toContain('Task complete')
    expect(container.querySelector('button')).toBeNull()
    expect(container.querySelector('ol')).toBeNull()
  })
})

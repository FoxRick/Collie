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
  completed_count: 0,
  total_count: 7,
  current_step_key: 'research',
  steps: [
    { key: 'research', title: 'Research destinations', status: 'in_progress', summary: 'Finding options.' },
    { key: 'shortlist', title: 'Make a shortlist', status: 'pending' },
    { key: 'compare', title: 'Compare options', status: 'pending' },
    { key: 'book', title: 'Choose a favourite', status: 'pending' },
    { key: 'packing', title: 'Prepare a packing list', status: 'pending' },
    { key: 'tickets', title: 'Save travel details', status: 'pending' },
    { key: 'share', title: 'Share the itinerary', status: 'pending' }
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
  it('shows the current step number and expands an accessible checklist', () => {
    const onStop = vi.fn()
    const container = render(<TaskProgress task={activeTask} onStop={onStop} />)
    const toggle = container.querySelector<HTMLButtonElement>('button[aria-expanded]')
    expect(toggle?.textContent).toContain('Step 1 of 7')
    expect(toggle?.textContent).toContain('Research destinations')
    expect(toggle?.getAttribute('aria-expanded')).toBe('false')
    const liveRegion = container.querySelector('[role="status"]')
    expect(liveRegion?.getAttribute('aria-live')).toBe('polite')
    expect(liveRegion?.getAttribute('aria-atomic')).toBe('true')

    act(() => toggle?.click())
    expect(toggle?.getAttribute('aria-expanded')).toBe('true')
    expect(container.querySelector('ol')?.textContent).toContain('Research destinations')
    expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('in progress')
    expect(container.querySelector('[aria-live="polite"]')).toBeNull()

    const stop = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Stop'))
    act(() => stop?.click())
    expect(onStop).toHaveBeenCalledOnce()
  })

  it('joins the live checklist to the composer when requested', () => {
    const container = render(
      <TaskProgress task={activeTask} onStop={vi.fn()} attachedToComposer />
    )

    const progress = container.querySelector('section[aria-label="Task progress"]')
    expect(progress?.classList.contains('task-progress--composer')).toBe(true)
    expect((progress as HTMLElement | null)?.style.width).toBe('100%')
  })

  it('shows completion count and a checkmark for each completed step', () => {
    const completed: TaskState = {
      ...activeTask,
      status: 'completed',
      revision: 3,
      completed_count: 7,
      steps: activeTask.steps.map((step) => ({ ...step, status: 'completed' }))
    }
    const container = render(<TaskProgress task={completed} onStop={vi.fn()} />)
    const toggle = container.querySelector<HTMLButtonElement>('[aria-expanded]')
    expect(toggle?.textContent).toContain('Completed 7 of 7')
    expect(Array.from(container.querySelectorAll('button')).some((button) => button.textContent?.includes('Stop'))).toBe(false)
    act(() => toggle?.click())
    expect(container.querySelectorAll('svg.lucide-check')).toHaveLength(7)
    expect(container.querySelector('ol')?.textContent).toContain('completed')
  })

  it('clearly identifies blocked and failed steps when expanded', () => {
    const blocked: TaskState = {
      ...activeTask,
      status: 'blocked',
      revision: 4,
      completed_count: 1,
      current_step_key: 'shortlist',
      steps: [
        { ...activeTask.steps[0], status: 'completed' },
        { ...activeTask.steps[1], status: 'blocked', error_message: 'Need a budget before continuing.' },
        { ...activeTask.steps[2], status: 'failed', error_message: 'Comparison source was unavailable.' },
        ...activeTask.steps.slice(3)
      ]
    }
    const container = render(<TaskProgress task={blocked} />)
    const toggle = container.querySelector<HTMLButtonElement>('[aria-expanded]')
    expect(toggle?.textContent).toContain('Step 2 of 7')
    act(() => toggle?.click())
    expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('blocked')
    expect(container.querySelector('ol')?.textContent).toContain('failed')
    expect(container.textContent).toContain('Need a budget before continuing.')
    expect(container.textContent).toContain('Comparison source was unavailable.')
  })

  it('renders persisted history as a non-interactive collapsed summary', () => {
    const completed: TaskState = { ...activeTask, status: 'completed', revision: 3, completed_count: 7 }
    const container = render(<TaskProgress task={completed} readOnly />)
    expect(container.textContent).toContain('Task complete')
    expect(container.querySelector('button')).toBeNull()
    expect(container.querySelector('ol')).toBeNull()
  })
})

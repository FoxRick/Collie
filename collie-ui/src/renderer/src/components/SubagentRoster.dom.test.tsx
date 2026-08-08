// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { ActiveAgent } from '../lib/ipc'
import SubagentRoster from './SubagentRoster'

const working = (overrides: Partial<ActiveAgent> = {}): ActiveAgent => ({
  id: 'a1',
  name: 'Trip Planner',
  phase: 'awaiting_tools',
  task_description: 'Checking hotel prices in Eixample.',
  conversation_id: 'conv-1',
  started_at_ms: 1_000_000,
  ...overrides
})

const settled = (overrides: Partial<ActiveAgent> = {}): ActiveAgent => ({
  id: 'a2',
  name: 'Budget Checker',
  phase: 'done',
  task_description: 'Compare hotel prices.',
  conversation_id: 'conv-1',
  started_at_ms: 900_000,
  ended_at_ms: 1_100_000,
  outcome: 'ok',
  ...overrides
})

let root: Root | null = null
let host: HTMLElement | null = null

function render(element: React.ReactNode): void {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root!.render(element)
  })
}

function text(): string {
  return host?.textContent ?? ''
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  host?.remove()
  host = null
})

describe('SubagentRoster', () => {
  it('renders nothing when no agents are active or recent', () => {
    render(<SubagentRoster active={[]} recent={[]} nowMs={1_200_000} />)
    expect(host!.children.length).toBe(0)
  })

  it('shows Working now rows with the activity line and elapsed', () => {
    render(<SubagentRoster active={[working()]} recent={[]} nowMs={1_200_000} />)
    expect(text()).toContain('Working now')
    expect(text()).toContain('Trip Planner')
    expect(text()).toContain('Checking hotel prices in Eixample.')
    expect(text()).toContain('3m 20s')
    expect(text()).not.toContain('Earlier')
  })

  it('shows settled rows under Earlier with outcome copy', () => {
    render(
      <SubagentRoster
        active={[]}
        recent={[settled(), settled({ id: 'a3', name: 'Web Searcher', outcome: 'cancelled' })]}
        nowMs={1_200_000}
      />
    )
    expect(text()).toContain('Earlier')
    expect(text()).toContain('Budget Checker')
    expect(text()).toContain('Done')
    expect(text()).toContain('Web Searcher')
    expect(text()).toContain('Stopped')
    // Settled elapsed is frozen at ended_at - started_at = 3m 20s.
    expect(text()).toContain('3m 20s')
    expect(text()).not.toContain('Working now')
  })

  it('combines working and settled groups in order', () => {
    render(
      <SubagentRoster
        active={[working()]}
        recent={[settled()]}
        nowMs={1_200_000}
      />
    )
    const workingIndex = text().indexOf('Working now')
    const earlierIndex = text().indexOf('Earlier')
    expect(workingIndex).toBeGreaterThan(-1)
    expect(earlierIndex).toBeGreaterThan(workingIndex)
  })

  it('caps the working rows and shows the overflow count', () => {
    const many = Array.from({ length: 8 }, (_, index) =>
      working({ id: `w${index}`, name: `Agent ${index}` })
    )
    render(<SubagentRoster active={many} recent={[]} nowMs={1_200_000} />)
    expect(text()).toContain('+2 more working')
    expect(text()).toContain('Agent 0')
    expect(text()).toContain('Agent 5')
    expect(text()).not.toContain('Agent 7')
  })
})

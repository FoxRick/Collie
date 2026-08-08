// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActiveAgent } from '../lib/ipc'
import AgentsScreen from './AgentsScreen'

interface Activity {
  active_agents: ActiveAgent[]
  recent_agents: ActiveAgent[]
}

const working = (overrides: Partial<ActiveAgent> = {}): ActiveAgent => ({
  id: 'w1',
  name: 'Trip Planner',
  phase: 'awaiting_tools',
  task_description: 'Checking hotel prices in Eixample.',
  conversation_id: 'conv-1',
  started_at_ms: 1_000_000,
  ...overrides
})

const settled = (overrides: Partial<ActiveAgent> = {}): ActiveAgent => {
  const endedAt = Date.now() - 60_000
  return {
    id: 's1',
    name: 'Budget Checker',
    phase: 'done',
    task_description: 'Compare hotel prices.',
    conversation_id: 'conv-1',
    started_at_ms: endedAt - 120_000,
    ended_at_ms: endedAt,
    outcome: 'ok',
    ...overrides
  }
}

const hooks = vi.hoisted(() => {
  const client = {
    listSubagents: vi.fn(),
    listSkills: vi.fn(),
    getSubagentActivity: vi.fn(),
    listVersions: vi.fn(),
    rollbackArtifact: vi.fn(),
    createSubagent: vi.fn(),
    updateSubagent: vi.fn(),
    deleteSubagent: vi.fn()
  }
  return { client }
})

vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason?: unknown) => void
} {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let root: Root | null = null
let host: HTMLElement | null = null

function render(): void {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root!.render(<AgentsScreen />)
  })
}

function unmount(): void {
  act(() => {
    root?.unmount()
  })
  root = null
}

function text(): string {
  return host?.textContent ?? ''
}

beforeEach(() => {
  vi.useFakeTimers()
  hooks.client.listSubagents.mockReset()
  hooks.client.listSkills.mockReset()
  hooks.client.getSubagentActivity.mockReset()
  hooks.client.listSubagents.mockResolvedValue({ subagents: [], starters: [] })
  hooks.client.listSkills.mockResolvedValue({ skills: [] })
})

afterEach(() => {
  unmount()
  host?.remove()
  host = null
  vi.useRealTimers()
})

describe('AgentsScreen live roster poll', () => {
  it('does not stack requests while the core is slow', async () => {
    const pending = deferred<Activity>()
    hooks.client.getSubagentActivity.mockReturnValue(pending.promise)
    render()

    // The initial poll is in flight; the 2 s ticks must NOT fire more
    // requests on top of it (the old setInterval would have stacked ~3).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000)
    })
    expect(hooks.client.getSubagentActivity).toHaveBeenCalledTimes(1)
  })

  it('schedules the next tick only after the previous poll completes', async () => {
    const first = deferred<Activity>()
    const second = deferred<Activity>()
    hooks.client.getSubagentActivity
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    render()

    // First request still pending after 4 s: no second request.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000)
    })
    expect(hooks.client.getSubagentActivity).toHaveBeenCalledTimes(1)

    // Completing it arms the next tick; a new request fires 2 s later.
    await act(async () => {
      first.resolve({ active_agents: [working()], recent_agents: [] })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(hooks.client.getSubagentActivity).toHaveBeenCalledTimes(2)
    expect(text()).toContain('Trip Planner')

    // The latest response wins: newer rows replace older ones.
    await act(async () => {
      second.resolve({ active_agents: [working({ id: 'w2', name: 'Web Searcher' })], recent_agents: [] })
    })
    expect(text()).toContain('Web Searcher')
    expect(text()).not.toContain('Trip Planner')
  })

  it('a stale response from an earlier poll cannot overwrite newer rows', async () => {
    const first = deferred<Activity>()
    const second = deferred<Activity>()
    hooks.client.getSubagentActivity
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    render()

    // First poll settles with older data.
    await act(async () => {
      first.resolve({ active_agents: [working()], recent_agents: [] })
    })
    expect(text()).toContain('Trip Planner')

    // Second poll fires only after the first settled (completion-driven).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(hooks.client.getSubagentActivity).toHaveBeenCalledTimes(2)

    // Newer data lands and sticks — an out-of-order late response would have
    // to fight the generation guard, and with serialized polling it never
    // even starts while an older request is pending.
    await act(async () => {
      second.resolve({ active_agents: [working({ id: 'w2', name: 'Web Searcher' })], recent_agents: [] })
    })
    expect(text()).toContain('Web Searcher')
    expect(text()).not.toContain('Trip Planner')
  })

  it('stops polling and ignores late responses after unmount', async () => {
    const pending = deferred<Activity>()
    hooks.client.getSubagentActivity.mockReturnValue(pending.promise)
    render()
    await act(async () => {})

    expect(hooks.client.getSubagentActivity).toHaveBeenCalledTimes(1)
    unmount()

    // Resolving after unmount must not apply state or re-arm the timer.
    await act(async () => {
      pending.resolve({ active_agents: [working()], recent_agents: [] })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000)
    })
    expect(hooks.client.getSubagentActivity).toHaveBeenCalledTimes(1)
    expect(text()).not.toContain('Trip Planner')
  })

  it('clears stale working rows after three consecutive failures', async () => {
    const roster = { active_agents: [working()], recent_agents: [settled()] }
    hooks.client.getSubagentActivity
      .mockResolvedValueOnce(roster)
      .mockRejectedValueOnce(new Error('core down'))
      .mockRejectedValueOnce(new Error('core down'))
      .mockRejectedValueOnce(new Error('core down'))
    render()
    await act(async () => {})

    expect(text()).toContain('Trip Planner')
    expect(text()).toContain('Budget Checker')

    // Three failed polls in a row: working rows clear, settled rows stay.
    for (let index = 0; index < 3; index += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000)
      })
    }
    expect(text()).not.toContain('Trip Planner')
    expect(text()).toContain('Budget Checker')

    // A successful poll restores the roster and resets the failure counter.
    hooks.client.getSubagentActivity.mockResolvedValue(roster)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(text()).toContain('Trip Planner')
  })
})

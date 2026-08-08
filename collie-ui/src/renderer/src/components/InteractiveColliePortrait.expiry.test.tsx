// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActiveAgent } from '../lib/ipc'
import InteractiveColliePortrait from './InteractiveColliePortrait'

const settled = (overrides: Partial<ActiveAgent> = {}): ActiveAgent => ({
  id: 's1',
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

function render(props: Partial<React.ComponentProps<typeof InteractiveColliePortrait>> = {}): void {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root!.render(
      <InteractiveColliePortrait
        thinking={null}
        phrase=""
        isTyping={false}
        activeAgents={[]}
        recentAgents={[]}
        {...props}
      />
    )
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn()
  }) as unknown as typeof window.matchMedia
})

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  host?.remove()
  host = null
  vi.useRealTimers()
})

describe('InteractiveColliePortrait settled-row expiry', () => {
  it('unmounts the empty agent list and stops the ticker once rows expire', () => {
    const ended = Date.now() - 60_000
    render({
      recentAgents: [settled({ ended_at_ms: ended, started_at_ms: ended - 120_000 })]
    })

    expect(host!.textContent).toContain('Budget Checker')
    expect(host!.querySelector('.portrait-agent-list')).not.toBeNull()
    const timersWhileVisible = vi.getTimerCount()

    // The 1 s ticker keeps the elapsed/age calculation live up to the
    // 3-minute boundary.
    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(host!.textContent).toContain('Budget Checker')

    // Past the boundary: rows gone, the empty container unmounts, and the
    // ticker is cleared — no forever-mounted empty list.
    act(() => {
      vi.advanceTimersByTime(2 * 60_000 + 1_000)
    })
    expect(host!.textContent).not.toContain('Budget Checker')
    expect(host!.querySelector('.portrait-agent-list')).toBeNull()
    expect(vi.getTimerCount()).toBeLessThan(timersWhileVisible)
  })

  it('keeps rows visible while fresh settled rows arrive', () => {
    const ended = Date.now() - 10_000
    render({
      recentAgents: [settled({ ended_at_ms: ended, started_at_ms: ended - 120_000 })]
    })
    expect(host!.textContent).toContain('Budget Checker')

    act(() => {
      vi.advanceTimersByTime(120_000)
    })
    expect(host!.textContent).toContain('Budget Checker')
  })
})

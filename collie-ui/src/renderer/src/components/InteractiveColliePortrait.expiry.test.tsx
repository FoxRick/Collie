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
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
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
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('InteractiveColliePortrait settled-row expiry', () => {
  it('promotes a first-click wave to an open-mouth happy reaction on double click', () => {
    render()
    const ring = host!.querySelector('.collie-portrait-ring')!
    act(() => ring.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 })))
    expect(host!.querySelector('.is-click_reaction')).not.toBeNull()
    act(() => ring.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, detail: 2 })))
    expect(host!.querySelector('.is-happy_reaction')).not.toBeNull()
    act(() => vi.advanceTimersByTime(1_000))
    expect(host!.querySelector('.is-happy_reaction')).not.toBeNull()
    act(() => vi.advanceTimersByTime(651))
    expect(host!.querySelector('.is-happy_reaction')).toBeNull()
  })
  it.each(['awaiting_approval', 'error'])('keeps %s above pointer attention and pet reactions', (engine) => {
    render({ thinking: { state: engine, phrase: '', pet_animation: '' } })
    const ring = host!.querySelector('.collie-portrait-ring')!
    vi.spyOn(ring, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 100, width: 100, height: 100 } as DOMRect)
    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { clientX: 600, clientY: 150 }))
      ring.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      ring.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, detail: 2 }))
    })
    expect(host!.querySelector(engine === 'error' ? '.is-error' : '.is-waiting')).not.toBeNull()
    expect(host!.querySelector('.is-click_reaction')).toBeNull()
  })
  it('tracks outside the ring, keeps attention on ring exit, and returns on window blur', () => {
    render()
    const ring = host!.querySelector('.collie-portrait-ring')!
    vi.spyOn(ring, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 100, width: 100, height: 100 } as DOMRect)
    act(() => window.dispatchEvent(new MouseEvent('pointermove', { clientX: 600, clientY: 150 })))
    expect(host!.querySelector('.is-pointer_look')).not.toBeNull()
    act(() => ring.dispatchEvent(new MouseEvent('pointerout', { bubbles: true })))
    expect(host!.querySelector('.is-pointer_look')).not.toBeNull()
    act(() => window.dispatchEvent(new Event('blur')))
    expect(host!.querySelector('.is-pointer_look')).toBeNull()
  })
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

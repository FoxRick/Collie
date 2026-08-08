// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import MessageList from './MessageList'

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
  Element.prototype.scrollIntoView = vi.fn()
})

describe('MessageList streaming output', () => {
  it('renders the live response as Markdown without a blinking cursor', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(
      <MessageList messages={[]} streamText="A **smooth** response" />
    ))

    expect(container.querySelector('strong')?.textContent).toBe('smooth')
    expect(container.textContent).toBe('A smooth response')
    expect(container.querySelector('.collie-thinking')).toBeNull()

    act(() => root.unmount())
  })

  it('does not auto-follow after the user scrolls away from the bottom', () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const scrollIntoView = vi.mocked(Element.prototype.scrollIntoView)
    scrollIntoView.mockClear()

    act(() => root.render(<MessageList messages={[]} streamText="First frame" />))
    const scroller = container.firstElementChild as HTMLDivElement
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, value: 100, writable: true }
    })
    act(() => scroller.dispatchEvent(new Event('scroll', { bubbles: true })))
    const callsBeforeUpdate = scrollIntoView.mock.calls.length

    act(() => root.render(<MessageList messages={[]} streamText="Second frame" />))
    expect(scrollIntoView).toHaveBeenCalledTimes(callsBeforeUpdate)

    act(() => root.unmount())
  })

  it('holds the writing frame on the live bubble before the first token arrives', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(<MessageList messages={[]} streamText="" streaming />))
    expect(container.querySelector('.collie-skeleton--active')).not.toBeNull()
    expect(container.querySelector('.message-bubble')?.getAttribute('aria-busy')).toBe('true')

    act(() => root.unmount())
  })

  it('streams the first tokens into the same live bubble without a gap', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(<MessageList messages={[]} streamText="" streaming />))
    const bubble = container.querySelector('.message-bubble')

    act(() => root.render(<MessageList messages={[]} streamText="Hello there" streaming />))
    expect(container.querySelector('.message-bubble')).toBe(bubble)
    expect(container.textContent).toContain('Hello there')

    act(() => root.unmount())
  })

  it('leaves no frame behind once the live stream ends', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(<MessageList messages={[]} streamText="Done" streaming />))
    expect(container.querySelector('.collie-skeleton')).not.toBeNull()

    act(() => root.render(<MessageList messages={[]} streamText="" />))
    expect(container.querySelector('.collie-skeleton')).toBeNull()

    act(() => root.unmount())
  })
})

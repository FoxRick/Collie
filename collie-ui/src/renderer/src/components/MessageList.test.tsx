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
    const jump = container.querySelector<HTMLButtonElement>('.chat-jump-latest')
    expect(jump?.textContent).toContain('Jump to latest')
    act(() => jump?.click())
    expect(scrollIntoView).toHaveBeenCalledTimes(callsBeforeUpdate + 1)
    act(() => root.render(<MessageList messages={[]} streamText="Third frame" />))
    expect(scrollIntoView).toHaveBeenCalledTimes(callsBeforeUpdate + 2)

    act(() => root.unmount())
  })

  it('keeps following paused after a small upward scroll and resumes at the bottom', () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const scrollIntoView = vi.mocked(Element.prototype.scrollIntoView)
    scrollIntoView.mockClear()
    act(() => root.render(<MessageList messages={[]} streamText="First frame" />))
    const scroller = container.firstElementChild as HTMLDivElement
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, value: 700, writable: true }
    })
    act(() => scroller.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: -40 })))
    scroller.scrollTop = 660
    act(() => scroller.dispatchEvent(new Event('scroll', { bubbles: true })))
    const callsBeforeUpdate = scrollIntoView.mock.calls.length
    act(() => root.render(<MessageList messages={[]} streamText="Second frame" />))
    expect(scrollIntoView).toHaveBeenCalledTimes(callsBeforeUpdate)
    expect(container.querySelector('.chat-jump-latest')).not.toBeNull()
    scroller.scrollTop = 700
    act(() => scroller.dispatchEvent(new Event('scroll', { bubbles: true })))
    act(() => root.render(<MessageList messages={[]} streamText="Third frame" />))
    expect(scrollIntoView).toHaveBeenCalledTimes(callsBeforeUpdate + 1)
    expect(container.querySelector('.chat-jump-latest')).toBeNull()
    act(() => root.unmount())
  })

  it('shows a compact activity status without an empty answer before the first token', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(<MessageList messages={[]} streamText="" streaming />))
    expect(container.querySelector('[role="status"]')?.textContent).toContain('thinking')
    expect(container.querySelector('.message-bubble')).toBeNull()

    act(() => root.unmount())
  })

  it('replaces activity with the answer as soon as text arrives', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(<MessageList messages={[]} streamText="" streaming />))

    act(() => root.render(<MessageList messages={[]} streamText="Hello there" streaming />))
    expect(container.querySelector('.message-bubble')).not.toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
    expect(container.textContent).toContain('Hello there')

    act(() => root.unmount())
  })

  it('leaves no frame behind once the live stream ends', () => {
    const container = document.createElement('div')
    const root = createRoot(container)

    act(() => root.render(<MessageList messages={[]} streamText="Done" streaming />))
    expect(container.querySelector('.message-bubble')).not.toBeNull()

    act(() => root.render(<MessageList messages={[]} streamText="" />))
    expect(container.querySelector('.message-bubble')).toBeNull()

    act(() => root.unmount())
  })
})

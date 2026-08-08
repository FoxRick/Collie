// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
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

describe('MessageBubble streaming skeleton frame', () => {
  it('shows placeholder lines while the first tokens are still on their way', () => {
    const container = render(<MessageBubble role="assistant" content="" streaming />)
    const skeleton = container.querySelector('.collie-skeleton')
    expect(skeleton).not.toBeNull()
    expect(skeleton?.classList.contains('collie-skeleton--active')).toBe(true)
    expect(skeleton?.querySelectorAll('.collie-skeleton-line')).toHaveLength(3)
    expect(skeleton?.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelector('.message-bubble')?.getAttribute('aria-busy')).toBe('true')
  })

  it('keeps a short streamed answer inside the same active frame', () => {
    const container = render(<MessageBubble role="assistant" content="Sure — on it." streaming />)
    const skeleton = container.querySelector('.collie-skeleton')
    expect(skeleton).not.toBeNull()
    expect(skeleton?.classList.contains('collie-skeleton--active')).toBe(true)
    expect(container.textContent).toContain('Sure — on it.')
    expect(container.querySelector('.message-bubble')?.getAttribute('aria-busy')).toBe('true')
  })

  it('fades the placeholder lines once real text fills the card', () => {
    const container = render(
      <MessageBubble
        role="assistant"
        content="A longer answer that has clearly crossed the initial-latency threshold by now."
        streaming
      />
    )
    const skeleton = container.querySelector('.collie-skeleton')
    expect(skeleton).not.toBeNull()
    expect(skeleton?.classList.contains('collie-skeleton--settled')).toBe(true)
    expect(skeleton?.classList.contains('collie-skeleton--active')).toBe(false)
  })

  it('streams markdown into the same card while the frame is up', () => {
    const container = render(<MessageBubble role="assistant" content="A **smooth** answer" streaming />)
    expect(container.querySelector('strong')?.textContent).toBe('smooth')
    expect(container.querySelector('.message-content')).not.toBeNull()
  })

  it('never shows the frame for user bubbles', () => {
    const container = render(<MessageBubble role="user" content="" streaming />)
    expect(container.querySelector('.collie-skeleton')).toBeNull()
    expect(container.querySelector('.message-bubble')?.getAttribute('aria-busy')).toBeNull()
  })

  it('renders no frame once streaming has finished', () => {
    const container = render(<MessageBubble role="assistant" content="All set." />)
    expect(container.querySelector('.collie-skeleton')).toBeNull()
    expect(container.querySelector('.message-bubble')?.getAttribute('aria-busy')).toBeNull()
  })
})

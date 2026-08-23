// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import MarkdownContent from './MarkdownContent'

const roots: Root[] = []

function render(content: string): HTMLElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(<MarkdownContent content={content} />))
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

describe('MarkdownContent images', () => {
  it('does not render remote image URLs', () => {
    const container = render('![tracking pixel](https://tracker.example/pixel.png)')

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Remote image hidden for privacy: tracking pixel')
  })

  it('also blocks protocol-relative remote image URLs', () => {
    const container = render('![tracking pixel](//tracker.example/pixel.png)')

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Remote image hidden for privacy')
  })

  it.each([
    'http://tracker.example/pixel.png',
    'file:///C:/Users/example/private.png',
    'blob:https://tracker.example/id'
  ])('blocks scheme-bearing image source %s', (source) => {
    const container = render(`![tracking pixel](${source})`)

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Remote image hidden for privacy')
  })

  it('preserves local relative images', () => {
    const container = render('![local](./assets/example.png)')
    const images = container.querySelectorAll('img')

    expect(images).toHaveLength(1)
    expect(images[0]?.getAttribute('src')).toBe('./assets/example.png')
  })

  it('preserves supported inline raster images', () => {
    const container = render('![inline](data:image/png;base64,iVBORw0KGgo=)')

    expect(container.querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,iVBORw0KGgo=')
  })
})

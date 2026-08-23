// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import NewsCard from './NewsCard'

const roots: Root[] = []

function renderImage(source: unknown): HTMLElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(
    <NewsCard data={{ articles: [{ headline: 'Headline', source: 'Publisher', image: source }] }} />
  ))
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

describe('NewsCard images', () => {
  it.each([
    'https://tracker.example/pixel.png',
    '//tracker.example/pixel.png',
    'file:///C:/Users/example/private.png',
    'blob:https://tracker.example/id'
  ])('does not render scheme-bearing or protocol-relative source %s', (source) => {
    const container = renderImage(source)

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Headline')
  })

  it.each([
    ['./assets/news.png', './assets/news.png'],
    ['data:image/webp;base64,UklGRg==', 'data:image/webp;base64,UklGRg==']
  ])('preserves safe local or inline raster source %s', (source, expected) => {
    const container = renderImage(source)

    expect(container.querySelector('img')?.getAttribute('src')).toBe(expected)
  })
})

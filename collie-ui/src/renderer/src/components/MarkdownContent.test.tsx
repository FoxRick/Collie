// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeAll, describe, expect, it } from 'vitest'
import MarkdownContent from './MarkdownContent'

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

function renderIn(content: string): HTMLElement {
  const container = document.createElement('div')
  const root = createRoot(container)
  act(() => root.render(<MarkdownContent content={content} />))
  return container
}

describe('MarkdownContent image safety', () => {
  it('renders no images when none are present', () => {
    expect(renderIn('Just some text with no images.').querySelectorAll('img')).toHaveLength(0)
  })

  it('blocks remote https images', () => {
    expect(renderIn('![spy](https://evil.example/pixel.png)').querySelectorAll('img')).toHaveLength(0)
  })

  it('blocks remote http images', () => {
    expect(renderIn('![spy](http://evil.example/pixel.png)').querySelectorAll('img')).toHaveLength(0)
  })

  it('blocks other non-local schemes like file:', () => {
    expect(renderIn('![local](file:///etc/passwd.png)').querySelectorAll('img')).toHaveLength(0)
  })

  it('data: URIs never produce a fetchable img (react-markdown v10 strips the src itself)', () => {
    // Baseline behavior of the markdown library: data: URLs are sanitized out,
    // so our override has no src to allow — nothing renders. Still no remote fetch.
    expect(renderIn('![diagram](data:image/png;base64,AAAA)').querySelectorAll('img')).toHaveLength(0)
  })

  it('keeps same-origin relative paths (the local media server) with their src intact', () => {
    const img = renderIn('![diagram](/api/media/sig/payload)').querySelector('img')
    expect(img).not.toBeNull()
    expect(img?.getAttribute('src')).toBe('/api/media/sig/payload')
  })

  it('still renders links normally', () => {
    expect(renderIn('[Collie](https://heycollie.com)').querySelector('a')).not.toBeNull()
  })
})
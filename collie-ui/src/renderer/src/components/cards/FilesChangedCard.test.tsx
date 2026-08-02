// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import CardRenderer from './CardRenderer'
import FilesChangedCard, { parseFilesChangedCardData } from './FilesChangedCard'

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
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

describe('FilesChangedCard', () => {
  it('renders the explicit summary and each reported file in a collapsible card', () => {
    const container = render(<FilesChangedCard data={{
      files: [
        { path: 'src/app.ts', additions: 17, deletions: 1, status: 'modified' },
        { path: 'src/old.ts', additions: 0, deletions: 8, status: 'deleted' }
      ]
    }} />)

    expect(container.querySelector('[aria-label="Files changed"]')?.textContent).toContain('2 changed files')
    expect(container.querySelector('summary')?.textContent).toContain('+17')
    expect(container.querySelector('summary')?.textContent).toContain('-9')
    expect(container.querySelector('details')).not.toBeNull()
    expect(container.querySelector('[aria-label="Changed files"]')?.textContent).toContain('src/old.ts')
    expect(container.textContent).toContain('Deleted')
  })

  it('ignores malformed file records and safely normalizes invalid counts and statuses', () => {
    expect(parseFilesChangedCardData({
      files: [
        null,
        { path: '  ', additions: 2, deletions: 1 },
        { path: 'safe.ts', additions: -1, deletions: '3', status: 'unexpected' }
      ]
    })).toEqual({ files: [{ path: 'safe.ts', additions: 0, deletions: 0, status: 'modified' }] })
  })

  it('renders nothing for missing or invalid file lists', () => {
    const container = render(<CardRenderer cardType="files_changed" cardData={{ files: 'not a list' }} />)
    expect(container.querySelector('[aria-label="Files changed"]')).toBeNull()
    expect(container.textContent).toBe('')
  })
})

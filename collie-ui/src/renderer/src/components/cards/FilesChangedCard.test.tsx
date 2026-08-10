// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { collieClient } from '../../lib/ipc'
import CardRenderer from './CardRenderer'
import FilesChangedCard, { parseFilesChangedCardData } from './FilesChangedCard'

vi.mock('../../lib/ipc', () => ({
  collieClient: {
    undoFileChanges: vi.fn()
  }
}))

const mockedUndo = vi.mocked(collieClient.undoFileChanges)

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
  vi.clearAllMocks()
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

  it('parses undo entry ids and conversation id defensively', () => {
    expect(parseFilesChangedCardData({
      conversation_id: 'conv-1',
      files: [
        { path: 'notes.md', status: 'added', undo_entry_id: 'abc123' },
        { path: 'x.md', status: 'modified', undo_entry_id: 42 },
        { path: 'y.md', status: 'modified' }
      ]
    })).toEqual({
      conversationId: 'conv-1',
      files: [
        { path: 'notes.md', additions: 0, deletions: 0, status: 'added', undoEntryId: 'abc123' },
        { path: 'x.md', additions: 0, deletions: 0, status: 'modified' },
        { path: 'y.md', additions: 0, deletions: 0, status: 'modified' }
      ]
    })
  })

  it('offers one-tap undo and confirms when restored', async () => {
    mockedUndo.mockResolvedValue({ undone: [{ id: 'abc123', path: 'notes.md' }], errors: [] })
    const container = render(<FilesChangedCard data={{
      conversation_id: 'conv-1',
      files: [{ path: 'notes.md', status: 'modified', undo_entry_id: 'abc123' }]
    }} />)

    const button = container.querySelector('button')
    expect(button?.textContent).toContain('Take it back')

    await act(async () => { button?.click() })
    expect(mockedUndo).toHaveBeenCalledWith('conv-1', ['abc123'])
    expect(container.querySelector('[aria-label="Undo changes"]')?.textContent).toContain('Undone')
    expect(container.textContent).toContain('back the way they were')
  })

  it('surfaces a notice when undo fails', async () => {
    mockedUndo.mockResolvedValue({
      undone: [],
      errors: [{ id: 'abc123', path: 'notes.md', message: 'missing' }]
    })
    const container = render(<FilesChangedCard data={{
      conversation_id: 'conv-1',
      files: [{ path: 'notes.md', status: 'modified', undo_entry_id: 'abc123' }]
    }} />)

    await act(async () => { container.querySelector('button')?.click() })
    expect(container.querySelector('[aria-label="Undo changes"]')?.textContent).toContain('could not be restored')
  })

  it('hides the undo button without journal entries or a conversation id', () => {
    const noIds = render(<FilesChangedCard data={{
      files: [{ path: 'notes.md', status: 'modified' }]
    }} />)
    expect(noIds.querySelector('button')).toBeNull()

    const noConv = render(<FilesChangedCard data={{
      files: [{ path: 'notes.md', status: 'modified', undo_entry_id: 'abc' }]
    }} />)
    expect(noConv.querySelector('button')).toBeNull()
  })
})

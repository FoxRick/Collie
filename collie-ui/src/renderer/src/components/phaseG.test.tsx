/**
 * Component-level regression tests for Phase G fixes.
 *
 * These render with react-dom/server (no jsdom needed) and assert the pure
 * helpers extracted from the fixed components plus smoke-renders.
 */
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../lib/ipc', () => ({
  collieClient: {
    readFile: vi.fn(),
    writeFile: vi.fn(),
    resolveApproval: vi.fn(),
    approveAllForRun: vi.fn(),
    listApprovalRules: vi.fn().mockResolvedValue({ rules: [] }),
    getSettings: vi.fn().mockResolvedValue({ settings: {} }),
    deleteApprovalRule: vi.fn(),
    command: vi.fn()
  }
}))

describe('SuggestionCard merge (C9)', () => {
  it('appends the suggestion to non-empty files', async () => {
    const { mergeSuggestion } = await import('./cards/SuggestionCard')
    const merged = mergeSuggestion('# My notes\n\nSome content', 'Add this idea')
    expect(merged).toContain('# My notes')
    expect(merged).toContain('Add this idea')
    expect(merged.startsWith('# My notes')).toBe(true)
  })

  it('writes the suggestion alone into empty files', async () => {
    const { mergeSuggestion } = await import('./cards/SuggestionCard')
    expect(mergeSuggestion('', 'Only idea')).toBe('Only idea\n')
    expect(mergeSuggestion('   \n', 'Only idea')).toBe('Only idea\n')
  })

  it('never drops existing content even when the suggestion is empty', async () => {
    const { mergeSuggestion } = await import('./cards/SuggestionCard')
    expect(mergeSuggestion('Keep me', '   ')).toBe('Keep me')
  })
})

describe('MemoryTab person fields (N2)', () => {
  it('strips the UI-only id and empty values', async () => {
    const { personFieldsFrom } = await import('./settings/MemoryTab')
    const fields = personFieldsFrom({
      id: '',
      name: 'Mom',
      relationship: '',
      birthday: '03-15',
      notes: 'Loves tulips'
    })
    expect(fields).toEqual({ name: 'Mom', birthday: '03-15', notes: 'Loves tulips' })
    expect(fields.id).toBeUndefined()
  })
})

describe('ApprovalSheet smoke', () => {
  it('renders the pending action without crashing', async () => {
    const { default: ApprovalSheet } = await import('./approvals/ApprovalSheet')
    const markup = renderToStaticMarkup(
      createElement(ApprovalSheet, {
        approval: {
          id: 'a1',
          action: 'web_fetch',
          resource: 'https://example.com',
          risk: 'read',
          display_json: JSON.stringify({ summary: 'Fetch a page', reversible: true }),
          run_id: null
        },
        onResolved: () => undefined
      })
    )
    expect(markup).toContain('Fetch a page')
    expect(markup).toContain('Allow once')
    expect(markup).toContain('Reject')
  })
})

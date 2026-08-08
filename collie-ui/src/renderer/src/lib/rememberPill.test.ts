// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import type { MemoryJournalEntry } from './ipc'
import {
  journalEntryKey,
  loadPillShownCount,
  loadSeenPillKeys,
  rememberPillText,
  savePillShownCount,
  saveSeenPillKeys
} from './rememberPill'

function entry(overrides: Partial<MemoryJournalEntry> = {}): MemoryJournalEntry {
  return {
    id: 1,
    kind: 'fact',
    subject: 'name',
    action: 'add',
    value: 'Rick',
    created_at: '2026-08-08T10:00:00Z',
    ...overrides
  }
}

beforeEach(() => {
  sessionStorage.clear()
})

describe('rememberPillText', () => {
  it('names a short fact naturally', () => {
    expect(rememberPillText(entry())).toBe("Got it — I'll remember your name is Rick.")
  })

  it('trims whitespace around a short fact value', () => {
    expect(rememberPillText(entry({ value: '  Rick  ' }))).toBe(
      "Got it — I'll remember your name is Rick."
    )
  })

  it('falls back to the plain line for long facts', () => {
    expect(rememberPillText(entry({ value: 'a very long detail that exceeds the whisper limit' }))).toBe(
      "Got it — I'll remember that."
    )
  })

  it('falls back to the plain line for multi-line facts', () => {
    expect(rememberPillText(entry({ value: 'first line\nsecond line' }))).toBe(
      "Got it — I'll remember that."
    )
  })

  it('falls back to the plain line for empty values', () => {
    expect(rememberPillText(entry({ value: '' }))).toBe("Got it — I'll remember that.")
  })

  it('falls back to the plain line for structured values', () => {
    expect(rememberPillText(entry({ value: { city: 'Lisbon' } }))).toBe(
      "Got it — I'll remember that."
    )
  })

  it('falls back to the plain line for people and dates', () => {
    expect(rememberPillText(entry({ kind: 'person', subject: 'Mom' }))).toBe(
      "Got it — I'll remember that."
    )
    expect(rememberPillText(entry({ kind: 'date', subject: 'birthday' }))).toBe(
      "Got it — I'll remember that."
    )
  })
})

describe('journalEntryKey', () => {
  it('is stable for identical entries', () => {
    expect(journalEntryKey(entry({ id: 1 }))).toBe(journalEntryKey(entry({ id: 99 })))
  })

  it('distinguishes different values', () => {
    expect(journalEntryKey(entry({ value: 'Rick' }))).not.toBe(
      journalEntryKey(entry({ value: 'Alex' }))
    )
  })

  it('distinguishes deletes from adds of the same subject', () => {
    expect(journalEntryKey(entry({ action: 'add' }))).not.toBe(
      journalEntryKey(entry({ action: 'delete' }))
    )
  })

  it('serializes structured values deterministically', () => {
    expect(journalEntryKey(entry({ value: { city: 'Lisbon' } }))).toBe(
      journalEntryKey(entry({ value: { city: 'Lisbon' } }))
    )
  })
})

describe('session cap storage', () => {
  it('starts at zero and persists the shown count', () => {
    expect(loadPillShownCount()).toBe(0)
    savePillShownCount(2)
    expect(loadPillShownCount()).toBe(2)
  })

  it('ignores corrupted counts', () => {
    sessionStorage.setItem('collie.rememberPill.shown', 'not-a-number')
    expect(loadPillShownCount()).toBe(0)
  })

  it('round-trips the seen-key set', () => {
    expect(loadSeenPillKeys().size).toBe(0)
    saveSeenPillKeys(new Set(['fact|name|add|Rick']))
    expect(loadSeenPillKeys()).toEqual(new Set(['fact|name|add|Rick']))
  })

  it('ignores corrupted seen keys', () => {
    sessionStorage.setItem('collie.rememberPill.seen', '{oops')
    expect(loadSeenPillKeys().size).toBe(0)
  })
})

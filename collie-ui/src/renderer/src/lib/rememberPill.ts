/**
 * Remember pill — the quiet "I'll remember that." whisper.
 *
 * Pure helpers shared by the RememberPill component and its tests: the warm
 * one-line copy, the de-duplication key for identical journal entries, and
 * the per-session cap + seen-set persisted in sessionStorage (so the whisper
 * survives a renderer reload but resets when the app closes).
 */

import type { MemoryJournalEntry } from './ipc'

/** How many times the whisper may appear per app session (then silent). */
export const REMEMBER_PILL_CAP = 2
/** How long the whisper stays visible before dismissing itself. */
export const REMEMBER_PILL_DURATION_MS = 6000

const SHOWN_COUNT_KEY = 'collie.rememberPill.shown'
const SEEN_KEYS_KEY = 'collie.rememberPill.seen'

/**
 * Warm one-line acknowledgement of a fresh memory write. Uses the detail
 * variant only for short, single-line facts where it reads naturally
 * ("I'll remember your name is Rick."); preference/note writes get their own
 * friendly lines (the raw value is a sentence fragment that would not fit
 * the "your X is Y" template); everything else gets the plain line.
 */
export function rememberPillText(entry: MemoryJournalEntry): string {
  const value = typeof entry.value === 'string' ? entry.value.trim() : null
  const isShortFact =
    entry.kind === 'fact' &&
    value !== null &&
    value.length > 0 &&
    value.length <= 24 &&
    !value.includes('\n')
  if (isShortFact) {
    if (entry.subject === 'preferences') {
      return "Got it — I've saved your preference."
    }
    if (entry.subject === 'notes') {
      return "Got it — I've made a note of that."
    }
    return `Got it — I'll remember your ${entry.subject} is ${value}.`
  }
  return "Got it — I'll remember that."
}

/** Identity of a journal entry for de-duplication (kind + subject + action + value). */
export function journalEntryKey(entry: MemoryJournalEntry): string {
  const value =
    typeof entry.value === 'string' ? entry.value : JSON.stringify(entry.value ?? null)
  return `${entry.kind}|${entry.subject}|${entry.action}|${value}`
}

export function loadPillShownCount(): number {
  try {
    const raw = sessionStorage.getItem(SHOWN_COUNT_KEY)
    if (raw === null) return 0
    const count = Number.parseInt(raw, 10)
    return Number.isFinite(count) && count > 0 ? count : 0
  } catch {
    return 0
  }
}

export function savePillShownCount(count: number): void {
  try {
    sessionStorage.setItem(SHOWN_COUNT_KEY, String(count))
  } catch {
    // Storage unavailable — the in-memory cap still applies this session.
  }
}

export function loadSeenPillKeys(): Set<string> {
  try {
    const raw = sessionStorage.getItem(SEEN_KEYS_KEY)
    if (raw === null) return new Set()
    const parsed = JSON.parse(raw) as unknown
    return Array.isArray(parsed)
      ? new Set(parsed.filter((item): item is string => typeof item === 'string'))
      : new Set()
  } catch {
    return new Set()
  }
}

export function saveSeenPillKeys(keys: Set<string>): void {
  try {
    sessionStorage.setItem(SEEN_KEYS_KEY, JSON.stringify([...keys]))
  } catch {
    // Best effort only.
  }
}

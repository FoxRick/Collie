/**
 * RememberPill — the quiet "I'll remember that." whisper.
 *
 * Watches the core's append-only memory journal. When an assistant turn in
 * the current conversation completes and the journal shows a brand-new memory
 * write (kind fact/person/date, action add/update), a small warm pill appears
 * near the message list for a few seconds. It never fires for context usage
 * (reads are not journaled), never replays entries that existed before this
 * session, de-duplicates identical entries, and goes silent after two shows.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { collieClient, type CollieEvent, type MemoryJournalEntry } from '../lib/ipc'
import {
  journalEntryKey,
  loadPillShownCount,
  loadSeenPillKeys,
  REMEMBER_PILL_CAP,
  REMEMBER_PILL_DURATION_MS,
  rememberPillText,
  savePillShownCount,
  saveSeenPillKeys
} from '../lib/rememberPill'

interface Props {
  /** Only completed turns in this conversation count as "learning during chat". */
  conversationId: string | null
}

interface PillState {
  entry: MemoryJournalEntry
}

export default function RememberPill({ conversationId }: Props): React.JSX.Element | null {
  const [pill, setPill] = useState<PillState | null>(null)
  const shownRef = useRef(loadPillShownCount())
  const seenKeysRef = useRef(loadSeenPillKeys())
  const baselineRef = useRef<number | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Read the conversation id from a ref so the event subscription can be
  // registered exactly once — re-subscribing on every conversation adoption
  // would clobber ChatScreen's own collieClient listener (ordering tests).
  const conversationIdRef = useRef(conversationId)

  // ...but the ref *value* must track the active conversation. ChatScreen
  // mounts this component before the starter conversation is opened
  // (activeId starts null), so without this sync the assistant-turn gate
  // below would never pass in the real app.
  useEffect(() => {
    conversationIdRef.current = conversationId
  }, [conversationId])

  const dismiss = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setPill(null)
  }, [])

  const show = useCallback(
    (entry: MemoryJournalEntry) => {
      const key = journalEntryKey(entry)
      if (seenKeysRef.current.has(key)) return
      if (shownRef.current >= REMEMBER_PILL_CAP) return
      seenKeysRef.current.add(key)
      saveSeenPillKeys(seenKeysRef.current)
      shownRef.current += 1
      savePillShownCount(shownRef.current)
      setPill({ entry })
      if (timerRef.current !== null) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(dismiss, REMEMBER_PILL_DURATION_MS)
    },
    [dismiss]
  )

  /**
   * Ask the core for recent journal entries and whisper about anything new.
   * The first sight of the journal in a session only establishes the baseline
   * — old entries must never replay as if they were just learned.
   */
  const checkForLearning = useCallback(async () => {
    try {
      const { entries } = await collieClient.getMemoryJournal(5)
      if (entries.length === 0) return
      const newestId = entries[0].id
      const baseline = baselineRef.current
      if (baseline === null) {
        baselineRef.current = newestId
        return
      }
      baselineRef.current = Math.max(baseline, newestId)
      const fresh = entries.find(
        (entry) =>
          entry.id > baseline && (entry.action === 'add' || entry.action === 'update')
      )
      if (fresh) show(fresh)
    } catch {
      // Core unreachable — the next completed turn retries.
    }
  }, [show])

  /** Re-anchor the baseline without showing anything (app start / core restart). */
  const establishBaseline = useCallback(async () => {
    try {
      const { entries } = await collieClient.getMemoryJournal(1)
      baselineRef.current = entries.length > 0 ? entries[0].id : 0
    } catch {
      // Keep the current baseline; the next completed turn retries.
    }
  }, [])

  useEffect(() => {
    void establishBaseline()
  }, [establishBaseline])

  useEffect(() => {
    const off = collieClient.on((event: CollieEvent) => {
      switch (event.type) {
        case 'ready':
          // The core (re)started: clear the stale whisper and re-anchor so
          // nothing written before the restart is treated as new learning.
          dismiss()
          void establishBaseline()
          break
        case 'message':
          if (event.message.role === 'user') {
            // Any user send clears the whisper, per the quiet-whisper rule.
            dismiss()
          } else if (
            event.message.role === 'assistant' &&
            conversationIdRef.current !== null &&
            event.message.conversation_id === conversationIdRef.current
          ) {
            // The turn is committed — memory writes happened, check the journal.
            void checkForLearning()
          }
          break
        default:
          break
      }
    })
    return off
  }, [checkForLearning, dismiss, establishBaseline])

  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
    },
    []
  )

  if (!pill) return null
  return (
    <div className="remember-pill" role="status" aria-live="polite" onClick={dismiss}>
      <span aria-hidden="true">🧠</span>
      <span>{rememberPillText(pill.entry)}</span>
    </div>
  )
}

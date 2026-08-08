// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CollieEvent, MemoryJournalEntry } from '../lib/ipc'

const hooks = vi.hoisted(() => {
  const listeners = new Set<(event: CollieEvent) => void>()
  const client = {
    on: vi.fn((listener: (event: CollieEvent) => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    }),
    getMemoryJournal: vi.fn()
  }
  return {
    journal: { entries: [] as MemoryJournalEntry[] },
    emit: (event: CollieEvent): void => {
      for (const listener of [...listeners]) listener(event)
    },
    client
  }
})

vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))

import RememberPill from './RememberPill'

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

function assistantMessage(conversationId: string): CollieEvent {
  return {
    type: 'message',
    conversation_id: conversationId,
    message: {
      id: 'm-1',
      conversation_id: conversationId,
      role: 'assistant',
      content: 'Done!',
      created_at: '2026-08-08T10:00:01Z'
    }
  }
}

function userMessage(conversationId: string): CollieEvent {
  return {
    type: 'message',
    conversation_id: conversationId,
    message: {
      id: 'm-0',
      conversation_id: conversationId,
      role: 'user',
      content: 'remember my name',
      created_at: '2026-08-08T10:00:00Z'
    }
  }
}

/** Flush the mock's promise chains (baseline fetch, journal poll). */
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

beforeEach(() => {
  hooks.journal.entries = []
  hooks.client.getMemoryJournal.mockImplementation(async () => ({
    entries: hooks.journal.entries
  }))
  sessionStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('RememberPill', () => {
  it('whispers about a new fact after the assistant completes a turn', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush() // baseline established (empty journal -> 0)

    hooks.journal.entries = [entry({ id: 1 })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()

    const pill = container.querySelector('.remember-pill')
    expect(pill).not.toBeNull()
    expect(pill?.getAttribute('role')).toBe('status')
    expect(pill?.textContent).toContain("Got it — I'll remember your name is Rick.")

    act(() => root.unmount())
  })

  it('never replays entries that existed before this session', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    hooks.journal.entries = [entry({ id: 7 })]
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush() // baseline = 7

    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()

    expect(container.querySelector('.remember-pill')).toBeNull()
    act(() => root.unmount())
  })

  it('stays silent when the only journal activity is a delete', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    hooks.journal.entries = [entry({ id: 1, action: 'delete', value: 'Rick' })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()

    expect(container.querySelector('.remember-pill')).toBeNull()
    act(() => root.unmount())
  })

  it('ignores assistant turns in other conversations', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    hooks.journal.entries = [entry({ id: 1 })]
    await act(async () => {
      hooks.emit(assistantMessage('other-conversation'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).toBeNull()

    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()
    act(() => root.unmount())
  })

  it('dismisses on the next user send', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    hooks.journal.entries = [entry({ id: 1 })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()

    await act(async () => {
      hooks.emit(userMessage('c1'))
    })
    expect(container.querySelector('.remember-pill')).toBeNull()
    act(() => root.unmount())
  })

  it('auto-dismisses after four seconds', async () => {
    vi.useFakeTimers()
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    hooks.journal.entries = [entry({ id: 1 })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()

    act(() => {
      vi.advanceTimersByTime(4000)
    })
    expect(container.querySelector('.remember-pill')).toBeNull()
    act(() => root.unmount())
  })

  it('caps at two whispers per session, then stays silent', async () => {
    vi.useFakeTimers()
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    // First learning -> pill.
    hooks.journal.entries = [entry({ id: 1 })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()
    act(() => {
      vi.advanceTimersByTime(4000)
    })

    // Second learning -> pill again.
    hooks.journal.entries = [entry({ id: 2, subject: 'city', value: 'Lisbon' })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()
    act(() => {
      vi.advanceTimersByTime(4000)
    })

    // Third learning -> silent.
    hooks.journal.entries = [entry({ id: 3, subject: 'job', value: 'designer' })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).toBeNull()
    act(() => root.unmount())
  })

  it('de-duplicates identical journal entries', async () => {
    vi.useFakeTimers()
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    // First sight of "name = Rick" -> pill.
    hooks.journal.entries = [entry({ id: 1 })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()
    act(() => {
      vi.advanceTimersByTime(4000)
    })

    // The same fact re-learned (new journal id, identical content) -> silent.
    hooks.journal.entries = [entry({ id: 2 })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).toBeNull()

    // A genuinely new fact -> pill.
    hooks.journal.entries = [entry({ id: 3, subject: 'city', value: 'Lisbon' })]
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).not.toBeNull()
    act(() => root.unmount())
  })

  it('re-baselines after a core restart without replaying history', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(<RememberPill conversationId="c1" />))
    await flush()

    // Old entries exist before the restart.
    hooks.journal.entries = [entry({ id: 10 })]
    await act(async () => {
      hooks.emit({ type: 'ready', protocol: 1, phrase: 'Hi' })
    })
    await flush()
    expect(container.querySelector('.remember-pill')).toBeNull()

    // Nothing new after the restart -> still silent.
    await act(async () => {
      hooks.emit(assistantMessage('c1'))
    })
    await flush()
    expect(container.querySelector('.remember-pill')).toBeNull()
    act(() => root.unmount())
  })
})

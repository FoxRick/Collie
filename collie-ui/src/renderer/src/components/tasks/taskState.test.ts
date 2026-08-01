import { describe, expect, it } from 'vitest'
import type { TaskState } from '../../lib/ipc'
import {
  applyTaskState,
  beginTaskHydration,
  isCurrentConversationEvent,
  isCurrentTaskHydration,
  recordTaskStateEvent
} from './taskState'

const task = (revision: number, id = 'task-1'): TaskState => ({
  id,
  source: 'checklist',
  status: 'active',
  revision,
  title: 'Compare hotels',
  completed_count: 1,
  total_count: 3,
  current_step_key: 'research',
  steps: [
    { key: 'research', title: 'Compare hotels', status: 'in_progress' },
    { key: 'choose', title: 'Choose one', status: 'pending' },
    { key: 'book', title: 'Book it', status: 'pending' }
  ]
})

describe('applyTaskState', () => {
  it('does not regress a conversation to an older full snapshot', () => {
    const current = task(4)
    expect(applyTaskState({ c1: current }, 'c1', task(3))).toEqual({ c1: current })
  })

  it('ignores an equal-revision replay of the same task', () => {
    const current = { ...task(4), title: 'Newest title' }
    const replay = task(4)
    expect(applyTaskState({ c1: current }, 'c1', replay)).toEqual({ c1: current })
  })

  it('keeps state isolated by conversation', () => {
    const c1 = task(2, 'c1-task')
    const c2 = task(1, 'c2-task')
    expect(applyTaskState({ c1 }, 'c2', c2)).toEqual({ c1, c2 })
  })

  it('accepts a new task identity even when its revision starts below a completed predecessor', () => {
    const completed = { ...task(3, 'old-task'), status: 'completed' }
    const fresh = task(1, 'new-task')
    expect(applyTaskState({ c1: completed }, 'c1', fresh)).toEqual({ c1: fresh })
  })

  it('removes a conversation only when its rehydration says it has no active task', () => {
    expect(applyTaskState({ c1: task(1) }, 'c1', null)).toEqual({})
  })

  it('rejects a stale null hydration after a pushed task state', () => {
    const generations: Record<string, number> = {}
    const requestGeneration = beginTaskHydration(generations, 'c1')
    recordTaskStateEvent(generations, 'c1')
    expect(isCurrentTaskHydration(generations, 'c1', requestGeneration)).toBe(false)
  })

  it('does not treat background assistant traffic as belonging to the open conversation', () => {
    expect(isCurrentConversationEvent('background', 'open-chat')).toBe(false)
    expect(isCurrentConversationEvent('open-chat', 'open-chat')).toBe(true)
  })
})

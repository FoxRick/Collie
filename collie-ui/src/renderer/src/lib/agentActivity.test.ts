import { describe, expect, it } from 'vitest'
import type { ActiveAgent } from './ipc'
import {
  SETTLED_VISIBILITY_MS,
  agentActivityLine,
  agentElapsedMs,
  agentOutcomeLabel,
  agentPhaseLabel,
  formatAgentElapsed,
  isAgentSettled,
  settledRowsWithinWindow
} from './agentActivity'

const workingAgent = (overrides: Partial<ActiveAgent> = {}): ActiveAgent => ({
  id: 'a1',
  name: 'Trip Planner',
  phase: 'awaiting_tools',
  task_description: 'Checking hotel prices in Eixample.',
  conversation_id: 'conv-1',
  started_at_ms: 1_000_000,
  ...overrides
})

describe('agentPhaseLabel', () => {
  it('maps engine phases to friendly copy', () => {
    expect(agentPhaseLabel({ phase: 'awaiting_tools' })).toBe('Using tools')
    expect(agentPhaseLabel({ phase: 'final_response' })).toBe('Wrapping up')
    expect(agentPhaseLabel({ phase: 'cancelled' })).toBe('Stopped')
  })

  it('falls back to the raw phase for unknown values', () => {
    expect(agentPhaseLabel({ phase: 'barking' })).toBe('barking')
  })
})

describe('agentActivityLine', () => {
  it('prefers the task description', () => {
    expect(agentActivityLine(workingAgent())).toBe('Checking hotel prices in Eixample.')
  })

  it('falls back to the phase label and truncates long tasks', () => {
    expect(agentActivityLine(workingAgent({ task_description: undefined })))
      .toBe('Using tools')
    const long = 'x'.repeat(100)
    const line = agentActivityLine(workingAgent({ task_description: long }))
    expect(line.length).toBeLessThanOrEqual(72)
    expect(line.endsWith('…')).toBe(true)
  })
})

describe('agentOutcomeLabel', () => {
  it('labels each outcome', () => {
    expect(agentOutcomeLabel('ok')).toBe('Done')
    expect(agentOutcomeLabel('error')).toBe('Couldn’t finish')
    expect(agentOutcomeLabel('cancelled')).toBe('Stopped')
  })
})

describe('formatAgentElapsed', () => {
  it('formats seconds, minutes and hours compactly', () => {
    expect(formatAgentElapsed(5_000)).toBe('5s')
    expect(formatAgentElapsed(134_000)).toBe('2m 14s')
    expect(formatAgentElapsed(60_000)).toBe('1m')
    expect(formatAgentElapsed(3_781_000)).toBe('1h 3m')
  })
})

describe('agentElapsedMs', () => {
  it('freezes settled rows at ended_at', () => {
    const agent = workingAgent({
      ended_at_ms: 1_200_000,
      outcome: 'ok'
    })
    expect(agentElapsedMs(agent, 9_000_000)).toBe(200_000)
  })

  it('ticks working rows against now', () => {
    expect(agentElapsedMs(workingAgent(), 1_200_000)).toBe(200_000)
  })

  it('returns null without a started_at_ms', () => {
    expect(agentElapsedMs(workingAgent({ started_at_ms: undefined }), 1_200_000)).toBeNull()
  })
})

describe('isAgentSettled', () => {
  it('recognizes terminal outcomes', () => {
    expect(isAgentSettled(workingAgent({ outcome: 'ok' }))).toBe(true)
    expect(isAgentSettled(workingAgent({ outcome: 'error' }))).toBe(true)
    expect(isAgentSettled(workingAgent({ outcome: 'cancelled' }))).toBe(true)
    expect(isAgentSettled(workingAgent({ outcome: undefined }))).toBe(false)
  })
})

describe('settledRowsWithinWindow', () => {
  it('keeps only settled rows inside the visibility window', () => {
    const now = Date.now()
    const fresh = workingAgent({
      ended_at_ms: now - 60_000,
      outcome: 'ok'
    })
    const expired = workingAgent({
      id: 'a2',
      ended_at_ms: now - SETTLED_VISIBILITY_MS - 1_000,
      outcome: 'ok'
    })
    const stillWorking = workingAgent({ id: 'a3', outcome: undefined })
    expect(settledRowsWithinWindow([fresh, expired, stillWorking], now)).toEqual([fresh])
  })

  it('drops rows with no wall-clock ended_at', () => {
    const now = Date.now()
    const missing = workingAgent({ id: 'a4', ended_at_ms: undefined, outcome: 'ok' })
    expect(settledRowsWithinWindow([missing], now)).toEqual([])
  })
})

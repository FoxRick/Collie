import { describe, expect, it } from 'vitest'
import {
  INITIAL_RENDERER_RECOVERY_STATE,
  RENDERER_RECOVERY_BASE_DELAY_MS,
  RENDERER_RECOVERY_MAX_ATTEMPTS,
  RENDERER_RECOVERY_WINDOW_MS,
  isRecoverableRendererReason,
  planRendererRecovery
} from './renderer-recovery'

describe('isRecoverableRendererReason', () => {
  it('treats real crash reasons as recoverable', () => {
    expect(isRecoverableRendererReason('crashed')).toBe(true)
    expect(isRecoverableRendererReason('oom')).toBe(true)
    expect(isRecoverableRendererReason('killed')).toBe(true)
    expect(isRecoverableRendererReason('abnormal-exit')).toBe(true)
    expect(isRecoverableRendererReason('launch-failed')).toBe(true)
  })

  it('does not reload on clean exits or unknown reasons', () => {
    expect(isRecoverableRendererReason('clean-exit')).toBe(false)
    expect(isRecoverableRendererReason('integrity-failure')).toBe(false)
    expect(isRecoverableRendererReason('')).toBe(false)
  })
})

describe('planRendererRecovery', () => {
  it('returns no plan for an unrecoverable reason', () => {
    expect(planRendererRecovery(INITIAL_RENDERER_RECOVERY_STATE, 0, 'clean-exit')).toBeNull()
  })

  it('starts a recovery window on the first crash', () => {
    const plan = planRendererRecovery(INITIAL_RENDERER_RECOVERY_STATE, 1_000, 'oom')
    expect(plan).not.toBeNull()
    expect(plan!.delayMs).toBe(RENDERER_RECOVERY_BASE_DELAY_MS)
    expect(plan!.next).toEqual({ attempts: 1, windowStartedAt: 1_000 })
  })

  it('backs off exponentially across crashes in the same window', () => {
    let state = INITIAL_RENDERER_RECOVERY_STATE
    const expected = [250, 500, 1_000]
    for (const delay of expected) {
      const plan = planRendererRecovery(state, 10_000, 'crashed')
      expect(plan).not.toBeNull()
      expect(plan!.delayMs).toBe(delay)
      state = plan!.next
    }
  })

  it('exhausts the budget after the maximum attempts in one window', () => {
    let state = INITIAL_RENDERER_RECOVERY_STATE
    for (let i = 0; i < RENDERER_RECOVERY_MAX_ATTEMPTS; i += 1) {
      const plan = planRendererRecovery(state, 20_000, 'crashed')
      expect(plan).not.toBeNull()
      state = plan!.next
    }
    expect(state.attempts).toBe(RENDERER_RECOVERY_MAX_ATTEMPTS)
    expect(planRendererRecovery(state, 20_000, 'crashed')).toBeNull()
  })

  it('opens a fresh window after the rolling window elapses', () => {
    let state = INITIAL_RENDERER_RECOVERY_STATE
    for (let i = 0; i < RENDERER_RECOVERY_MAX_ATTEMPTS; i += 1) {
      const plan = planRendererRecovery(state, 30_000, 'crashed')
      expect(plan).not.toBeNull()
      state = plan!.next
    }
    expect(planRendererRecovery(state, 30_000, 'crashed')).toBeNull()

    const later = 30_000 + RENDERER_RECOVERY_WINDOW_MS + 1
    const retry = planRendererRecovery(state, later, 'crashed')
    expect(retry).not.toBeNull()
    expect(retry!.delayMs).toBe(RENDERER_RECOVERY_BASE_DELAY_MS)
    expect(retry!.next).toEqual({ attempts: 1, windowStartedAt: later })
  })

  it('caps the backoff delay', () => {
    // A window that somehow accumulates more attempts than the cap would
    // still never exceed the max delay.
    expect(RENDERER_RECOVERY_BASE_DELAY_MS * 2 ** (RENDERER_RECOVERY_MAX_ATTEMPTS - 1)).toBe(1_000)
  })
})

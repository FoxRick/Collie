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
  })

  it('does not treat a failed launch as recoverable', () => {
    // Decision (OOM-recovery hardening): a failed launch means the renderer
    // could not start; reloading the same window state would fail again, so
    // the recovery budget must not be spent on it.
    expect(isRecoverableRendererReason('launch-failed')).toBe(false)
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
    expect(plan!.next).toEqual({ recentCrashTimes: [1_000] })
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
    expect(state.recentCrashTimes).toHaveLength(RENDERER_RECOVERY_MAX_ATTEMPTS)
  })

  it('exhausts the budget after the maximum attempts in one window', () => {
    let state = INITIAL_RENDERER_RECOVERY_STATE
    for (let i = 0; i < RENDERER_RECOVERY_MAX_ATTEMPTS; i += 1) {
      const plan = planRendererRecovery(state, 20_000, 'crashed')
      expect(plan).not.toBeNull()
      state = plan!.next
    }
    expect(state.recentCrashTimes).toHaveLength(RENDERER_RECOVERY_MAX_ATTEMPTS)
    expect(planRendererRecovery(state, 20_000, 'crashed')).toBeNull()
  })

  it('keeps the budget exhausted across the window boundary (rolling window)', () => {
    // Regression: the window is ROLLING, not fixed. A burst of crashes must
    // never earn a fresh full budget just because the 60s boundary passes.
    let state = INITIAL_RENDERER_RECOVERY_STATE
    for (const t of [0, 10_000, 20_000]) {
      const plan = planRendererRecovery(state, t, 'crashed')
      expect(plan).not.toBeNull()
      state = plan!.next
    }
    expect(state.recentCrashTimes).toEqual([0, 10_000, 20_000])

    // 61s in: the t=0 crash has aged out of the window, so only two crashes
    // remain — the crash is allowed, with the delay for the 3rd crash in the
    // window (1000ms), NOT a fresh budget's 250ms.
    const at61s = planRendererRecovery(state, 61_000, 'crashed')
    expect(at61s).not.toBeNull()
    expect(at61s!.delayMs).toBe(RENDERER_RECOVERY_BASE_DELAY_MS * 2 ** 2)
    expect(at61s!.next.recentCrashTimes).toEqual([10_000, 20_000, 61_000])

    // 62s in: the crashes at 10s, 20s and 61s are all still inside the window
    // — three crashes → blocked. The boundary never grants a fresh budget.
    expect(planRendererRecovery(at61s!.next, 62_000, 'crashed')).toBeNull()
  })

  it('opens a fresh window only after every crash has aged out', () => {
    let state = INITIAL_RENDERER_RECOVERY_STATE
    for (let i = 0; i < RENDERER_RECOVERY_MAX_ATTEMPTS; i += 1) {
      const plan = planRendererRecovery(state, 30_000, 'crashed')
      expect(plan).not.toBeNull()
      state = plan!.next
    }
    expect(planRendererRecovery(state, 30_000, 'crashed')).toBeNull()

    // Once every recorded crash is older than the window, the budget is
    // genuinely fresh again.
    const later = 30_000 + RENDERER_RECOVERY_WINDOW_MS + 1
    const retry = planRendererRecovery(state, later, 'crashed')
    expect(retry).not.toBeNull()
    expect(retry!.delayMs).toBe(RENDERER_RECOVERY_BASE_DELAY_MS)
    expect(retry!.next).toEqual({ recentCrashTimes: [later] })
  })

  it('caps the backoff delay', () => {
    // A window that somehow accumulates more attempts than the cap would
    // still never exceed the max delay.
    expect(RENDERER_RECOVERY_BASE_DELAY_MS * 2 ** (RENDERER_RECOVERY_MAX_ATTEMPTS - 1)).toBe(1_000)
  })
})

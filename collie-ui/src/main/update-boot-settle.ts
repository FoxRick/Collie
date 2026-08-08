/**
 * Core "did it really settle?" judgement for post-update boot verification.
 *
 * The Python core flips to `running` on its FIRST ready stdout message, which
 * is not sustained health: a core that crashes seconds later must not be
 * accepted as last-known-good. When an update boot record is pending (the
 * just-installed version is the one now starting), we require the core to be
 * observed CONTINUOUSLY `running` for a probation window before the boot is
 * judged healthy:
 *
 * - any `failed` sample during probation → immediate `failed` verdict;
 * - any `starting` (or `stopped`) sample resets the continuous-running clock;
 * - the overall timeout bounds the wait → `failed`.
 *
 * On a normal first boot (no pending record) the fast path keeps startup
 * snappy: the first `running` sample is the verdict, and only a sustained
 * `failed` period or the overall timeout yields `failed`.
 *
 * This module is a pure state machine — all time is passed in, so the policy
 * is fully unit-testable without timers.
 */

export type CoreSettleState = 'stopped' | 'starting' | 'running' | 'failed'
export type CoreSettleVerdict = 'running' | 'failed'

export interface CoreSettleConfig {
  /** Continuous `running` required before an update boot is accepted. */
  probationMs: number
  /** Overall budget before the wait is abandoned as failed. */
  timeoutMs: number
  /** How often the main process re-samples coreState(). */
  pollIntervalMs: number
}

export const DEFAULT_CORE_SETTLE_CONFIG: CoreSettleConfig = {
  probationMs: 5_000,
  timeoutMs: 60_000,
  pollIntervalMs: 300
}

export interface CoreSettleMachine {
  /** Null until the machine has decided. */
  verdict: CoreSettleVerdict | null
  /** True when a pending update record demands the probation window. */
  requireProbation: boolean
  /** Start of the current uninterrupted `running` streak, or null. */
  runningSince: number | null
  /** Start of the current `failed` streak (fast path only), or null. */
  failedSince: number | null
  /** Machine creation time; the overall timeout counts from here. */
  startedAt: number
}

export function createCoreSettleMachine(
  now: number,
  requireProbation: boolean
): CoreSettleMachine {
  return {
    verdict: null,
    requireProbation,
    runningSince: null,
    failedSince: null,
    startedAt: now
  }
}

/**
 * Feed one core-state sample into the machine. Returns the (possibly
 * updated) machine; once a verdict is set it is never changed.
 */
export function sampleCoreSettle(
  machine: CoreSettleMachine,
  state: CoreSettleState,
  now: number,
  config: CoreSettleConfig = DEFAULT_CORE_SETTLE_CONFIG
): CoreSettleMachine {
  if (machine.verdict !== null) return machine

  if (state === 'running') {
    const runningSince = machine.runningSince ?? now
    if (!machine.requireProbation || now - runningSince >= config.probationMs) {
      return { ...machine, runningSince, verdict: 'running' }
    }
    return { ...machine, runningSince }
  }

  if (state === 'failed') {
    const failedSince = machine.failedSince ?? now
    // During probation any failure is fatal; on the fast path only a
    // sustained failure (the supervision respawns after 3s) is a verdict.
    if (machine.requireProbation || now - failedSince >= config.probationMs) {
      return { ...machine, failedSince, verdict: 'failed' }
    }
    return { ...machine, failedSince }
  }

  // 'starting' / 'stopped': not running, so any running streak is over.
  if (now - machine.startedAt >= config.timeoutMs) {
    return { ...machine, verdict: 'failed' }
  }
  return { ...machine, runningSince: null, failedSince: null }
}

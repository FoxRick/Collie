import { describe, expect, it } from 'vitest'
import {
  createCoreSettleMachine,
  sampleCoreSettle,
  type CoreSettleState
} from './update-boot-settle'

/**
 * Drive the pure settle machine through a scripted sequence of
 * [timestamp, state] samples starting at t=0.
 */
function settle(
  samples: Array<[number, CoreSettleState]>,
  requireProbation: boolean
): ReturnType<typeof createCoreSettleMachine> {
  let machine = createCoreSettleMachine(0, requireProbation)
  for (const [now, state] of samples) {
    machine = sampleCoreSettle(machine, state, now)
  }
  return machine
}

describe('core settle — fast path (no pending update record)', () => {
  it('resolves running on the first running sample so startup is not delayed', () => {
    const machine = settle([[100, 'starting'], [400, 'running']], false)
    expect(machine.verdict).toBe('running')
  })

  it('tolerates a short failed blip before the core comes up', () => {
    const machine = settle([[0, 'failed'], [1000, 'failed'], [2000, 'running']], false)
    expect(machine.verdict).toBe('running')
  })

  it('fails only after a sustained failed period', () => {
    const m1 = settle([[0, 'failed'], [4999, 'failed']], false)
    expect(m1.verdict).toBeNull()
    const m2 = sampleCoreSettle(m1, 'failed', 5000)
    expect(m2.verdict).toBe('failed')
  })

  it('times out when the core never leaves starting', () => {
    const m1 = settle([[0, 'starting'], [59999, 'starting']], false)
    expect(m1.verdict).toBeNull()
    const m2 = sampleCoreSettle(m1, 'starting', 60000)
    expect(m2.verdict).toBe('failed')
  })
})

describe('core settle — probation (pending update record matches current version)', () => {
  it('requires the core to run continuously for the full probation window', () => {
    const m1 = settle([[0, 'starting'], [100, 'running'], [5099, 'running']], true)
    expect(m1.verdict).toBeNull()
    const m2 = sampleCoreSettle(m1, 'running', 5100)
    expect(m2.verdict).toBe('running')
  })

  it('is failed when the core goes ready then crashes mid-window', () => {
    const machine = settle([[0, 'running'], [2000, 'running'], [2100, 'failed']], true)
    expect(machine.verdict).toBe('failed')
  })

  it('treats any failed sample during probation as an immediate failure', () => {
    const machine = settle([[0, 'starting'], [300, 'failed']], true)
    expect(machine.verdict).toBe('failed')
  })

  it('resets the continuous-running clock when the core respawns', () => {
    // 4s running → respawn → 4s running again: the interrupted streaks must
    // NOT accumulate toward the 5s window.
    const m1 = settle(
      [[0, 'running'], [4000, 'running'], [4100, 'starting'], [8100, 'running']],
      true
    )
    expect(m1.verdict).toBeNull()
    expect(m1.runningSince).toBe(8100)

    const m2 = sampleCoreSettle(m1, 'running', 13099)
    expect(m2.verdict).toBeNull()
    const m3 = sampleCoreSettle(m2, 'running', 13100)
    expect(m3.verdict).toBe('running')
  })

  it('times out when the core never reaches running', () => {
    const m1 = settle([[0, 'starting'], [30000, 'starting'], [59999, 'starting']], true)
    expect(m1.verdict).toBeNull()
    const m2 = sampleCoreSettle(m1, 'starting', 60000)
    expect(m2.verdict).toBe('failed')
  })
})

describe('core settle — verdict stability', () => {
  it('keeps the verdict once decided', () => {
    const m1 = settle([[0, 'running'], [5000, 'running']], true)
    expect(m1.verdict).toBe('running')
    const m2 = sampleCoreSettle(m1, 'failed', 6000)
    expect(m2.verdict).toBe('running')
  })
})

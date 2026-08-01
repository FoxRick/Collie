import { describe, expect, it } from 'vitest'
import {
  coreExitError,
  LineBuffer,
  parseCoreProtocolLine,
  RestartBudget
} from './core-supervision'

describe('LineBuffer', () => {
  it('retains split readiness messages until the newline arrives', () => {
    const lines = new LineBuffer()
    expect(lines.push('COLLIE_RE')).toEqual([])
    expect(lines.push('ADY {"port": 4242}\npartial')).toEqual(['COLLIE_READY {"port": 4242}'])
    expect(parseCoreProtocolLine('COLLIE_READY {"port": 4242}')).toEqual({
      kind: 'ready',
      port: 4242
    })
    expect(lines.flush()).toEqual(['partial'])
    expect(lines.flush()).toEqual([])
  })

  it('handles CRLF boundaries and split UTF-8 buffers', () => {
    const lines = new LineBuffer()
    const encoded = Buffer.from('puppy 🐕\r\nnext', 'utf8')
    const split = encoded.indexOf(0xf0) + 2
    expect(lines.push(encoded.subarray(0, split))).toEqual([])
    expect(lines.push(encoded.subarray(split))).toEqual(['puppy 🐕'])
    expect(lines.flush()).toEqual(['next'])
  })

  it('keeps stdout and stderr remainders independent', () => {
    const stdout = new LineBuffer()
    const stderr = new LineBuffer()
    expect(stdout.push('COLLIE_RE')).toEqual([])
    expect(stderr.push('warning\n')).toEqual(['warning'])
    expect(stdout.push('ADY {"port": 3818}\n')).toEqual(['COLLIE_READY {"port": 3818}'])
  })
})

describe('core restart budget', () => {
  const healthyWindow = 5 * 60 * 1000

  it('does not respawn after an intentional stop or clean exit', () => {
    const budget = new RestartBudget(3, healthyWindow)
    expect(budget.recordExit(null, true, 0)).toBe(false)
    expect(budget.recordExit(0, false, 1)).toBe(false)
    expect(budget.abnormalExitCount).toBe(0)
  })

  it('caps consecutive pre-ready crashes', () => {
    const budget = new RestartBudget(3, healthyWindow)
    expect(budget.recordExit(1, false, 0)).toBe(true)
    expect(budget.recordExit(1, false, 1)).toBe(true)
    expect(budget.recordExit(1, false, 2)).toBe(false)
    expect(budget.abnormalExitCount).toBe(3)
  })

  it('rolls pre-ready crashes out of the time window', () => {
    const budget = new RestartBudget(3, healthyWindow)
    expect(budget.recordExit(1, false, 0)).toBe(true)
    expect(budget.recordExit(1, false, 1)).toBe(true)
    expect(budget.recordExit(1, false, healthyWindow + 1)).toBe(true)
    expect(budget.abnormalExitCount).toBe(1)
  })

  it('does not reset on READY and caps repeated post-ready crashes', () => {
    const budget = new RestartBudget(3, healthyWindow)
    expect(budget.recordExit(1, false, 0)).toBe(true)
    budget.markReady(1)
    expect(budget.recordExit(2, false, 2)).toBe(true)
    budget.markReady(3)
    expect(budget.recordExit(3, false, 4)).toBe(false)
  })

  it('restores the budget only after sustained healthy operation', () => {
    const budget = new RestartBudget(3, healthyWindow)
    expect(budget.recordExit(1, false, 0)).toBe(true)
    budget.markReady(100)
    expect(budget.decayAfterSustainedHealth(100 + healthyWindow - 1)).toBe(false)
    expect(budget.abnormalExitCount).toBe(1)
    expect(budget.decayAfterSustainedHealth(100 + healthyWindow)).toBe(true)
    expect(budget.abnormalExitCount).toBe(0)
    expect(budget.recordExit(1, false, 100 + healthyWindow + 1)).toBe(true)
  })

  it('retains a reported fatal error over a generic exit message', () => {
    expect(coreExitError(1, 'Provider configuration failed.')).toBe(
      'Provider configuration failed.'
    )
    expect(coreExitError(23, '')).toBe('Core exited with code 23')
    expect(coreExitError(null, '')).toBe('Core exited unexpectedly.')
  })
})

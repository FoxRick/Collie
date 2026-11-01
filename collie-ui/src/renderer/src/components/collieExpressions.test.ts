import { describe, expect, it } from 'vitest'
import { blinkAt, earTwitchAt, expressionTarget, stepExpression, type ExpressionPose } from './collieExpressions'

describe('continuous expression channels', () => {
  it('interrupted expressions ease from the current value without resetting the clock', () => {
    const pose: ExpressionPose = { mouth: .65, lids: 0, ears: .12, paw: 0, time: 10 }
    for (let i = 0; i < 12; i++) stepExpression(pose, 'completion', 1 / 60, i / 60)
    const previous = pose.mouth
    stepExpression(pose, 'error', 1 / 60, 0)
    expect(pose.mouth).toBeLessThan(previous)
    expect(previous - pose.mouth).toBeLessThan(.13)
    expect(pose.time).toBe(10)
  })
  it('settles independently of the display refresh rate', () => {
    const at = (hz: number): ExpressionPose => {
      const pose = { mouth: .65, lids: 0, ears: .12, paw: 0, time: 0 }
      for (let i = 0; i < hz; i++) stepExpression(pose, 'sleepy', 1 / hz, i / hz)
      return pose
    }
    expect(at(30).mouth).toBeCloseTo(at(144).mouth, 8)
    expect(at(60).lids).toBeGreaterThan(.99)
  })
  it('a handoff or input request raises the paw once and settles', () => {
    for (const state of ['delegating', 'waiting'] as const) {
      expect(expressionTarget(state, 0, .6).paw).toBeGreaterThan(.6)
      expect(expressionTarget(state, 0, 3).paw).toBe(0)
    }
  })
  it('blinks briefly rather than holding closed eyes while awake', () => {
    const closed = Array.from({ length: 1700 }, (_, i) => blinkAt(i / 100)).filter(v => v > .5)
    expect(closed.length).toBeGreaterThan(20)
    expect(closed.length).toBeLessThan(1700 * .05)
  })
  it('ear gestures have a visible perk and relax, with quiet time between them', () => {
    const samples = Array.from({ length: 1030 }, (_, i) => earTwitchAt(i / 100))
    expect(Math.max(...samples)).toBeGreaterThan(.7)
    expect(Math.min(...samples)).toBeLessThan(-.3)
    expect(samples.filter(value => Math.abs(value) < .01).length).toBeGreaterThan(700)
  })
})

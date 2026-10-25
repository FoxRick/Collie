import { describe, expect, it } from 'vitest'
import { attentionTarget, rigPoint, type RigPose } from './colliePortraitRig'
const neutral: RigPose = { x: 0, y: 0, joy: 0, focus: 0, concern: 0, rest: 0, time: 0 }
describe('portrait geometry', () => {
  it('has a continuous dead zone and bounded window-wide attention', () => {
    expect(attentionTarget(0.1, 0.1)).toEqual({ x: 0, y: 0 })
    expect(attentionTarget(0.16001, 0).x).toBeLessThan(0.0001)
    expect(Math.hypot(...Object.values(attentionTarget(100, -100)))).toBeCloseTo(1)
  })
  it('moves upward as well as in every other direction without moving the chest root', () => {
    for (let i = 0; i < 8; i++) {
      const angle = i * Math.PI / 4, x = Math.cos(angle), y = Math.sin(angle)
      const point = rigPoint(180, 180, { ...neutral, x, y })
      expect((point.x - 180) * x + (point.y - 180) * y).toBeGreaterThan(5)
      expect(rigPoint(180, 384, { ...neutral, x, y })).toEqual({ x: 180, y: 384 })
    }
  })
  it('keeps every triangle orientation through extreme directions and reactions', () => {
    for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) for (const joy of [0, 1]) {
      const pose = { ...neutral, x, y, joy, concern: 1, rest: 1 }
      for (let py = 0; py < 384; py += 24) for (let px = 0; px < 384; px += 32) {
        const a = rigPoint(px, py, pose), b = rigPoint(px + 32, py, pose), c = rigPoint(px, py + 24, pose)
        expect((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)).toBeGreaterThan(400)
      }
    }
  })
})

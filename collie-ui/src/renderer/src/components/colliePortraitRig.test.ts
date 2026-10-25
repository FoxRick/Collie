import { describe, expect, it } from 'vitest'
import { attentionTarget, rigPoint, type RigPose } from './colliePortraitRig'
const neutral: RigPose = { x: 0, y: 0, mouth: 0, time: 0 }
describe('portrait geometry', () => {
  it('keeps the lower neck stationary at corner targets and gives the ears their own small motion', () => {
    const base = rigPoint(192, 300, neutral)
    for (const x of [-.707, .707]) for (const y of [-.707, .707]) {
      const neck = rigPoint(192, 300, { ...neutral, x, y })
      expect(neck.x).toBeCloseTo(base.x, 6)
      expect(neck.y).toBeCloseTo(base.y, 6)
    }
    expect(Math.abs(rigPoint(78, 50, { ...neutral, time: 2 }).x - rigPoint(78, 50, neutral).x)).toBeGreaterThan(1)
  })
  it('keeps the jaw settle bounded while the original wide painted smile appears', () => {
    const opening = (mouth: number): number => rigPoint(187, 255, { ...neutral, mouth }).y - rigPoint(187, 232, { ...neutral, mouth }).y
    expect(opening(1) - opening(0)).toBeGreaterThan(5)
    expect(opening(1) - opening(0)).toBeLessThan(10)
  })
  it('rotates the snout relative to the eyes instead of translating the face', () => {
    const relativeNose = (x: number): number => rigPoint(184, 183, { ...neutral, x }).x - rigPoint(145, 130, { ...neutral, x }).x
    expect(relativeNose(1) - relativeNose(-1)).toBeGreaterThan(60)
    const relativePitch = (y: number): number => rigPoint(184, 183, { ...neutral, y }).y - rigPoint(145, 130, { ...neutral, y }).y
    expect(relativePitch(1) - relativePitch(-1)).toBeGreaterThan(25)
  })
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
      expect(rigPoint(180, 384, { ...neutral, x, y })).toEqual({ x: 180, y: 400 })
    }
  })
  it('keeps every triangle orientation through extreme directions and reactions', () => {
    for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) for (const mouth of [0, 1]) {
      const radius = Math.max(1, Math.hypot(x, y))
      const pose = { ...neutral, x: x / radius, y: y / radius, mouth }
      for (let py = 0; py < 384; py += 24) for (let px = 0; px < 384; px += 32) {
        const a = rigPoint(px, py, pose), b = rigPoint(px + 32, py, pose), c = rigPoint(px, py + 24, pose)
        expect((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x), JSON.stringify({pose,px,py})).toBeGreaterThan(10)
        const d = rigPoint(px + 32, py + 24, pose)
        expect((d.x - b.x) * (c.y - b.y) - (d.y - b.y) * (c.x - b.x)).toBeGreaterThan(10)
      }
    }
  })
})

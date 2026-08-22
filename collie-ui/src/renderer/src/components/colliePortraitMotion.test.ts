import { describe, expect, it } from 'vitest'
import {
  chaseDirection,
  horizontalFramingOffset,
  quantizePortraitPointer,
  smoothFactor,
  wrapDirectionDelta
} from './colliePortraitMotion'

describe('Collie portrait pointer motion', () => {
  it('uses a central dead zone and the sixteen clockwise directions', () => {
    expect(quantizePortraitPointer(0.05, -0.05)).toBeNull()
    expect(quantizePortraitPointer(0, -1)).toBe(0)
    expect(quantizePortraitPointer(1, 0)).toBe(4)
    expect(quantizePortraitPointer(0, 1)).toBe(8)
    expect(quantizePortraitPointer(-1, 0)).toBe(12)
  })

  it('computes the shortest signed wrap delta across the 16-way circle', () => {
    expect(wrapDirectionDelta(0, 4)).toBe(4)
    expect(wrapDirectionDelta(4, 0)).toBe(-4)
    // Forward across zero: 15.9 → 0.1 is a +0.2 step.
    expect(wrapDirectionDelta(15.9, 0.1)).toBeCloseTo(0.2, 5)
    // Backward across zero: 0.1 → 15.9 is a -0.2 step.
    expect(wrapDirectionDelta(0.1, 15.9)).toBeCloseTo(-0.2, 5)
    expect(wrapDirectionDelta(8, 8)).toBe(0)
  })

  it('chases one continuous direction value without snapping across the face', () => {
    const wrap = (value: number): number => ((value % 16) + 16) % 16
    // Full lerp reaches the target exactly (and wraps past the circle seam).
    expect(chaseDirection(0, 4, 1)).toBe(4)
    expect(wrap(chaseDirection(15.9, 0.1, 1))).toBeCloseTo(0.1, 5)
    expect(wrap(chaseDirection(0.1, 15.9, 1))).toBeCloseTo(15.9, 5)
    // Partial lerp moves toward the target the short way, never the long way.
    expect(chaseDirection(0, 4, 0.5)).toBe(2)
    expect(chaseDirection(15, 0.5, 0.5)).toBeCloseTo(15.75, 5)
    // Exactly 8 away is a tie; the delta resolves backward (-8).
    expect(chaseDirection(0, 8, 0.5)).toBe(-4)
  })

  it('exponentially smooths a rate over a dt frame', () => {
    expect(smoothFactor(9, 0)).toBe(0)
    expect(smoothFactor(9, 1)).toBeCloseTo(0.9999, 4)
    expect(smoothFactor(9, 0.016)).toBeGreaterThan(0)
    expect(smoothFactor(9, 0.016)).toBeLessThan(1)
  })

  it('centers content horizontally and clamps it inside the canvas', () => {
    const SIZE = 384
    // Symmetric content: no offset needed.
    expect(horizontalFramingOffset(0.3, 0.7, 200, 1, SIZE)).toBeCloseTo(0, 5)
    // Content shifted right within the cell: dx pulls it back to center.
    const rightShifted = horizontalFramingOffset(0.5, 0.8, 200, 1, SIZE)
    expect(rightShifted).toBeLessThan(0)
    expect(rightShifted).toBeCloseTo((0.5 - 0.65) * 200, 5)
    // Content shifted left: dx pushes it right.
    const leftShifted = horizontalFramingOffset(0.15, 0.45, 200, 1, SIZE)
    expect(leftShifted).toBeGreaterThan(0)
    // Clamp: a heavily off-center but narrow face stays fully visible.
    const clamped = horizontalFramingOffset(0.02, 0.12, 300, 1.4, SIZE)
    const contentHalf = ((0.12 - 0.02) * 300 * 1.4) / 2
    expect(clamped).toBeGreaterThanOrEqual(contentHalf - SIZE / 2)
    // No clamp when content is wider than the canvas — stays centered.
    const wide = horizontalFramingOffset(0.05, 0.99, 300, 1.4, SIZE)
    expect(wide).toBeCloseTo((0.5 - 0.52) * 300 * 1.4, 5)
  })
})

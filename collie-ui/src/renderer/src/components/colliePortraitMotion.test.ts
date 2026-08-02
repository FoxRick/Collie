import { describe, expect, it } from 'vitest'
import { quantizePortraitPointer, stepPortraitDirection } from './colliePortraitMotion'

describe('Collie portrait pointer motion', () => {
  it('uses a central dead zone and the sixteen clockwise directions', () => {
    expect(quantizePortraitPointer(0.1, -0.1)).toBeNull()
    expect(quantizePortraitPointer(0, -1)).toBe(0)
    expect(quantizePortraitPointer(1, 0)).toBe(4)
    expect(quantizePortraitPointer(0, 1)).toBe(8)
    expect(quantizePortraitPointer(-1, 0)).toBe(12)
  })

  it('moves one neighbouring direction at a time, including across zero', () => {
    expect(stepPortraitDirection(0, 4)).toBe(1)
    expect(stepPortraitDirection(4, 0)).toBe(3)
    expect(stepPortraitDirection(15, 1)).toBe(0)
    expect(stepPortraitDirection(1, 15)).toBe(0)
  })
})

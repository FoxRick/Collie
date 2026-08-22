// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { countdownLabel } from './countdown'

function daysFromNowAt(hour: number, days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  date.setHours(hour, 30, 0, 0)
  return date.toISOString()
}

describe('countdownLabel', () => {
  it('labels today, tomorrow, and the coming week', () => {
    expect(countdownLabel(daysFromNowAt(9, 0))).toBe('today!')
    expect(countdownLabel(daysFromNowAt(23, 1))).toBe('tomorrow')
    expect(countdownLabel(daysFromNowAt(12, 3))).toBe('in 3 days')
    expect(countdownLabel(daysFromNowAt(12, 6))).toBe('in 6 days')
  })

  it('marks overdue and caps at next week', () => {
    expect(countdownLabel(daysFromNowAt(1, -1))).toBe('overdue')
    expect(countdownLabel(daysFromNowAt(12, 7))).toBe('next week')
    expect(countdownLabel(daysFromNowAt(12, 20))).toBeNull()
  })

  it('handles malformed input', () => {
    expect(countdownLabel('not a date')).toBeNull()
  })
})

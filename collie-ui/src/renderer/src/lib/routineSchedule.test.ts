import { describe, expect, it } from 'vitest'
import { routineScheduleLabel } from './routineSchedule'

describe('routine recurrence labels', () => {
  const loop = { id: 'a', name: 'Review', enabled: 1, schedule: '08:00', timezone: 'Asia/Shanghai' }
  it('shows weekdays rather than the lossy daily legacy field', () => {
    expect(routineScheduleLabel({ ...loop, schedule_json: JSON.stringify({kind:'weekdays',time:'08:00',timezone:'Asia/Shanghai'}) }))
      .toBe('Weekdays at 8 am · Asia/Shanghai')
  })
  it('keeps the selected days and timezone of a multi-day routine', () => {
    expect(routineScheduleLabel({...loop, schedule_json:{kind:'weekly',time:'17:30',timezone:'America/New_York',days:['MON','FRI']}}))
      .toBe('Monday, Friday at 5:30 pm · America/New_York')
  })
  it('renders monthly and one-time structured schedules', () => {
    expect(routineScheduleLabel({...loop,schedule_json:{kind:'monthly',time:'09:00',timezone:'UTC',day:31}})).toContain('Day 31')
    expect(routineScheduleLabel({...loop,schedule_json:{kind:'once',time:'09:00',timezone:'UTC',date:'2026-12-25'}})).toContain('Once on 2026-12-25')
  })
})

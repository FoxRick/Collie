import type { CollieAutomation, RoutineSchedule } from './ipc'

export function routineSchedule(loop: CollieAutomation): RoutineSchedule | null {
  if (!loop.schedule_json) return null
  try {
    const value = typeof loop.schedule_json === 'string' ? JSON.parse(loop.schedule_json) : loop.schedule_json
    return value && typeof value.time === 'string' && typeof value.kind === 'string' ? value : null
  } catch {
    return null
  }
}

/** Prefer the same structured recurrence the scheduler actually executes. */
export function routineScheduleLabel(loop: CollieAutomation): string {
  const value = routineSchedule(loop)
  const legacy = (loop.schedule || '').trim().split(/\s+/)
  const clock = value?.time || legacy.at(-1) || ''
  const match = /^(\d{1,2}):(\d{2})$/.exec(clock)
  if (!match) return loop.schedule || 'Not scheduled'
  const hours = Number(match[1])
  const minutes = Number(match[2])
  const time = `${hours % 12 || 12}${minutes ? ':' + match[2] : ''} ${hours < 12 ? 'am' : 'pm'}`
  const days: Record<string, string> = { MON: 'Monday', TUE: 'Tuesday', WED: 'Wednesday', THU: 'Thursday', FRI: 'Friday', SAT: 'Saturday', SUN: 'Sunday' }
  const zone = value?.timezone || loop.timezone
  let text: string
  if (value?.kind === 'weekdays') text = `Weekdays at ${time}`
  else if (value?.kind === 'once') text = `Once on ${value.date} at ${time}`
  else if (value?.kind === 'monthly') text = `Day ${value.day} of the month at ${time}`
  else if (value?.kind === 'weekly') text = `${(value.days || []).map(day => days[day.toUpperCase()] || day).join(', ')} at ${time}`
  else if (!value && legacy.length === 2 && /^\d+$/.test(legacy[0])) text = `Day ${Number(legacy[0])} of the month at ${time}`
  else if (!value && legacy.length === 2 && days[legacy[0].toUpperCase()]) text = `${days[legacy[0].toUpperCase()]}s at ${time}`
  else text = `Every day at ${time}`
  return zone ? `${text} · ${zone}` : text
}

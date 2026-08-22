/** Friendly countdown labels for reminder due times ("today!", "in 3 days"). */
export function countdownLabel(dueAt: string, now: Date = new Date()): string | null {
  const due = new Date(dueAt)
  if (Number.isNaN(due.getTime())) return null

  const startOfToday = new Date(now)
  startOfToday.setHours(0, 0, 0, 0)
  const startOfDueDay = new Date(due)
  startOfDueDay.setHours(0, 0, 0, 0)

  const dayDiff = Math.round((startOfDueDay.getTime() - startOfToday.getTime()) / 86_400_000)
  if (dayDiff < 0) return 'overdue'
  if (dayDiff === 0) return 'today!'
  if (dayDiff === 1) return 'tomorrow'
  if (dayDiff < 7) return `in ${dayDiff} days`
  if (dayDiff < 14) return 'next week'
  return null // far-away reminders don't need a chip
}

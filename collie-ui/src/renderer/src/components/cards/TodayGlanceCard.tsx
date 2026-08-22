interface GlanceWeather {
  location?: string
  icon?: string
  temp?: string
  condition?: string
  high?: string
  low?: string
  rain_chance?: number
}

interface GlanceReminder {
  text: string
  due_at: string | undefined
}

interface Props {
  data: Record<string, unknown>
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function friendlyDue(dueAt: string): string {
  const parsed = new Date(dueAt)
  if (Number.isNaN(parsed.getTime())) return dueAt
  return parsed.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/**
 * "Today at a glance" — one quiet card under the Morning Briefing: today's
 * weather row plus what's coming up in the next 24 hours. Sections drop out
 * independently when there is no data; the card renders only when at least
 * one section has something to show.
 */
export default function TodayGlanceCard({ data }: Props): React.JSX.Element | null {
  const weatherRaw = data.weather as Record<string, unknown> | undefined
  const weather: GlanceWeather = weatherRaw ?? {}
  const temp = asString(weather.temp)
  const condition = asString(weather.condition)
  const icon = asString(weather.icon) || '⛅'
  const high = asString(weather.high)
  const low = asString(weather.low)
  const rainChance = typeof weather.rain_chance === 'number' ? weather.rain_chance : null
  const location = asString(weather.location)

  const remindersRaw = Array.isArray(data.reminders) ? data.reminders : []
  const reminders: GlanceReminder[] = []
  for (const item of remindersRaw) {
    if (reminders.length >= 3) break
    const record = item as Record<string, unknown>
    const text = asString(record?.text)
    if (!text || text.trim() === '') continue
    reminders.push({ text, due_at: asString(record?.due_at) })
  }

  const dateLabel = asString(data.date) ?? ''
  const hasWeather = temp !== undefined && condition !== undefined
  const hasReminders = reminders.length > 0

  if (!hasWeather && !hasReminders) return null

  return (
    <section
      aria-label="Today at a glance"
      className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}
    >
      <div className="mb-3 flex items-center gap-2">
        <span className="text-lg" aria-hidden="true">🌅</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          Today at a glance
        </span>
        {dateLabel && (
          <span className="ml-auto text-xs" style={{ color: 'var(--collie-paw)' }}>
            {dateLabel}
          </span>
        )}
      </div>

      {hasWeather && (
        <div
          className="mb-3 flex items-center gap-3 rounded-xl p-3"
          style={{ background: 'var(--collie-bone)' }}
        >
          <span className="text-3xl" aria-hidden="true">{icon}</span>
          <div className="min-w-0">
            <div className="text-xl font-bold leading-tight" style={{ color: 'var(--collie-nose)' }}>
              {temp}°C{' '}
              <span className="text-sm font-normal" style={{ color: 'var(--collie-paw)' }}>
                {condition}
              </span>
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs" style={{ color: 'var(--collie-paw)' }}>
              {high != null && low != null && (
                <span>
                  H {high}° · L {low}°
                </span>
              )}
              {rainChance !== null && rainChance > 20 && <span>☂️ {rainChance}% rain</span>}
              {location && <span className="truncate">{location}</span>}
            </div>
          </div>
        </div>
      )}

      {hasReminders && (
        <div className="space-y-1.5">
          <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--collie-paw)' }}>
            Coming up
          </div>
          {reminders.map((item, index) => (
            <div
              key={index}
              className="flex items-center justify-between gap-3 rounded-xl px-3 py-2"
              style={{ background: 'var(--collie-bone)' }}
            >
              <span className="min-w-0 truncate text-sm" style={{ color: 'var(--collie-nose)' }}>
                {item.text}
              </span>
              <span className="shrink-0 text-xs font-medium" style={{ color: 'var(--collie-amber)' }}>
                {item.due_at ? friendlyDue(item.due_at) : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

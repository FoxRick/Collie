interface HabitRow {
  key: string
  label: string
  icon: string
  color: string
  /** One value per day, oldest → newest (7 entries). Null = nothing logged. */
  days: Array<number | null>
}

function parseDays(value: unknown): Array<number | null> {
  if (!Array.isArray(value)) return []
  return value.slice(0, 7).map((entry) =>
    typeof entry === 'number' && Number.isFinite(entry) && entry >= 0 ? entry : null
  )
}

function Dot({ filled, color }: { filled: boolean; color: string }): React.JSX.Element {
  return (
    <span
      className="h-2.5 w-2.5 rounded-full"
      style={{ background: filled ? color : 'var(--collie-fur)' }}
      aria-hidden="true"
    />
  )
}

/**
 * "Today at a glance" for habits: one row per tracked metric with a
 * GitHub-style 7-day dot grid — green dot = logged that day. Data comes from
 * the health tool's ``habits`` array; the legacy ``grid`` intensity strip
 * still renders when present.
 */
export default function HealthStreaks({
  habits,
  grid
}: {
  habits: unknown
  grid?: unknown
}): React.JSX.Element | null {
  const rowsRaw = Array.isArray(habits) ? habits : []
  const rows: HabitRow[] = []
  for (const raw of rowsRaw) {
    if (raw === null || typeof raw !== 'object') continue
    const record = raw as Record<string, unknown>
    const key = typeof record.key === 'string' ? record.key : ''
    const label = typeof record.label === 'string' ? record.label : ''
    if (!key || !label) continue
    const days = parseDays(record.days)
    if (!days.some((day) => day !== null)) continue // never show an all-empty habit row
    const icon = typeof record.icon === 'string' ? record.icon : '•'
    const color = typeof record.color === 'string' ? record.color : 'var(--collie-grass)'
    rows.push({ key, label, icon, color, days })
  }
  const weekGrid = Array.isArray(grid) ? (grid as number[]) : []
  if (rows.length === 0 && weekGrid.length === 0) return null

  return (
    <section aria-label="Habit streaks" className="mt-3 space-y-2">
      {rows.map((row) => (
        <div key={row.key} className="flex items-center gap-2">
          <span className="w-5 text-center text-sm" aria-hidden="true">{row.icon}</span>
          <span className="w-12 shrink-0 text-xs" style={{ color: 'var(--collie-paw)' }}>
            {row.label}
          </span>
          <span className="flex flex-1 justify-end gap-1.5" role="img" aria-label={`${row.label} last 7 days`}>
            {row.days.map((day, index) => (
              <Dot key={index} filled={day !== null} color={row.color} />
            ))}
          </span>
        </div>
      ))}
      {weekGrid.length > 0 && (
        <div className="flex gap-1 pt-1" aria-hidden="true">
          {weekGrid.map((value, index) => (
            <div
              key={index}
              className="h-1.5 flex-1 rounded-sm"
              style={{
                background:
                  value > 0
                    ? `color-mix(in srgb, var(--collie-grass) ${value * 25}%, var(--collie-fur))`
                    : 'var(--collie-fur)'
              }}
            />
          ))}
        </div>
      )}
    </section>
  )
}

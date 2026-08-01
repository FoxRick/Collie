interface Props {
  data: Record<string, unknown>
}

export default function CalendarCard({ data }: Props): React.JSX.Element {
  const events = data.events as Array<Record<string, unknown>> | undefined
  const dateRange = (data.date_range as string) || ''

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">📅</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          {dateRange || 'Your Calendar'}
        </span>
      </div>
      {events && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event, i) => (
            <div key={i} className="flex items-start gap-3 rounded-xl p-2"
              style={{ background: 'var(--collie-bone)' }}>
              <div className="w-10 text-center">
                <div className="text-xs font-bold" style={{ color: 'var(--collie-amber)' }}>
                  {String(event.time || '')}
                </div>
              </div>
              <div>
                <div className="text-sm font-semibold" style={{ color: 'var(--collie-nose)' }}>
                  {event.title as string}
                </div>
                {(event.location as string) && (
                  <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>
                    {event.location as string}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-sm" style={{ color: 'var(--collie-paw)' }}>
          No events for this period.
        </div>
      )}
      {events && events.length > 0 && (
        <button className="mt-3 w-full rounded-xl py-2 text-sm font-medium"
          style={{ color: 'var(--collie-amber)', background: 'var(--collie-bone)' }}>
          View Full Day →
        </button>
      )}
    </div>
  )
}

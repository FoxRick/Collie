interface Props {
  data: Record<string, unknown>
}

export default function TravelCard({ data }: Props): React.JSX.Element {
  const days = data.days as Array<Record<string, unknown>> | undefined
  const destination = (data.destination as string) || ''

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-lg">✈️</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          {destination || 'Your Trip'}
        </span>
      </div>
      {days && days.length > 0 ? (
        <div className="space-y-3">
          {days.map((day, i) => (
            <div key={i} className="rounded-xl p-3" style={{ background: 'var(--collie-bone)' }}>
              <div className="mb-1 text-xs font-bold" style={{ color: 'var(--collie-amber)' }}>
                {day.label as string}
              </div>
              <div className="text-sm" style={{ color: 'var(--collie-nose)' }}>
                {String(day.summary || '')}
              </div>
              {(day.activities as Array<Record<string, unknown>> | undefined) && (day.activities as Array<Record<string, unknown>>).length > 0 && (
                <div className="mt-2 space-y-1">
                  {(day.activities as Array<Record<string, unknown>>).map((act, j) => (
                    <div key={j} className="flex items-center gap-2 text-xs">
                      <span>{act.icon as string}</span>
                      <span style={{ color: 'var(--collie-paw)' }}>{act.time as string}</span>
                      <span style={{ color: 'var(--collie-nose)' }}>{act.title as string}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-sm" style={{ color: 'var(--collie-paw)' }}>
          Plan your trip details here.
        </div>
      )}
    </div>
  )
}

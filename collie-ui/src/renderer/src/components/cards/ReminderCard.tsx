import { useState } from 'react'

interface Props {
  data: Record<string, unknown>
}

export default function ReminderCard({ data }: Props): React.JSX.Element {
  const items = data.items as Array<Record<string, unknown>> | undefined
  const [done, setDone] = useState<Set<number>>(new Set())

  if (!items || items.length === 0) {
    return (
      <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
        style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
        <div className="flex items-center gap-2">
          <span className="text-lg">⏰</span>
          <span className="text-sm" style={{ color: 'var(--collie-paw)' }}>
            No reminders right now — you're all caught up!
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">⏰</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          Reminders
        </span>
      </div>
      <div className="space-y-2">
        {items.map((item, i) => (
          <div key={i} className="flex items-center gap-3 rounded-xl p-3"
            style={{
              background: done.has(i) ? 'var(--collie-fur)' : 'var(--collie-bone)',
              opacity: done.has(i) ? 0.6 : 1
            }}>
            <button
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 text-xs"
              style={{
                borderColor: done.has(i) ? 'var(--collie-grass)' : 'var(--collie-paw)',
                background: done.has(i) ? 'var(--collie-grass)' : 'transparent',
                color: done.has(i) ? 'white' : 'transparent'
              }}
              onClick={() => setDone((prev) => {
                const next = new Set(prev)
                if (next.has(i)) next.delete(i)
                else next.add(i)
                return next
              })}
            >
              ✓
            </button>
            <div className="flex-1">
              <div className={`text-sm ${done.has(i) ? 'line-through' : ''}`}
                style={{ color: 'var(--collie-nose)' }}>
                {item.text as string}
              </div>
              <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>
                {item.due_at as string}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

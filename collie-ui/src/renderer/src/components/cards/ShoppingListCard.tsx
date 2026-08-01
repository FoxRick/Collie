import { useState } from 'react'

interface Props {
  data: Record<string, unknown>
}

export default function ShoppingListCard({ data }: Props): React.JSX.Element {
  const categories = data.categories as Record<string, Array<Record<string, unknown>>> | undefined
  const [checked, setChecked] = useState<Set<string>>(new Set())

  if (!categories || Object.keys(categories).length === 0) {
    return <div />
  }

  const toggle = (id: string) => setChecked((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">🛒</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          Shopping List
        </span>
      </div>
      {Object.entries(categories).map(([cat, items]) => (
        <div key={cat} className="mb-3">
          <div className="mb-1 text-xs font-semibold" style={{ color: 'var(--collie-paw)' }}>
            {cat}
          </div>
          <div className="space-y-1">
            {items.map((item, i) => {
              const id = `${cat}-${i}`
              return (
                <div key={id} className="flex items-center gap-2 rounded-lg px-2 py-1"
                  style={{ background: item.done ? 'var(--collie-fur)' : 'transparent' }}>
                  <button
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded border text-xs"
                    style={{
                      borderColor: checked.has(id) ? 'var(--collie-grass)' : 'var(--collie-paw)',
                      background: checked.has(id) ? 'var(--collie-grass)' : 'transparent',
                      color: checked.has(id) ? 'white' : 'transparent'
                    }}
                    onClick={() => toggle(id)}
                  >
                    ✓
                  </button>
                  <span className={`text-sm ${checked.has(id) ? 'line-through' : ''}`}
                    style={{ color: 'var(--collie-nose)' }}>
                    {item.name as string}
                  </span>
                {item.quantity != null && (
                  <span className="ml-auto text-xs" style={{ color: 'var(--collie-paw)' }}>
                    {String(item.quantity)}
                  </span>
                )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

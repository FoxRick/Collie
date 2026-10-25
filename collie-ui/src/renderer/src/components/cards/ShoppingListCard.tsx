import { useEffect, useRef, useState } from 'react'
import { collieClient } from '../../lib/ipc'

interface Props {
  data: Record<string, unknown>
}

export default function ShoppingListCard({ data }: Props): React.JSX.Element {
  const categories = data.categories as Record<string, Array<Record<string, unknown>>> | undefined
  const initialChecked = (): Set<string> => new Set(Object.values(categories || {}).flat()
    .filter((item) => item.done && typeof item.id === 'string').map((item) => item.id as string))
  const [checked, setChecked] = useState<Set<string>>(initialChecked)
  const [saving, setSaving] = useState<Set<string>>(new Set())
  const pending = useRef(new Set<string>())
  const [error, setError] = useState('')
  useEffect(() => { setChecked(initialChecked()) }, [data])

  if (!categories || Object.keys(categories).length === 0) {
    return <div />
  }

  const toggle = async (id: string): Promise<void> => {
    if (pending.current.has(id)) return
    const nextChecked = !checked.has(id)
    pending.current.add(id)
    setSaving(new Set(pending.current))
    setError('')
    try {
      await collieClient.command('set_shopping_item_checked', { item_id: id, checked: nextChecked })
      setChecked((prev) => {
        const next = new Set(prev)
        if (nextChecked) next.add(id)
        else next.delete(id)
        return next
      })
    } catch {
      setError("I couldn't save that checkmark. Please try again.")
    } finally {
      pending.current.delete(id)
      setSaving(new Set(pending.current))
    }
  }

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
              const id = typeof item.id === 'string' ? item.id : ''
              return (
                <div key={id || `${cat}-${i}`} className="flex items-center gap-2 rounded-lg px-2 py-1"
                  style={{ background: checked.has(id) ? 'var(--collie-fur)' : 'transparent' }}>
                  <button
                    type="button"
                    role="checkbox"
                    aria-label={`Bought ${item.name as string}`}
                    aria-checked={checked.has(id)}
                    disabled={!id || saving.has(id)}
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded border text-xs"
                    style={{
                      borderColor: checked.has(id) ? 'var(--collie-grass)' : 'var(--collie-paw)',
                      background: checked.has(id) ? 'var(--collie-grass)' : 'transparent',
                      color: checked.has(id) ? 'white' : 'transparent'
                    }}
                    onClick={() => { void toggle(id) }}
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
      {error && <p role="alert" className="text-sm" style={{ color: 'var(--collie-text)' }}>{error}</p>}
    </div>
  )
}

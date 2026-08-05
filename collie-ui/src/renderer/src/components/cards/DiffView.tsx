import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

interface Props {
  diff: string
  /** Default collapsed state (only shows a summary line until opened). */
  defaultCollapsed?: boolean
  label?: string
}

/** Render a unified diff as friendly, color-coded lines. */
export default function DiffView({ diff, defaultCollapsed = true, label }: Props): React.JSX.Element {
  const [open, setOpen] = useState(!defaultCollapsed)

  const { lines, added, removed } = useMemo(() => {
    const raw = (diff || '').split('\n')
    const lines = raw.filter((line) => !line.startsWith('+++') && !line.startsWith('---') && !line.startsWith('@@'))
    const added = lines.filter((line) => line.startsWith('+')).length
    const removed = lines.filter((line) => line.startsWith('-')).length
    return { lines, added, removed }
  }, [diff])

  if (!diff) return <div />

  const summary = label ?? `What changed: ${added} line${added === 1 ? '' : 's'} added, ${removed} removed`

  return (
    <div className="diff-view">
      <button
        type="button"
        className="diff-toggle"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span>{summary}</span>
      </button>
      {open && (
        <pre className="diff-text">
          {lines.map((line, index) => {
            const isAdded = line.startsWith('+')
            const isRemoved = line.startsWith('-')
            const cls = isAdded ? 'is-added' : isRemoved ? 'is-removed' : ''
            return (
              <span key={index} className={cls}>
                {line === '' ? ' ' : line}
                {'\n'}
              </span>
            )
          })}
        </pre>
      )}
    </div>
  )
}

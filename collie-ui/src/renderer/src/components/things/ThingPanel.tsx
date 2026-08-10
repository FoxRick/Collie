import { useState } from 'react'
import { ChevronLeft, X } from 'lucide-react'
import type { Thing } from '../../lib/ipc'
import { useT } from '../../lib/i18n'
import ThingCard from './ThingCard'
import ThingPreview from './ThingPreview'

interface Props {
  things: Thing[]
  unseenIds: Set<string>
  onClose: () => void
  onOpen: (thing: Thing) => void
  onSaveCopy: (thing: Thing) => void
  onShowInFolder: (thing: Thing) => void
}

/**
 * Right-side "Your things" panel: everything Collie has made for this chat,
 * newest first. Clicking a card opens its in-panel preview.
 */
export default function ThingPanel({
  things,
  unseenIds,
  onClose,
  onOpen,
  onSaveCopy,
  onShowInFolder
}: Props): React.JSX.Element {
  const t = useT()
  const [selected, setSelected] = useState<Thing | null>(null)

  const handleClose = (): void => {
    setSelected(null)
    onClose()
  }

  return (
    <aside className="things-panel" aria-label={t('things.title')}>
      <header className="things-panel-head">
        {selected ? (
          <button
            type="button"
            className="things-panel-back"
            onClick={() => setSelected(null)}
            aria-label="Back"
          >
            <ChevronLeft size={16} />
          </button>
        ) : null}
        <b>{selected ? selected.title : t('things.title')}</b>
        <button
          type="button"
          className="things-panel-close"
          onClick={handleClose}
          aria-label="Close"
        >
          <X size={16} />
        </button>
      </header>

      {selected ? (
        <ThingPreview thing={selected} onOpen={onOpen} />
      ) : things.length === 0 ? (
        <div className="things-panel-empty">{t('things.empty')}</div>
      ) : (
        <div className="things-panel-list">
          {things.map((thing) => (
            <ThingCard
              key={thing.id}
              thing={thing}
              unseen={unseenIds.has(thing.id)}
              onSelect={setSelected}
              onOpen={onOpen}
              onSaveCopy={onSaveCopy}
              onShowInFolder={onShowInFolder}
            />
          ))}
        </div>
      )}
    </aside>
  )
}

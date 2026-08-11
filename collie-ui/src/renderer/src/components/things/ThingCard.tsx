import { useEffect, useRef, useState } from 'react'
import { Download, ExternalLink, FolderOpen, MoreVertical, X } from 'lucide-react'
import type { Thing } from '../../lib/ipc'
import { useT, type TranslationKey } from '../../lib/i18n'
import { formatThingSize, formatThingTime, thingKindIcon, kindLabelKey } from './thingMeta'

interface Props {
  thing: Thing
  unseen: boolean
  onSelect: (thing: Thing) => void
  onOpen: (thing: Thing) => void
  onSaveCopy: (thing: Thing) => void
  onShowInFolder: (thing: Thing) => void
}

/** One deliverable row in the "Your things" panel. */
export default function ThingCard({
  thing,
  unseen,
  onSelect,
  onOpen,
  onSaveCopy,
  onShowInFolder
}: Props): React.JSX.Element {
  const t = useT()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const close = (event: PointerEvent): void => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const kindKey = kindLabelKey(thing.kind) as TranslationKey

  return (
    <div className={`thing-card ${unseen ? 'is-new' : ''}`}>
      <button
        type="button"
        className="thing-card-main"
        onClick={() => onSelect(thing)}
        title={thing.title}
      >
        <span className="thing-card-icon" aria-hidden="true">
          {thingKindIcon(thing.kind)}
        </span>
        <span className="thing-card-copy">
          <b>{thing.title}</b>
          <small>
            {t(kindKey)}
            {thing.size_bytes > 0 ? ` · ${formatThingSize(thing.size_bytes)}` : ''}
            {thing.created_at ? ` · ${formatThingTime(thing.created_at)}` : ''}
          </small>
        </span>
        {unseen && <span className="thing-new-dot" aria-label={t('things.newThing')} />}
      </button>

      <div className="thing-card-actions" ref={menuRef}>
        <button
          type="button"
          className="thing-card-menu-btn"
          onClick={() => setMenuOpen((open) => !open)}
          aria-label={`${thing.title} — ${t('things.title')} menu`}
          aria-expanded={menuOpen}
        >
          <MoreVertical size={15} />
        </button>
        {menuOpen && (
          <div className="thing-menu" role="menu">
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onOpen(thing) }}>
              <ExternalLink size={14} />
              {t('things.open')}
            </button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onSaveCopy(thing) }}>
              <Download size={14} />
              {t('things.saveCopy')}
            </button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onShowInFolder(thing) }}>
              <FolderOpen size={14} />
              {t('things.showInFolder')}
            </button>
            <div className="thing-menu-close" role="none">
              <button type="button" role="menuitem" onClick={() => setMenuOpen(false)}>
                <X size={14} />
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

import { Package } from 'lucide-react'
import { useT } from '../../lib/i18n'

interface Props {
  unseen: number
  open: boolean
  onClick: () => void
}

/** Top-right header button that opens the "Your things" panel. */
export default function ThingsToggle({ unseen, open, onClick }: Props): React.JSX.Element {
  const t = useT()
  return (
    <button
      type="button"
      className={`things-toggle ${open ? 'is-active' : ''}`}
      onClick={onClick}
      title={t('things.title')}
      aria-label={t('things.title')}
      aria-expanded={open}
    >
      <Package size={17} />
      {unseen > 0 && (
        <span
          className="things-toggle-badge"
          aria-label={t('things.unseen', { count: unseen })}
        >
          {unseen > 9 ? '9+' : unseen}
        </span>
      )}
    </button>
  )
}

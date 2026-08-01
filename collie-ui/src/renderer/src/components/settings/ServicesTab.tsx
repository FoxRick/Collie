import { ArrowRight, Plug } from 'lucide-react'

export default function ServicesTab({
  onOpenConnectors
}: {
  onOpenConnectors: () => void
}): React.JSX.Element {
  return (
    <section className="settings-card">
      <div className="settings-card-icon">
        <Plug size={19} />
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="font-medium">Services moved to Connections</h3>
        <p className="mt-1 text-sm" style={{ color: 'var(--collie-paw)' }}>
          Add, test, rename, reconnect, and remove connected accounts from the main
          Connections directory.
        </p>
      </div>
      <button
        type="button"
        className="settings-button is-primary flex items-center gap-1.5"
        onClick={onOpenConnectors}
      >
        Open Connections <ArrowRight size={14} />
      </button>
    </section>
  )
}

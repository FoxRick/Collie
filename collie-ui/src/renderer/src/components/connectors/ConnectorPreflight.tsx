import type { ConnectorCatalogItem } from '../../lib/ipc'
import ConnectorIcon from './ConnectorIcon'

export default function ConnectorPreflight({
  connector,
  onCancel,
  onContinue
}: {
  connector: ConnectorCatalogItem
  onCancel: () => void
  onContinue: () => void
}): React.JSX.Element {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="connector-preflight-title"
        className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl"
      >
        <div className="flex items-center gap-3">
          <ConnectorIcon providerId={connector.id} name={connector.name} />
          <div>
            <h2 id="connector-preflight-title" className="font-semibold">
              Connect {connector.name}
            </h2>
            <p className="text-xs" style={{ color: 'var(--collie-paw)' }}>
              Sign in with the official provider
            </p>
          </div>
        </div>
        <p className="mt-4 text-sm leading-6">
          Collie will be able to {connector.permissions.join(', ')}. Changes still ask
          for approval, and important actions always require confirmation.
        </p>
        <p className="mt-3 text-xs leading-5" style={{ color: 'var(--collie-paw)' }}>
          Your authorization stays encrypted on this device. Connected content and
          credentials are not routed through an integration aggregator.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button className="rounded-lg border px-4 py-2 text-sm" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="rounded-lg px-4 py-2 text-sm font-semibold text-white"
            style={{ background: 'var(--collie-btn-primary-bg)' }}
            onClick={onContinue}
          >
            Continue to {connector.name}
          </button>
        </div>
      </section>
    </div>
  )
}

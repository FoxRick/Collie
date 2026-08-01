import type { ConnectorCatalogItem } from '../../lib/ipc'
import ConnectorIcon from './ConnectorIcon'

export function connectorIsInFlight(
  status: string
): status is 'authorizing' | 'testing' {
  return status === 'authorizing' || status === 'testing'
}

export default function ConnectorCard({
  connector,
  onConnect
}: {
  connector: ConnectorCatalogItem
  onConnect: (connector: ConnectorCatalogItem) => void
}): React.JSX.Element {
  const inFlight = connectorIsInFlight(connector.status)
  const actionLabel =
    connector.status === 'connected'
      ? 'Connected'
      : connector.status === 'authorizing'
        ? 'Sign-in in progress'
        : connector.status === 'testing'
          ? 'Checking connection'
          : connector.available
            ? 'Connect'
            : 'Coming soon'
  return (
    <article
      className="flex min-h-52 flex-col rounded-2xl border bg-white p-4"
      style={{
        borderColor: 'var(--collie-border)',
        opacity: connector.available ? 1 : 0.62
      }}
    >
      <div className="flex items-start gap-3">
        <ConnectorIcon providerId={connector.id} name={connector.name} />
        <div className="min-w-0">
          <h3 className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
            {connector.name}
          </h3>
          <p className="mt-1 text-xs leading-5" style={{ color: 'var(--collie-paw)' }}>
            {connector.description}
          </p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5" aria-label="Capabilities">
        {connector.capabilities.map((capability) => (
          <span
            key={capability}
            className="rounded-full border px-2 py-1 text-[11px]"
            style={{ borderColor: 'var(--collie-fur)', color: 'var(--collie-paw)' }}
          >
            {capability}
          </span>
        ))}
      </div>
      <div className="mt-auto pt-4">
        {connector.available && connector.status !== 'connected' && !inFlight ? (
          <p className="mb-2 text-[11px] leading-4" style={{ color: 'var(--collie-paw)' }}>
            {connector.status === 'attention' || connector.status === 'failed'
              ? 'Previous connection needs attention.'
              : 'Not connected yet.'}
          </p>
        ) : null}
        {!connector.available && connector.note ? (
          <p className="mb-2 text-[11px] leading-4" style={{ color: 'var(--collie-paw)' }}>
            {connector.note}
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => {
            if (connector.available && connector.status !== 'connected' && !inFlight) {
              onConnect(connector)
            }
          }}
          disabled={!connector.available || connector.status === 'connected' || inFlight}
          className="w-full rounded-lg px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: 'var(--collie-btn-primary-bg)' }}
        >
          {actionLabel}
        </button>
      </div>
    </article>
  )
}

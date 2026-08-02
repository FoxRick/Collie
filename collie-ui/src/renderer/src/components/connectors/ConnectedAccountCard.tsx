import { AlertCircle, CheckCircle2 } from 'lucide-react'
import type { ConnectorConnection } from '../../lib/ipc'
import ConnectorIcon from './ConnectorIcon'

export default function ConnectedAccountCard({
  connection,
  onOpen
}: {
  connection: ConnectorConnection
  onOpen: (connection: ConnectorConnection) => void
}): React.JSX.Element {
  const healthy = connection.status === 'connected'
  const statusLabel =
    connection.status === 'connected'
      ? 'Connected'
      : connection.status === 'auth_required'
        ? 'Sign in again'
        : connection.status === 'testing'
          ? 'Checking connection'
          : connection.status === 'authorizing'
            ? 'Sign-in in progress'
            : connection.status === 'failed'
              ? 'Connection failed'
              : 'Needs attention'
  return (
    <button
      type="button"
      onClick={() => onOpen(connection)}
      className="flex w-full items-center gap-3 rounded-2xl border bg-white p-4 text-left"
      style={{ borderColor: 'var(--collie-border)' }}
    >
      <ConnectorIcon providerId={connection.provider_id} name={connection.provider_name} />
      <span className="min-w-0 flex-1">
        <b className="block truncate text-sm" style={{ color: 'var(--collie-nose)' }}>
          {connection.display_name || connection.provider_name}
        </b>
        <small className="block truncate" style={{ color: 'var(--collie-paw)' }}>
          {connection.account_label || 'Account identity not verified'}
        </small>
      </span>
      <span
        className="flex items-center gap-1 text-xs"
        style={{ color: healthy ? 'var(--collie-grass)' : 'var(--collie-amber)' }}
      >
        {healthy ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
        {statusLabel}
      </span>
    </button>
  )
}

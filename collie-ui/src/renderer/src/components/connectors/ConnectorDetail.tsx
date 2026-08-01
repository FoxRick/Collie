import { ArrowLeft, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { ConnectorConnection } from '../../lib/ipc'
import ConnectorIcon from './ConnectorIcon'
import ConnectorPermissionSelect from './ConnectorPermissionSelect'

export default function ConnectorDetail({
  connection,
  busy,
  onBack,
  onRename,
  onPreference,
  onTest,
  onReconnect,
  onRemove
}: {
  connection: ConnectorConnection
  busy: boolean
  onBack: () => void
  onRename: (name: string) => void
  onPreference: (value: string) => void
  onTest: () => void
  onReconnect: () => void
  onRemove: () => void
}): React.JSX.Element {
  const [name, setName] = useState(connection.display_name || connection.provider_name)
  const preference = connection.tool_policy?._approval_preference || 'important'
  const accountDescription = connection.account_label || 'Account identity not verified'
  const statusDescription =
    connection.status === 'connected'
      ? 'Connected'
      : connection.status === 'testing'
        ? 'Checking connection'
        : connection.status === 'authorizing'
          ? 'Sign-in in progress'
          : connection.status === 'failed'
            ? 'Connection failed'
            : 'Needs attention'
  return (
    <div className="mx-auto max-w-3xl">
      <button className="mb-4 flex items-center gap-1 text-sm" onClick={onBack}>
        <ArrowLeft size={15} /> All connections
      </button>
      <section
        className="rounded-2xl border bg-white p-5"
        style={{ borderColor: 'var(--collie-border)' }}
      >
        <div className="flex items-center gap-3">
          <ConnectorIcon providerId={connection.provider_id} name={connection.provider_name} />
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold">{connection.display_name || connection.provider_name}</h2>
            <p className="truncate text-xs" style={{ color: 'var(--collie-paw)' }}>
              {accountDescription} · {statusDescription}
            </p>
          </div>
        </div>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block font-medium">Connection name</span>
            <span className="flex gap-2">
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border px-3 py-2"
                style={{ borderColor: 'var(--collie-border)' }}
              />
              <button
                disabled={busy || !name.trim()}
                className="rounded-lg border px-3 disabled:opacity-50"
                onClick={() => onRename(name.trim())}
              >
                Save
              </button>
            </span>
          </label>
          <ConnectorPermissionSelect value={preference} onChange={onPreference} />
        </div>
        <div className="mt-5 rounded-xl border p-4" style={{ borderColor: 'var(--collie-fur)' }}>
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck size={16} /> What Collie can access
          </div>
          <ul className="mt-2 list-inside list-disc text-sm leading-6" style={{ color: 'var(--collie-paw)' }}>
            {(connection.permissions || []).map((permission) => (
              <li key={permission}>{permission}</li>
            ))}
          </ul>
          <p className="mt-3 text-xs" style={{ color: 'var(--collie-paw)' }}>
            Route: {connection.route}. Credentials are encrypted on this device.
          </p>
          <p className="mt-1 text-xs" style={{ color: 'var(--collie-paw)' }}>
            Last verified:{' '}
            {connection.last_verified_at
              ? new Date(connection.last_verified_at).toLocaleString()
              : 'Not checked yet'}
          </p>
        </div>
        {connection.last_error_message ? (
          <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
            {connection.last_error_message}
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            disabled={busy}
            className="flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm disabled:opacity-50"
            onClick={onTest}
          >
            <RefreshCw size={14} /> Test connection
          </button>
          <button
            disabled={busy}
            className="rounded-lg border px-3 py-2 text-sm disabled:opacity-50"
            onClick={onReconnect}
          >
            Reconnect / Change account
          </button>
          <button
            disabled={busy}
            className="ml-auto flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700 disabled:opacity-50"
            onClick={onRemove}
          >
            <Trash2 size={14} /> Remove connection
          </button>
        </div>
      </section>
    </div>
  )
}

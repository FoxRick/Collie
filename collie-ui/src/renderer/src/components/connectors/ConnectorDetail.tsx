import { ArrowLeft, RefreshCw, ShieldCheck, Trash2, Wrench } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { ConnectorConnection } from '../../lib/ipc'
import type { ConnectorToolInfo } from '../../../../shared/connectors'
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
  onReconnectWithSecret,
  onInspectTools,
  onSaveTools,
  onRemove
}: {
  connection: ConnectorConnection
  busy: boolean
  onBack: () => void
  onRename: (name: string) => void
  onPreference: (value: string) => void
  onTest: () => void
  onReconnect: () => void
  onReconnectWithSecret: (secret: string) => Promise<void>
  onInspectTools: (query?: string) => Promise<ConnectorToolInfo[]>
  onSaveTools: (enabledTools: string[]) => Promise<void>
  onRemove: () => void
}): React.JSX.Element {
  const [name, setName] = useState(connection.display_name || connection.provider_name)
  const [tools, setTools] = useState<ConnectorToolInfo[] | null>(null)
  const [toolError, setToolError] = useState('')
  const [toolOverrides, setToolOverrides] = useState<Record<string, boolean>>({})
  const [toolQuery, setToolQuery] = useState('')
  const [showCredential, setShowCredential] = useState(false)
  const reconnectSecretRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    setToolOverrides({})
    setTools(null)
  }, [connection.id, connection.operation_revision])
  const preference = connection.tool_policy?._approval_preference || 'important'
  const accountDescription = connection.account_label || 'Account identity not verified'
  const statusDescription =
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
        {connection.failure || connection.last_error_message ? (
          <section aria-label="Connection recovery" className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
            <p className="font-medium">{connection.failure?.message || connection.last_error_message}</p>
            {connection.failure?.recovery_action ? <p className="mt-1">Next step: {connection.failure.recovery_action}</p> : <p className="mt-1">Test the connection for an updated diagnosis, or sign in again.</p>}
            {connection.failure?.stage ? <p className="mt-2 text-xs">Stage: {connection.failure.stage} · Reference: {connection.failure.code}</p> : null}
          </section>
        ) : null}
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            disabled={busy}
            className="flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm disabled:opacity-50"
            onClick={onTest}
          >
            <RefreshCw size={14} /> Test connection
          </button>
          <button disabled={busy} className="flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm disabled:opacity-50" onClick={() => { setToolError(''); void onInspectTools().then(setTools).catch((error) => setToolError(error instanceof Error ? error.message : 'Tools could not be loaded.')) }}><Wrench size={14} /> Inspect tools</button>
          <button
            disabled={busy}
            className="rounded-lg border px-3 py-2 text-sm disabled:opacity-50"
            onClick={() => connection.auth_type === 'token' || connection.auth_type === 'headers' ? setShowCredential(true) : onReconnect()}
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
        {showCredential ? <form className="mt-4 rounded-xl border p-4" onSubmit={(event) => { event.preventDefault(); const input = reconnectSecretRef.current; const value = input?.value || ''; if (input) input.value = ''; if (!value) return; void onReconnectWithSecret(value).then(() => setShowCredential(false)).catch((error) => setToolError(error instanceof Error ? error.message : 'Sign-in could not be refreshed.')) }}><label className="text-sm"><span className="mb-1 block font-medium">New {connection.auth_type === 'headers' ? 'header value' : 'token'}</span><input ref={reconnectSecretRef} type="password" autoComplete="off" className="w-full rounded-lg border px-3 py-2" /></label><p className="mt-2 text-xs" style={{ color: 'var(--collie-paw)' }}>Sent once through protected storage and cleared immediately.</p><div className="mt-3 flex justify-end gap-2"><button type="button" className="rounded-lg border px-3 py-2 text-sm" onClick={() => { if (reconnectSecretRef.current) reconnectSecretRef.current.value = ''; setShowCredential(false) }}>Cancel</button><button type="submit" className="rounded-lg px-3 py-2 text-sm font-semibold text-white" style={{ background: 'var(--collie-btn-primary-bg)' }}>Sign in again</button></div></form> : null}
        {toolError ? <p role="alert" className="mt-3 text-sm text-red-700">{toolError}</p> : null}
        {tools ? <section aria-label="Available tools" className="mt-4 rounded-xl border p-4"><h3 className="text-sm font-medium">Choose what Collie can use</h3><p className="mt-1 text-xs" style={{ color: 'var(--collie-paw)' }}>Enabling a tool grants access to it. Sends, changes, deletions, and other important actions still ask for approval.</p><form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); setToolError(''); void onInspectTools(toolQuery).then(setTools).catch((error) => setToolError(error instanceof Error ? error.message : 'Tools could not be loaded.')) }}><label className="sr-only" htmlFor="connector-tool-search">Search tools</label><input id="connector-tool-search" value={toolQuery} onChange={(event) => setToolQuery(event.target.value)} className="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm" placeholder="Search all tools"/><button className="rounded-lg border px-3 py-2 text-sm" type="submit">Search</button></form><p className="mt-2 text-xs" style={{ color: 'var(--collie-paw)' }}>Showing {tools.length} results. Search finds tools outside this list; existing selections stay enabled.</p>{tools.length ? <ul className="mt-3 grid gap-2">{tools.map((tool) => <li key={tool.name}><label className="flex gap-3 rounded-lg bg-stone-50 p-2 text-sm"><input type="checkbox" checked={toolOverrides[tool.name] ?? Boolean(tool.enabled)} onChange={(event) => setToolOverrides((current) => ({ ...current, [tool.name]: event.target.checked }))} /><span><b>{tool.name}</b>{tool.review_status === 'new' || tool.review_status === 'changed' ? <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-900">{tool.review_status === 'new' ? 'New — review first' : 'Changed — review again'}</span> : null}{tool.description ? <span className="mt-0.5 block text-xs" style={{ color: 'var(--collie-paw)' }}>{tool.description}</span> : null}</span></label></li>)}</ul> : <p className="mt-2 text-sm" style={{ color: 'var(--collie-paw)' }}>No matching tools.</p>}{tools.length ? <div className="mt-3 flex justify-end"><button disabled={busy} className="rounded-lg border px-3 py-2 text-sm disabled:opacity-50" onClick={() => { const enabled = new Set(connection.enabled_tools || []); for (const [toolName, allowed] of Object.entries(toolOverrides)) { if (allowed) enabled.add(toolName); else enabled.delete(toolName) } void onSaveTools([...enabled]) }}>Save tool access</button></div> : null}</section> : null}
      </section>
    </div>
  )
}

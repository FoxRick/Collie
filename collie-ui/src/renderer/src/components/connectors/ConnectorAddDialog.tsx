import { AlertTriangle, ArrowLeft, FileJson, Link2, Plus, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  ConnectorAuthStrategy,
  ConnectorDefinitionInput,
  ConnectorDefinitionPreview,
  ConnectorFailure,
  ConnectorImportPreview,
  ConnectorSecretInput,
  ConnectorTransport
} from '../../../../shared/connectors'

type ConnectorDefinitionDraft = ConnectorDefinitionInput & { name: string }

type StartChoice = 'link' | 'import'

export default function ConnectorAddDialog({
  busy,
  onClose,
  onValidate,
  onChooseImport,
  onDiscardImportSecrets,
  onSubmit
}: {
  busy: boolean
  onClose: () => void
  onValidate: (draft: ConnectorDefinitionDraft) => Promise<{
    valid: boolean
    preview: ConnectorDefinitionPreview | null
    warnings: string[]
    errors: ConnectorFailure[]
  }>
  onChooseImport: () => Promise<ConnectorImportPreview | null>
  onDiscardImportSecrets: (handles: string[]) => Promise<void>
  onSubmit: (draft: ConnectorDefinitionInput, secret?: ConnectorSecretInput, importSecretHandle?: string) => Promise<void>
}): React.JSX.Element {
  const [choice, setChoice] = useState<StartChoice | null>(null)
  const [draft, setDraft] = useState<ConnectorDefinitionDraft>({
    name: '', endpoint: '', transport: 'streamable_http', auth_strategy: 'none', allow_private_network: false
  })
  const [preview, setPreview] = useState<ConnectorDefinitionPreview | null>(null)
  const [importPreview, setImportPreview] = useState<ConnectorImportPreview | null>(null)
  const [selectedImport, setSelectedImport] = useState(0)
  const [messages, setMessages] = useState<{ warnings: string[]; errors: ConnectorFailure[] }>({ warnings: [], errors: [] })
  const secretRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const [headerName, setHeaderName] = useState('X-API-Key')
  const importHandlesRef = useRef<string[]>([])
  importHandlesRef.current = (importPreview?.definitions || []).flatMap((entry) => entry.secret_handle ? [entry.secret_handle] : [])

  const discardImportSecrets = useCallback((): void => {
    const handles = importHandlesRef.current
    if (handles.length) void onDiscardImportSecrets(handles)
    importHandlesRef.current = []
  }, [onDiscardImportSecrets])
  const close = useCallback((): void => { discardImportSecrets(); onClose() }, [discardImportSecrets, onClose])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const focusable = (): HTMLElement[] => Array.from(dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), summary'))
    focusable()[0]?.focus()
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') { event.preventDefault(); close(); return }
      if (event.key !== 'Tab') return
      const items = focusable()
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    dialog.addEventListener('keydown', onKeyDown)
    return () => dialog.removeEventListener('keydown', onKeyDown)
  }, [close])

  const update = <K extends keyof ConnectorDefinitionDraft>(key: K, value: ConnectorDefinitionDraft[K]): void => {
    setDraft((current) => ({ ...current, [key]: value }))
    setPreview(null)
    setMessages({ warnings: [], errors: [] })
  }

  const validate = async (): Promise<void> => {
    try {
      const definition = draft.auth_strategy === 'headers' ? { ...draft, header_name: headerName.trim() } : draft
      const result = await onValidate(definition)
      setPreview(result.preview)
      setMessages({ warnings: result.warnings || [], errors: result.errors || [] })
    } catch (error) {
      setMessages({ warnings: [], errors: [{ code: 'validation_failed', message: error instanceof Error ? error.message : 'This connection could not be checked.', recovery_action: 'Check the fields and try again.', stage: 'validation', retryable: true }] })
    }
  }

  const performSubmit = async (definition: ConnectorDefinitionInput, secret?: ConnectorSecretInput, importSecretHandle?: string): Promise<void> => {
    try {
      await onSubmit(definition, secret, importSecretHandle)
    } catch (error) {
      if (importSecretHandle) setImportPreview(null)
      setMessages({ warnings: [], errors: [{ code: 'connection_failed', message: error instanceof Error ? error.message : 'That connection did not go through.', recovery_action: importSecretHandle ? 'Choose the file again, review it, and retry.' : 'Check the sign-in details and try again.', stage: 'authentication', retryable: true }] })
    }
  }

  const submit = async (definition: ConnectorDefinitionInput): Promise<void> => {
    const value = secretRef.current?.value || ''
    const secret = value
      ? definition.auth_strategy === 'headers'
        ? { headers: { [headerName.trim()]: value } }
        : { token: value }
      : undefined
    if (secretRef.current) secretRef.current.value = ''
    await performSubmit(definition.auth_strategy === 'headers' ? { ...definition, header_name: headerName.trim() } : definition, secret)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onMouseDown={(event) => event.target === event.currentTarget && close()}>
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="connector-add-title" className="max-h-[calc(100vh-2rem)] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
        <div className="flex items-start gap-3">
          {choice ? <button type="button" aria-label="Back to add connection options" className="mt-0.5 rounded-lg p-1" onClick={() => { discardImportSecrets(); setChoice(null); setPreview(null); setImportPreview(null) }}><ArrowLeft size={18} /></button> : <Plus className="mt-0.5" size={20} />}
          <div className="min-w-0 flex-1">
            <h2 id="connector-add-title" className="font-semibold">{choice === 'link' ? 'Connect using a link' : choice === 'import' ? 'Import a connection file' : 'Add connection'}</h2>
            <p className="mt-1 text-sm" style={{ color: 'var(--collie-paw)' }}>{choice === 'import' ? 'Review the file before Collie saves or contacts anything.' : 'Connect a compatible remote MCP server.'}</p>
          </div>
          <button type="button" className="rounded-lg border px-3 py-1.5 text-sm" onClick={close}>Close</button>
        </div>

        {!choice ? <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <button type="button" className="rounded-xl border p-4 text-left" onClick={() => setChoice('link')}><Link2 size={20} /><b className="mt-3 block text-sm">Connect using a link</b><span className="mt-1 block text-xs" style={{ color: 'var(--collie-paw)' }}>Enter a remote server address and choose how to sign in.</span></button>
          <button type="button" className="rounded-xl border p-4 text-left" onClick={() => setChoice('import')}><FileJson size={20} /><b className="mt-3 block text-sm">Import a connection file</b><span className="mt-1 block text-xs" style={{ color: 'var(--collie-paw)' }}>Preview supported mcpServers entries. Local commands are never run here.</span></button>
        </div> : null}

        {choice === 'link' ? <div className="mt-5 grid gap-4">
          <label className="text-sm"><span className="mb-1 block font-medium">Connection name</span><input autoFocus required value={draft.name} onChange={(event) => update('name', event.target.value)} className="w-full rounded-lg border px-3 py-2" placeholder="My workspace" /></label>
          <label className="text-sm"><span className="mb-1 block font-medium">Server link</span><input required type="url" value={draft.endpoint} onChange={(event) => update('endpoint', event.target.value)} className="w-full rounded-lg border px-3 py-2" placeholder="https://example.com/mcp" /></label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm"><span className="mb-1 block font-medium">Connection type</span><select value={draft.transport} onChange={(event) => update('transport', event.target.value as ConnectorTransport)} className="w-full rounded-lg border px-3 py-2"><option value="streamable_http">Streamable HTTP</option><option value="sse">Legacy SSE</option></select></label>
            <label className="text-sm"><span className="mb-1 block font-medium">Sign-in method</span><select value={draft.auth_strategy} onChange={(event) => update('auth_strategy', event.target.value as ConnectorAuthStrategy)} className="w-full rounded-lg border px-3 py-2"><option value="none">No sign-in</option><option value="token">API token</option><option value="headers">Custom header</option><option value="oauth">OAuth</option></select></label>
          </div>
          {draft.auth_strategy === 'headers' ? <label className="text-sm"><span className="mb-1 block font-medium">Header name</span><input value={headerName} onChange={(event) => { setHeaderName(event.target.value); setPreview(null) }} className="w-full rounded-lg border px-3 py-2" placeholder="X-API-Key" /></label> : null}
          {(draft.auth_strategy === 'token' || draft.auth_strategy === 'headers') ? <label className="text-sm"><span className="mb-1 block font-medium">{draft.auth_strategy === 'token' ? 'Token' : 'Header value'}</span><input ref={secretRef} type="password" autoComplete="off" className="w-full rounded-lg border px-3 py-2" /><span className="mt-1 block text-xs" style={{ color: 'var(--collie-paw)' }}>Sent once to protected storage and cleared from this form.</span></label> : null}
          {draft.auth_strategy === 'oauth' ? <details className="rounded-xl border p-4"><summary className="cursor-pointer text-sm font-medium">Advanced OAuth settings</summary><div className="mt-4 grid gap-3">
            <label className="text-sm"><span className="mb-1 block">Client registration</span><select value={draft.oauth_registration || 'automatic'} onChange={(event) => update('oauth_registration', event.target.value as 'automatic' | 'preregistered')} className="w-full rounded-lg border px-3 py-2"><option value="automatic">Automatic</option><option value="preregistered">Registered client</option></select></label>
            {draft.oauth_registration === 'preregistered' ? <label className="text-sm">Client ID<input value={draft.client_id || ''} onChange={(event) => update('client_id', event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" /></label> : null}
            <label className="text-sm">Scopes, separated by spaces<input value={(draft.scopes || []).join(' ')} onChange={(event) => update('scopes', event.target.value.split(/\s+/).filter(Boolean))} className="mt-1 w-full rounded-lg border px-3 py-2" /></label>
            {(['redirect_uri', 'issuer', 'resource'] as const).map((field) => <label key={field} className="text-sm">{{ redirect_uri: 'Callback URL', issuer: 'Authorization issuer', resource: 'Resource' }[field]}<input value={draft[field] || ''} onChange={(event) => update(field, event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" /></label>)}
          </div></details> : null}
          <label className="flex items-start gap-3 rounded-xl border p-3 text-sm"><input type="checkbox" checked={draft.allow_private_network} onChange={(event) => update('allow_private_network', event.target.checked)} className="mt-0.5" /><span><b className="block">Allow this private or local address</b><span className="text-xs" style={{ color: 'var(--collie-paw)' }}>Only enable this for a server on your network that you trust. Access applies to this exact address.</span></span></label>
          {messages.errors.map((error) => <p key={`${error.code}:${error.message}`} role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error.message} <span className="block mt-1">{error.recovery_action}</span></p>)}
          {messages.warnings.map((message) => <p key={message} className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{message}</p>)}
          {preview ? <PreviewCard preview={preview} /> : null}
          <div className="flex justify-end gap-2"><button type="button" disabled={busy || !draft.name.trim() || !draft.endpoint.trim()} className="rounded-lg border px-4 py-2 text-sm disabled:opacity-50" onClick={() => void validate()}>Review connection</button><button type="button" disabled={busy || !preview || messages.errors.length > 0} className="rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" style={{ background: 'var(--collie-btn-primary-bg)' }} onClick={() => void submit(draft)}>Accept and continue</button></div>
        </div> : null}

        {choice === 'import' ? <div className="mt-5 grid gap-4">
          <button type="button" className="rounded-xl border border-dashed p-5 text-center text-sm" onClick={() => { discardImportSecrets(); void onChooseImport().then((result) => { if (result) { setImportPreview(result); setSelectedImport(0) } }).catch((error) => setMessages({ warnings: [], errors: [{ code: 'import_failed', message: error instanceof Error ? error.message : 'This file could not be read.', recovery_action: 'Choose a JSON file and try again.', stage: 'import', retryable: true }] })) }}><FileJson className="mx-auto mb-2" size={24} /><span className="font-medium">Choose a JSON connection file</span></button>
          <p className="text-xs" style={{ color: 'var(--collie-paw)' }}>Choosing a file only reads it for this preview. Collie will not run commands, install packages, or contact any server.</p>
          {messages.errors.map((error) => <p key={`${error.code}:${error.message}`} role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error.message} <span className="block mt-1">{error.recovery_action}</span></p>)}
          {importPreview ? <>
            {importPreview.definitions.length ? <fieldset><legend className="mb-2 text-sm font-medium">Connections found</legend><div className="grid gap-2">{importPreview.definitions.map((item, index) => <label key={item.key} className="flex gap-3 rounded-xl border p-3 text-sm"><input type="radio" name="import-definition" checked={selectedImport === index} onChange={() => setSelectedImport(index)} /><span><b className="block">{item.name}</b><span className="break-all text-xs" style={{ color: 'var(--collie-paw)' }}>{item.preview.endpoint || 'Local command (unsupported in this release)'}</span></span></label>)}</div></fieldset> : <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">No supported remote connections were found.</p>}
            {importPreview.unsupported.length ? <div className="rounded-xl border border-amber-200 bg-amber-50 p-3"><p className="flex items-center gap-2 text-sm font-medium text-amber-900"><AlertTriangle size={16} />Options that will not be imported</p><ul className="mt-2 list-disc pl-5 text-xs text-amber-900">{importPreview.unsupported.map((item) => <li key={`${item.path}:${item.reason}`}><b>{item.path}</b>: {item.reason}</li>)}</ul></div> : null}
            {importPreview.definitions[selectedImport] ? <PreviewCard preview={importPreview.definitions[selectedImport].preview} /> : null}
            <div className="flex justify-end"><button type="button" disabled={busy || !importPreview.definitions[selectedImport]} className="rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" style={{ background: 'var(--collie-btn-primary-bg)' }} onClick={() => { const entry = importPreview.definitions[selectedImport]; void performSubmit({ ...entry.definition, name: entry.name }, undefined, entry.secret_handle) }}>Accept and continue</button></div>
          </> : null}
        </div> : null}
      </section>
    </div>
  )
}

function PreviewCard({ preview }: { preview: ConnectorDefinitionPreview }): React.JSX.Element {
  return <section aria-label="Connection preview" className="rounded-xl border bg-stone-50 p-4 text-sm"><p className="flex items-center gap-2 font-medium"><ShieldCheck size={16} />Review before connecting</p><dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs"><dt className="font-medium">Name</dt><dd>{preview.name}</dd><dt className="font-medium">Location</dt><dd className="break-all">{preview.endpoint}</dd><dt className="font-medium">Connection</dt><dd>{preview.transport === 'sse' ? 'Legacy SSE' : 'Streamable HTTP'}</dd><dt className="font-medium">Sign-in</dt><dd>{preview.auth_strategy === 'none' ? 'None' : preview.auth_strategy === 'headers' ? 'Protected custom header' : preview.auth_strategy === 'token' ? 'Protected token' : 'OAuth'}</dd><dt className="font-medium">Private network</dt><dd>{preview.allow_private_network ? 'Allowed for this address' : 'Not allowed'}</dd>{preview.has_secret ? <><dt className="font-medium">Secret</dt><dd>Present (value hidden)</dd></> : null}</dl></section>
}

import { ui } from "../lib/i18n"
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  collieClient,
  type ConnectorCatalogItem,
  type ConnectorConnection,
  type RemoteRevocationStatus
} from '../lib/ipc'
import { splitConnectorCatalog } from '../lib/connectorCatalog'
import ConnectorAuthProgress from '../components/connectors/ConnectorAuthProgress'
import ConnectorCard, { connectorIsInFlight } from '../components/connectors/ConnectorCard'
import ConnectedAccountCard from '../components/connectors/ConnectedAccountCard'
import ConnectorDetail from '../components/connectors/ConnectorDetail'
import ConnectorPreflight from '../components/connectors/ConnectorPreflight'
import ConnectorRemoveDialog from '../components/connectors/ConnectorRemoveDialog'
import ConnectorSearch from '../components/connectors/ConnectorSearch'
import ConnectorAddDialog from '../components/connectors/ConnectorAddDialog'
import type { ConnectorDefinitionInput, ConnectorImportPreview, ConnectorSecretInput } from '../../../shared/connectors'

type Tab = 'connected' | 'explore'

export function connectorConnectNotice(name: string, status: string): string {
  if (status === 'connected') return `Connected to ${name}. Try it in chat!`
  if (status === 'auth_required')
    return `${name} needs a fresh sign-in before Collie can use it.`
  if (connectorIsInFlight(status)) return `${name} sign-in is already in progress.`
  return `${name} needs attention before Collie can use it.`
}

export function connectorRemovalNotice(
  name: string,
  remoteRevocation: RemoteRevocationStatus
): string {
  if (remoteRevocation === 'revoked') {
    return `Connection removed. ${name} also confirmed that Collie's access was revoked.`
  }
  if (remoteRevocation === 'unsupported') {
    return (
      `Connection removed from Collie. I can't revoke access with ${name} automatically yet, ` +
      `so you may also want to remove Collie in ${name}'s connected-app settings.`
    )
  }
  if (remoteRevocation === 'failed') {
    return (
      `Connection removed from Collie, but I couldn't confirm that ${name} signed out. ` +
      `You may also want to remove Collie in ${name}'s connected-app settings.`
    )
  }
  return 'Connection removed. You can reconnect any time.'
}

export function matchesActiveConnectorAuthStart(
  connectRequested: boolean,
  active: { providerId: string; connectionId?: string } | null,
  event: { provider_id?: string; connection_id?: string; origin?: string }
): boolean {
  return Boolean(
    connectRequested &&
      active &&
      !active.connectionId &&
      event.origin === 'connectors_ui' &&
      event.connection_id &&
      event.provider_id === active.providerId
  )
}

export function matchesActiveConnectorStatus(
  activeConnectionId: string | undefined,
  event: { connection_id?: string; status?: string }
): boolean {
  return Boolean(
    activeConnectionId &&
      event.connection_id === activeConnectionId &&
      event.status === 'testing'
  )
}

export default function ConnectorsScreen(): React.JSX.Element {
  const [catalog, setCatalog] = useState<ConnectorCatalogItem[]>([])
  const [connections, setConnections] = useState<ConnectorConnection[]>([])
  const [tab, setTab] = useState<Tab>('explore')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<ConnectorConnection | null>(null)
  const [preflight, setPreflight] = useState<ConnectorCatalogItem | null>(null)
  const [removing, setRemoving] = useState<ConnectorConnection | null>(null)
  const [progress, setProgress] = useState<{
    providerId: string
    name: string
    phase: 'authorizing' | 'testing' | 'connected'
    replaceConnectionId?: string
    connectionId?: string
    operationId?: string
    operationRevision?: number
  } | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const connectInFlight = useRef(false)

  const refresh = useCallback(async (): Promise<ConnectorConnection[] | null> => {
    try {
      const [catalogData, connectionData] = await Promise.all([
        collieClient.listConnectorCatalog(),
        collieClient.listConnectorConnections()
      ])
      setCatalog(catalogData.connectors)
      setConnections(connectionData.connections)
      setSelected((current) =>
        current
          ? connectionData.connections.find((item) => item.id === current.id) || null
          : null
      )
      return connectionData.connections
    } catch {
      setNotice("I couldn't reach the connector directory. Is the core awake?")
      return null
    }
  }, [])

  useEffect(() => {
    void refresh()
    return collieClient.on((event) => {
      if (event.type === 'connector_auth_started' && event.connection_id) {
        setProgress((current) => {
          if (
            !current ||
            !matchesActiveConnectorAuthStart(connectInFlight.current, current, event)
          ) {
            return current
          }
          return { ...current, connectionId: event.connection_id, operationId: event.operation_id, operationRevision: event.operation_revision }
        })
      }
      if (
        event.type.startsWith('connector_') &&
        event.type !== 'connector_auth_started'
      ) {
        void refresh()
      }
      if (event.type === 'connector_status_changed' && event.status === 'testing') {
        setProgress((current) =>
          current && matchesActiveConnectorStatus(current.connectionId, event)
            ? { ...current, phase: 'testing' }
            : current
        )
      }
      if ((event.type === 'connector_status_changed' || event.type === 'connector_connected' || event.type === 'connector_failed') && event.connection_id) {
        setProgress((current) => {
          if (!current || current.connectionId !== event.connection_id) return current
          if (current.operationId && event.operation_id && current.operationId !== event.operation_id) return current
          if (current.operationRevision !== undefined && event.operation_revision !== undefined && current.operationRevision !== event.operation_revision) return current
          if (event.type === 'connector_connected' || event.status === 'connected') {
            const completedConnectionId = current.connectionId
            const completedOperationId = current.operationId
            window.setTimeout(() => setProgress((latest) => latest && latest.connectionId === completedConnectionId && latest.operationId === completedOperationId ? null : latest), 1800)
            setNotice(`Connected to ${current.name}. Try it in chat!`)
            return { ...current, phase: 'connected' }
          }
          if (event.type === 'connector_failed' || event.status === 'failed' || event.status === 'auth_required' || event.status === 'attention') {
            setNotice(event.failure?.recovery_action || event.failure?.message || `${current.name} needs attention before Collie can use it.`)
            return null
          }
          return current
        })
      }
    })
  }, [refresh])

  const filtered = useMemo(() => {
    const wanted = query.trim().toLowerCase()
    return wanted
      ? catalog.filter((item) =>
          `${item.name} ${item.description} ${item.category}`.toLowerCase().includes(wanted)
        )
      : catalog
  }, [catalog, query])

  const { featured, categorized, categories } = useMemo(
    () => splitConnectorCatalog(filtered, Boolean(query)),
    [filtered, query]
  )
  const connectedCount = useMemo(
    () => connections.filter((connection) => connection.status === 'connected').length,
    [connections]
  )

  const openPreflight = (connector: ConnectorCatalogItem): void => {
    if (
      !connector.available ||
      connector.status === 'connected' ||
      connectorIsInFlight(connector.status)
    ) {
      return
    }
    setPreflight(connector)
  }

  const startConnect = async (
    connector: ConnectorCatalogItem,
    replaceConnectionId?: string
  ): Promise<void> => {
    if (!connector.available) {
      setPreflight(null)
      setNotice(`${connector.name} is coming soon in this build.`)
      return
    }
    if (connector.status === 'connected') {
      setPreflight(null)
      setNotice(`${connector.name} is already connected.`)
      return
    }
    if (connectorIsInFlight(connector.status) || connectInFlight.current) {
      setPreflight(null)
      setNotice(`${connector.name} sign-in is already in progress.`)
      return
    }
    connectInFlight.current = true
    setPreflight(null)
    setBusy(true)
    setNotice('')
    setProgress({
      providerId: connector.id,
      name: connector.name,
      phase: 'authorizing',
      replaceConnectionId
    })
    try {
      const result = await collieClient.beginConnectorAuth(connector.id, replaceConnectionId)
      const connections = await refresh()
      const status =
        connections?.find((connection) => connection.id === result.connection_id)?.status ||
        result.status
      if (status === 'connected') {
        setProgress((current) => (current ? { ...current, phase: 'connected' } : current))
        setNotice(connectorConnectNotice(connector.name, status))
        window.setTimeout(() => setProgress(null), 1800)
      } else if (connectorIsInFlight(status)) {
        setProgress((current) =>
          current
            ? {
                ...current,
                connectionId: result.connection_id,
                phase: status
              }
            : current
        )
        setNotice(connectorConnectNotice(connector.name, status))
      } else {
        setProgress(null)
        setNotice(connectorConnectNotice(connector.name, status))
      }
    } catch (error) {
      setProgress(null)
      setNotice(error instanceof Error ? error.message : "That connection didn't go through.")
    } finally {
      connectInFlight.current = false
      setBusy(false)
    }
  }

  const addDefinition = async (
    definition: ConnectorDefinitionInput,
    secret?: ConnectorSecretInput,
    importSecretHandle?: string
  ): Promise<void> => {
    setBusy(true)
    setNotice('')
    setProgress(null)
    try {
      const namedDefinition = { ...definition, name: definition.name || 'Imported connection' }
      const saved = await collieClient.saveConnectorDefinition(namedDefinition)
      if (!saved.definition_id) throw new Error('The connection was not saved. Try again.')
      const started = secret || importSecretHandle
        ? await window.collie.submitConnectorCredentials({ action: 'begin_auth', definition_id: saved.definition_id, display_name: definition.name, secret, import_secret_handle: importSecretHandle, origin: 'connectors_ui' })
        : await collieClient.beginDefinitionAuth(saved.definition_id, definition.name)
      setAdding(false)
      const currentConnections = await refresh()
      const authoritative = currentConnections?.find((connection) => connection.id === started.connection_id)
      const status = authoritative?.status || started.status
      if (started.connection_id && (status === 'authorizing' || status === 'testing')) {
        setProgress({
          providerId: saved.provider_id || saved.definition_id,
          name: definition.name || 'Connection',
          phase: status === 'testing' ? 'testing' : 'authorizing',
          connectionId: started.connection_id,
          operationId: started.operation_id,
          operationRevision: started.operation_revision
        })
      }
      setNotice(
        status === 'connected'
          ? `Connected to ${definition.name || 'your server'}. Try it in chat!`
          : authoritative?.failure?.recovery_action || 'Connection saved. Finish sign-in or follow the recovery step shown here.'
      )
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'That connection did not go through.')
      throw error
    } finally {
      setBusy(false)
    }
  }

  const chooseImport = async (): Promise<ConnectorImportPreview | null> => {
    if (typeof window.collie.previewConnectorImportFile === 'function') {
      return window.collie.previewConnectorImportFile()
    }
    throw new Error('Safe connection-file preview is unavailable in this build.')
  }

  if (selected) {
    const connector = catalog.find((item) => item.id === selected.provider_id)
    return (
      <main className="min-w-0 flex-1 overflow-y-auto p-6">
        <ConnectorDetail
          connection={selected}
          busy={busy}
          onBack={() => setSelected(null)}
          onRename={(name) => {
            setBusy(true)
            void collieClient
              .updateConnector(selected.id, { display_name: name })
              .then(({ connection }) => {
                setSelected(connection)
                setNotice('Connection name saved.')
              })
              .catch((error) =>
                setNotice(error instanceof Error ? error.message : 'The name was not saved.')
              )
              .finally(() => setBusy(false))
          }}
          onPreference={(approval_preference) => {
            void collieClient
              .updateConnector(selected.id, { approval_preference })
              .then(({ connection }) => setSelected(connection))
              .catch((error) =>
                setNotice(error instanceof Error ? error.message : 'The preference was not saved.')
              )
          }}
          onTest={() => {
            setBusy(true)
            void collieClient
              .testConnector(selected.id)
              .then(({ connection }) => {
                setSelected(connection)
                setNotice(connection.status === 'connected' ? 'Connection looks healthy.' : connection.failure?.recovery_action || connection.last_error_message || 'The connection still needs attention.')
              })
              .catch((error) =>
                setNotice(error instanceof Error ? error.message : 'The check failed.')
              )
              .finally(() => setBusy(false))
          }}
          onInspectTools={(toolQuery) => collieClient.listConnectorTools(selected.id, toolQuery, 100).then((result) => result.tools)}
          onSaveTools={(enabled_tools) => collieClient.updateConnector(selected.id, { enabled_capabilities: selected.enabled_capabilities, enabled_tools }).then(({ connection }) => { setSelected(connection); setNotice('Tool access saved.') })}
          onReconnectWithSecret={async (value) => {
            setBusy(true)
            try {
              const result = await window.collie.submitConnectorCredentials({ action: 'reconnect', connection_id: selected.id, operation_revision: selected.operation_revision, secret: { token: value }, origin: 'connectors_ui' })
              await refresh()
              setNotice(result.failure?.recovery_action || (result.status === 'connected' ? 'Connection restored.' : 'Reconnection started.'))
            } finally { setBusy(false) }
          }}
          onReconnect={() => {
            setBusy(true)
            void collieClient.reconnectConnector(selected.id, selected.operation_revision).then(async (result) => {
              await refresh()
              setNotice(result.failure?.recovery_action || (result.status === 'connected' ? 'Connection restored.' : 'Reconnection started.'))
            }).catch((error) => setNotice(error instanceof Error ? error.message : 'Reconnection failed.')).finally(() => setBusy(false))
          }}
          onRemove={() => setRemoving(selected)}
        />
        {notice ? (
          <p className="mx-auto mt-4 max-w-3xl text-sm" role="status">
            {notice}
          </p>
        ) : null}
        {preflight && connector ? (
          <ConnectorPreflight
            connector={preflight}
            onCancel={() => setPreflight(null)}
            onContinue={() => void startConnect(preflight, selected.id)}
          />
        ) : null}
        {removing ? (
          <ConnectorRemoveDialog
            name={removing.display_name || removing.provider_name}
            onCancel={() => setRemoving(null)}
            onRemove={() => {
              setBusy(true)
              void collieClient
                .removeConnector(removing.id)
                .then(({ remote_revocation }) => {
                  setRemoving(null)
                  setSelected(null)
                  setNotice(connectorRemovalNotice(removing.provider_name, remote_revocation))
                  return refresh()
                })
                .catch((error) =>
                  setNotice(error instanceof Error ? error.message : 'The removal failed.')
                )
                .finally(() => setBusy(false))
            }}
          />
        ) : null}
      </main>
    )
  }

  return (
    <main className="min-w-0 flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-6xl">
        <header className="mb-5 flex items-start justify-between gap-4">
          <div><h1 className="text-2xl font-semibold">{ui("Connections")}</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--collie-paw)' }}>
            {ui("Pick an app, sign in, and use it in chat. Collie confirms important actions.")}
          </p></div>
          <button type="button" className="rounded-lg px-4 py-2 text-sm font-semibold text-white" style={{ background: 'var(--collie-btn-primary-bg)' }} onClick={() => setAdding(true)}>Add connection</button>
        </header>
        {notice ? (
          <p className="mb-4 rounded-xl border bg-white p-3 text-sm" role="status">
            {notice}
          </p>
        ) : null}
        {progress ? (
          <ConnectorAuthProgress
            providerName={progress.name}
            phase={progress.phase}
            onCancel={
              progress.connectionId
                ? () => {
                    void collieClient
                      .cancelConnectorAuth(progress.connectionId!, progress.operationId, progress.operationRevision)
                      .then((result) => {
                        setProgress(null)
                        setBusy(false)
                        setNotice(result.cancelled ? 'Sign-in cancelled. Nothing was connected.' : 'That sign-in had already finished. The current connection status is shown below.')
                        void refresh()
                      })
                      .catch(() => {
                        setProgress(null)
                        setBusy(false)
                        setNotice('I could not cancel that sign-in. Try again?')
                      })
                  }
                : undefined
            }
          />
        ) : null}
        <div className="mb-5 flex gap-2 border-b" role="tablist">
          {(['connected', 'explore'] as const).map((item) => (
            <button
              key={item}
              role="tab"
              aria-selected={tab === item}
              className={`border-b-2 px-3 py-2 text-sm font-medium ${
                tab === item ? 'border-current' : 'border-transparent'
              }`}
              onClick={() => setTab(item)}
            >
              {item === 'connected' ? `Connected (${connectedCount})` : 'Explore'}
            </button>
          ))}
        </div>
        {tab === 'connected' ? (
          <div className="grid gap-3 md:grid-cols-2">
            {connections.map((connection) => (
              <ConnectedAccountCard
                key={connection.id}
                connection={connection}
                onOpen={setSelected}
              />
            ))}
            {connections.length === 0 ? (
              <div className="col-span-full rounded-2xl border border-dashed p-10 text-center">
                <p className="font-medium">No connected apps yet</p>
                <button
                  className="mt-3 rounded-lg px-4 py-2 text-sm font-semibold text-white"
                  style={{ background: 'var(--collie-btn-primary-bg)' }}
                  onClick={() => setTab('explore')}
                >
                  Explore connections
                </button>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <ConnectorSearch value={query} onChange={setQuery} />
            {featured.length > 0 ? (
              <section className="mt-6">
                <h2 className="mb-3 text-sm font-semibold">{ui("Featured")}</h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {featured.map((connector) => (
                    <ConnectorCard
                      key={`featured-${connector.id}`}
                      connector={connector}
                      onConnect={openPreflight}
                    />
                  ))}
                </div>
              </section>
            ) : null}
            {categories.map((category) => (
              <section className="mt-7" key={category}>
                <h2 className="mb-3 text-sm font-semibold">{category}</h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {categorized
                    .filter((item) => item.category === category)
                    .map((connector) => (
                      <ConnectorCard
                        key={connector.id}
                        connector={connector}
                        onConnect={openPreflight}
                      />
                    ))}
                </div>
              </section>
            ))}
          </>
        )}
      </div>
      {preflight?.available && preflight.status !== 'connected' ? (
        <ConnectorPreflight
          connector={preflight}
          onCancel={() => setPreflight(null)}
          onContinue={() => void startConnect(preflight)}
        />
      ) : null}
      {adding ? (
        <ConnectorAddDialog
          busy={busy}
          onClose={() => setAdding(false)}
          onValidate={(definition) => collieClient.validateConnectorDefinition(definition)}
          onChooseImport={chooseImport}
          onDiscardImportSecrets={(handles) => window.collie.discardConnectorImportSecrets(handles).then(() => undefined)}
          onSubmit={addDefinition}
        />
      ) : null}
    </main>
  )
}

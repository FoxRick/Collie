import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  collieClient,
  type ConnectorCatalogItem,
  type ConnectorConnection
} from '../lib/ipc'
import { splitConnectorCatalog } from '../lib/connectorCatalog'
import ConnectorAuthProgress from '../components/connectors/ConnectorAuthProgress'
import ConnectorCard, { connectorIsInFlight } from '../components/connectors/ConnectorCard'
import ConnectedAccountCard from '../components/connectors/ConnectedAccountCard'
import ConnectorDetail from '../components/connectors/ConnectorDetail'
import ConnectorPreflight from '../components/connectors/ConnectorPreflight'
import ConnectorRemoveDialog from '../components/connectors/ConnectorRemoveDialog'
import ConnectorSearch from '../components/connectors/ConnectorSearch'

type Tab = 'connected' | 'explore'

export function connectorConnectNotice(name: string, status: string): string {
  if (status === 'connected') return `Connected to ${name}. Try it in chat!`
  if (status === 'auth_required')
    return `${name} needs a fresh sign-in before Collie can use it.`
  if (connectorIsInFlight(status)) return `${name} sign-in is already in progress.`
  return `${name} needs attention before Collie can use it.`
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
  } | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
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
          return { ...current, connectionId: event.connection_id }
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
      setNotice(error instanceof Error ? error.message : 'That connection slipped my paws.')
    } finally {
      connectInFlight.current = false
      setBusy(false)
    }
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
                setNotice('Connection looks healthy. *tail wag*')
              })
              .catch((error) =>
                setNotice(error instanceof Error ? error.message : 'The check failed.')
              )
              .finally(() => setBusy(false))
          }}
          onReconnect={() => connector && openPreflight(connector)}
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
                .then(() => {
                  setRemoving(null)
                  setSelected(null)
                  setNotice('Connection removed. You can reconnect any time.')
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
        <header className="mb-5">
          <h1 className="text-2xl font-semibold">Connections</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--collie-paw)' }}>
            Pick an app, sign in, and use it in chat. Collie confirms important actions.
          </p>
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
                      .cancelConnectorAuth(progress.connectionId!)
                      .then(() => {
                        setProgress(null)
                        setBusy(false)
                        setNotice('Sign-in cancelled. Nothing was connected.')
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
                <h2 className="mb-3 text-sm font-semibold">Featured</h2>
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
    </main>
  )
}

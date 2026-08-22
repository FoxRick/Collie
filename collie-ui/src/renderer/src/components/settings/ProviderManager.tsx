import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Plus, Trash2 } from 'lucide-react'
import {
  collieClient,
  type ProviderInfo,
  type RuntimeStatus
} from '../../lib/ipc'
import {
  configureApiKeyProvider,
  configureProvider
} from '../../lib/providerConfiguration'
import BrandLogo from '../BrandLogo'

interface Props {
  status: RuntimeStatus
  settings: Record<string, unknown>
  oauth: { chatgpt: boolean; claude: boolean }
  onRefresh: () => Promise<void>
  onNotice: (message: string) => void
}

const API_PROVIDERS = ['openai', 'anthropic', 'openrouter', 'deepseek', 'groq', 'ollama', 'custom'] as const

/** Friendly names — non-coders should never see raw provider ids. */
const PROVIDER_LABELS: Record<(typeof API_PROVIDERS)[number], string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic (Claude)',
  openrouter: 'OpenRouter',
  deepseek: 'DeepSeek',
  groq: 'Groq',
  ollama: 'Ollama (local)',
  custom: 'Custom API'
}

export const configureSettingsApiKey = configureApiKeyProvider

function providerLabel(provider: ProviderInfo): string {
  if (provider.auth_type === 'chatgpt-oauth') return 'ChatGPT subscription'
  if (provider.auth_type === 'claude-oauth') return 'Claude subscription'
  return provider.name.charAt(0).toUpperCase() + provider.name.slice(1)
}

function persistedProviderId(provider: ProviderInfo): string {
  if (provider.auth_type === 'chatgpt-oauth') return 'oauth-chatgpt'
  if (provider.auth_type === 'claude-oauth') return 'oauth-claude'
  return `api-${provider.name}`
}

export default function ProviderManager({
  status,
  settings,
  oauth,
  onRefresh,
  onNotice
}: Props): React.JSX.Element {
  const [showForm, setShowForm] = useState(false)
  const [provider, setProvider] = useState('openai')
  const [displayName, setDisplayName] = useState('')
  const [protocol, setProtocol] = useState<'openai' | 'anthropic'>('openai')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [busyOAuth, setBusyOAuth] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState('')
  const [secretProviders, setSecretProviders] = useState<string[]>([])
  const [catalogueUpdatedAt, setCatalogueUpdatedAt] = useState<string>('')
  const [catalogueBusy, setCatalogueBusy] = useState(false)
  const oauthAttemptRef = useRef(0)

  useEffect(() => {
    if (typeof window.collie?.listSecrets !== 'function') return
    void window.collie.listSecrets().then(setSecretProviders).catch(() => undefined)
  }, [status.providers])

  useEffect(() => {
    void collieClient
      .getProviderCatalogue()
      .then((data) => setCatalogueUpdatedAt(data.refresh?.refreshed_at || ''))
      .catch(() => undefined)
  }, [])

  const checkCatalogue = useCallback(async (): Promise<void> => {
    setCatalogueBusy(true)
    try {
      const result = await collieClient.refreshProviderCatalogue()
      if (result.refreshed) {
        onNotice(`Provider catalogue updated — ${result.providers_count} providers.`)
        setCatalogueUpdatedAt(result.refreshed_at || '')
      } else {
        onNotice(result.error || 'Catalogue check came back empty — I kept the bundled one.')
      }
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'I could not check the catalogue right now.')
    } finally {
      setCatalogueBusy(false)
    }
  }, [onNotice])

  function catalogueAge(): string {
    if (!catalogueUpdatedAt) return ''
    const updated = new Date(catalogueUpdatedAt).getTime()
    if (Number.isNaN(updated)) return ''
    const minutes = Math.max(1, Math.round((Date.now() - updated) / 60_000))
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'}`
    const hours = Math.round(minutes / 60)
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'}`
    const days = Math.round(hours / 24)
    return `${days} day${days === 1 ? '' : 's'}`
  }

  const providers = [...(status.providers || [])]
  const currentAuth = String(settings['provider.auth'] || '')
  const currentName = String(settings['provider.name'] || '')
  if (
    currentName &&
    !providers.some(
      (item) =>
        (item.runtime_name || item.name) === currentName &&
        item.auth_type === currentAuth
    )
  ) {
    providers.push({
      id: `legacy-${currentAuth}-${currentName}`,
      name: currentName,
      auth_type: currentAuth,
      is_default: 1,
      model: String(settings['provider.model'] || '') || null
    })
  }
  for (const secretProvider of secretProviders) {
    if (providers.some((item) => item.name === secretProvider && item.auth_type === 'api-key')) {
      continue
    }
    providers.push({
      id: `legacy-api-key-${secretProvider}`,
      name: secretProvider,
      auth_type: 'api-key',
      is_default: currentAuth === 'api-key' && currentName === secretProvider ? 1 : 0,
      model:
        currentAuth === 'api-key' && currentName === secretProvider
          ? String(settings['provider.model'] || '') || null
          : null
    })
  }
  if (oauth.chatgpt && !providers.some((item) => item.auth_type === 'chatgpt-oauth')) {
    providers.push({
      id: 'legacy-chatgpt-oauth-openai_codex',
      name: 'openai_codex',
      auth_type: 'chatgpt-oauth',
      is_default: currentAuth === 'chatgpt-oauth' ? 1 : 0,
      model: currentAuth === 'chatgpt-oauth' ? String(settings['provider.model'] || '') || null : null
    })
  }
  if (oauth.claude && !providers.some((item) => item.auth_type === 'claude-oauth')) {
    providers.push({
      id: 'legacy-claude-oauth-anthropic',
      name: 'anthropic',
      auth_type: 'claude-oauth',
      is_default: currentAuth === 'claude-oauth' ? 1 : 0,
      model: currentAuth === 'claude-oauth' ? String(settings['provider.model'] || '') || null : null
    })
  }

  const saveApiKey = async (): Promise<void> => {
    if (!apiKey.trim()) return
    const connectionName = displayName.trim() || provider
    if (provider === 'custom' && (!displayName.trim() || !baseUrl.trim() || !model.trim())) {
      onNotice('A custom API needs a name, base URL, and model ID.')
      return
    }
    setBusyAction('add-api')
    try {
      await configureSettingsApiKey({
        provider,
        displayName: connectionName,
        protocol,
        apiKey,
        model,
        baseUrl
      })
      setApiKey('')
      setModel('')
      setDisplayName('')
      setBaseUrl('')
      setShowForm(false)
      onNotice(`${providerLabel({
        id: '',
        name: connectionName,
        auth_type: 'api-key',
        is_default: 1,
        model: null
      })} connected and selected.`)
      await onRefresh()
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyAction('')
    }
  }

  const cancelOAuth = useCallback((kind: 'chatgpt' | 'claude') => {
    oauthAttemptRef.current += 1
    setBusyOAuth(null)
    collieClient.cancelOAuthLogin(kind).catch(() => {})
  }, [])

  const connectOAuth = useCallback(async (kind: 'chatgpt' | 'claude'): Promise<void> => {
    const attempt = oauthAttemptRef.current + 1
    oauthAttemptRef.current = attempt
    setBusyOAuth(kind)
    try {
      await collieClient.oauthLogin(kind)
      if (oauthAttemptRef.current !== attempt) return
      const isChatGpt = kind === 'chatgpt'
      const result = await collieClient.configure()
      if (!result.configured) throw new Error(result.error || 'That subscription could not connect.')
      onNotice(`${isChatGpt ? 'ChatGPT' : 'Claude'} connected and selected.`)
      await onRefresh()
    } catch (error) {
      if (oauthAttemptRef.current !== attempt) return
      onNotice(error instanceof Error ? error.message : String(error))
    } finally {
      if (oauthAttemptRef.current === attempt) setBusyOAuth(null)
    }
  }, [onRefresh, onNotice])

  const activate = async (item: ProviderInfo): Promise<void> => {
    if (item.auth_type === 'api-key') {
      await configureProvider({
        provider_id: item.id.startsWith('legacy-') ? persistedProviderId(item) : item.id,
        name: item.name,
        auth_type: 'api-key',
        model: item.model,
        runtime_name: item.runtime_name || item.name,
        protocol: item.protocol || 'openai',
        api_base: item.api_base || null,
        secret_name: item.secret_name || item.name
      })
      return
    }
    if (item.id.startsWith('legacy-')) {
      await collieClient.upsertProvider({
        provider_id: persistedProviderId(item),
        name: item.name,
        auth_type: item.auth_type,
        model: item.model,
        is_default: true
      })
      const result = await collieClient.configure()
      if (!result.configured) throw new Error(result.error || 'That provider could not connect.')
      return
    }
    const result = await collieClient.activateProvider(item.id)
    if (!result.configured) throw new Error(result.error || 'That provider could not connect.')
  }

  const remove = async (item: ProviderInfo): Promise<void> => {
    if (!window.confirm(`Remove ${providerLabel(item)} from Collie?`)) return
    setBusyAction(item.id)
    try {
      if (item.auth_type === 'api-key') {
        await window.collie.deleteSecret(item.name)
      } else if (item.auth_type === 'chatgpt-oauth') {
        await collieClient.oauthLogout('chatgpt')
      } else if (item.auth_type === 'claude-oauth') {
        await collieClient.oauthLogout('claude')
      }
        let providerId = item.id
        if (item.id.startsWith('legacy-')) {
          providerId = persistedProviderId(item)
          await collieClient.upsertProvider({
            provider_id: providerId,
            name: item.name,
            auth_type: item.auth_type,
            model: item.model,
            // Removing must not flip the provider to default first.
            is_default: item.is_default === 1
          })
        }
      await collieClient.deleteProvider(providerId)
      onNotice(`${providerLabel(item)} removed.`)
      await onRefresh()
    } catch (error) {
      onNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyAction('')
    }
  }

  return (
    <section className="settings-card provider-manager">
      <div className="provider-heading">
        <div>
          <h3>Models & providers</h3>
          <p>Keep more than one connection and switch from the chat box.</p>
        </div>
        <button type="button" className="secondary-button" onClick={() => setShowForm(!showForm)}>
          <Plus size={14} /> Add AI connection
        </button>
      </div>

      <div className="provider-list">
        {providers.map((item) => (
          <div className="provider-row" key={item.id}>
            <BrandLogo brand={item.name} name={providerLabel(item)} size={36} />
            <div className="provider-copy">
              <b>{providerLabel(item)}</b>
              <span>{item.model || 'Default model'} · {item.auth_type === 'api-key' ? 'API key' : 'Sign-in'}</span>
            </div>
            {item.is_default === 1 ? (
              <span className="provider-active"><Check size={12} /> In use</span>
            ) : (
              <button
                type="button"
                className="provider-use"
                disabled={Boolean(busyAction) || busyOAuth !== null}
                onClick={() => {
                  setBusyAction(item.id)
                  void activate(item)
                    .then(async () => {
                      onNotice(`${providerLabel(item)} is now in use.`)
                      await onRefresh()
                    })
                    .catch((error) => onNotice(error instanceof Error ? error.message : String(error)))
                    .finally(() => setBusyAction(''))
                }}
              >
                Use
              </button>
            )}
            <button
              type="button"
              className="provider-remove"
              disabled={busyAction === item.id || busyOAuth !== null}
              onClick={() => void remove(item)}
              aria-label={`Remove ${providerLabel(item)}`}
              title={`Remove ${providerLabel(item)}`}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {providers.length === 0 && <p className="provider-empty">No AI connection yet — add one above to start chatting.</p>}
      </div>

      <details className="provider-advanced">
        <summary>Advanced — provider list updates</summary>
        <p className="provider-catalogue-line">
          {catalogueAge()
            ? `Provider list updated ${catalogueAge()} ago.`
            : 'Using the built-in provider list.'}{' '}
          <button
            type="button"
            className="catalogue-check"
            disabled={catalogueBusy}
            onClick={() => void checkCatalogue()}
          >
            {catalogueBusy ? 'Checking…' : 'Check for updates'}
          </button>
        </p>
      </details>

      {showForm && (
        <div className="provider-add">
          <select
            value={provider}
            aria-label="API provider"
            onChange={(event) => {
              const value = event.target.value
              setProvider(value)
              setProtocol(value === 'anthropic' ? 'anthropic' : 'openai')
              if (value !== 'custom') setDisplayName('')
            }}
          >
            {API_PROVIDERS.map((item) => <option key={item} value={item}>{PROVIDER_LABELS[item]}</option>)}
          </select>
          <input
            type="password"
            value={apiKey}
            aria-label="API key"
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={secretProviders.includes(displayName.trim() || provider) ? 'Enter a replacement API key' : 'Paste API key'}
          />
          {/* Base URL / protocol / model ID are expert knobs — most people
              only need provider + key. They stay available, tucked away. */}
          {provider === 'custom' && (
            <>
              <input
                type="url"
                value={baseUrl}
                aria-label="API base URL"
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="Base URL"
              />
              <select
                value={protocol}
                aria-label="API protocol"
                onChange={(event) => setProtocol(event.target.value as 'openai' | 'anthropic')}
              >
                <option value="openai">OpenAI-compatible</option>
                <option value="anthropic">Anthropic-compatible</option>
              </select>
            </>
          )}
          <details className="provider-advanced">
            <summary>Advanced — connection name and model</summary>
            <input
              type="text"
              value={displayName}
              aria-label="Connection name"
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder={provider === 'custom' ? 'Connection name' : 'Connection name (optional)'}
            />
            <input
              type="text"
              value={model}
              aria-label="Model ID"
              onChange={(event) => setModel(event.target.value)}
              placeholder="Model ID (optional)"
            />
            {!['custom', 'ollama'].includes(provider) && (
              <input
                type="url"
                value={baseUrl}
                aria-label="API base URL"
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="Custom base URL (optional)"
              />
            )}
          </details>
          <button
            type="button"
            className="primary-button"
            disabled={!apiKey.trim() || busyAction === 'add-api'}
            onClick={() => void saveApiKey()}
          >
            Connect
          </button>
          <span className="provider-or">or sign in</span>
          <div className="provider-signin-row">
            <button
              type="button"
              className="secondary-button provider-signin"
              disabled={busyOAuth === 'chatgpt' || Boolean(busyAction)}
              onClick={() => void connectOAuth('chatgpt')}
            >
              {busyOAuth === 'chatgpt' ? 'Opening browser...' : oauth.chatgpt ? 'Reconnect ChatGPT' : 'Sign in with ChatGPT'}
            </button>
            {busyOAuth === 'chatgpt' && (
              <button
                type="button"
                className="text-xs underline"
                style={{ color: 'var(--collie-text-muted)' }}
                onClick={() => cancelOAuth('chatgpt')}
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              className="secondary-button provider-signin"
              disabled={busyOAuth === 'claude' || busyAction !== ''}
              onClick={() => void connectOAuth('claude')}
            >
              {busyOAuth === 'claude' ? 'Opening browser...' : oauth.claude ? 'Reconnect Claude' : 'Sign in with Claude'}
            </button>
            {busyOAuth === 'claude' && (
              <button
                type="button"
                className="text-xs underline"
                style={{ color: 'var(--collie-text-muted)' }}
                onClick={() => cancelOAuth('claude')}
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

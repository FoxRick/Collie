import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronUp, Key, Search, Sparkles } from 'lucide-react'
import { collieClient, type CatalogueProvider } from '../lib/ipc'
import {
  configureApiKeyProvider,
  SecureStorageUnavailableError
} from '../lib/providerConfiguration'
import BrandLogo from '../components/BrandLogo'
import CollieFace from '../components/CollieFace'

const HELP_URL = 'https://heycollie.com/get-started'
const CUSTOM_PROVIDER_ID = 'custom'
const OLLAMA_BASE_URL = 'http://localhost:11434/v1'

interface Props {
  onDone: () => void
  onCancel?: () => void
}

export const configureWelcomeApiKey = configureApiKeyProvider

/** Turn bare URLs inside error copy into real links (warm copy, clickable help). */
function linkify(text: string): React.ReactNode[] {
  const parts = text.split(/(https?:\/\/[^\s]+)/g)
  return parts.map((part, index) =>
    /^https?:\/\//.test(part) ? (
      <a
        key={index}
        href={part}
        onClick={(event) => {
          event.preventDefault()
          void window.collie?.openExternal(part)
        }}
        className="underline"
      >
        {part}
      </a>
    ) : (
      <span key={index}>{part}</span>
    )
  )
}

/**
 * Actionable copy for when the OS keychain (DPAPI/Keychain/keyring) refused
 * to store the API key. Verified failure mode on the QA rig: on machines
 * with no unlocked keyring, Connect used to dead-end with "Collie could not
 * store that API key securely." and nothing else.
 */
function secureStorageGuidance(platform: string | null | undefined): string {
  switch (platform) {
    case 'darwin':
      return "Your Mac's Keychain is locked or unavailable, so Collie can't save your API key. Open the \u201cKeychain Access\u201d app and unlock your \u201clogin\u201d keychain (or sign back in to your Mac account), then try Connect again."
    case 'linux':
      return "This computer has no unlocked keyring, so Collie can't save your API key. Start your desktop keyring (GNOME Keyring or KWallet), or use Collie just for this session below."
    case 'win32':
    default:
      return "Windows secure storage is locked or turned off, so Collie can't save your API key. Sign back in to your Windows account and try Connect again. (If your work computer blocks it, ask your IT about BitLocker or credential policies.)"
  }
}

export default function WelcomeScreen({ onDone, onCancel }: Props): React.JSX.Element {
  const [busyOAuth, setBusyOAuth] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [showKeyForm, setShowKeyForm] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [provider, setProvider] = useState('openai')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerQuery, setPickerQuery] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [protocol, setProtocol] = useState<'openai' | 'anthropic'>('openai')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [catalogue, setCatalogue] = useState<CatalogueProvider[]>([])
  const [detectHint, setDetectHint] = useState('')
  const [customModels, setCustomModels] = useState<string[]>([])
  const [detectingModels, setDetectingModels] = useState(false)
  const [localModels, setLocalModels] = useState<{ available: boolean; models: string[] }>({
    available: false,
    models: []
  })
  const [localModel, setLocalModel] = useState('')
  const [busyLocal, setBusyLocal] = useState(false)
  const [secureStoragePlatform, setSecureStoragePlatform] = useState<string | null>(null)
  const [secureStorageBlocked, setSecureStorageBlocked] = useState(false)
  const [sessionOnlyBusy, setSessionOnlyBusy] = useState(false)
  const [wsConnected, setWsConnected] = useState(false)
  const mountedRef = useRef(true)
  const oauthAttemptRef = useRef(0)
  const pickerRef = useRef<HTMLDivElement>(null)

  const custom = provider === CUSTOM_PROVIDER_ID
  const selectedProvider = catalogue.find((item) => item.id === provider)
  const protocolForProvider: 'openai' | 'anthropic' = custom
    ? protocol
    : selectedProvider?.protocol || 'openai'

  useEffect(() => {
    mountedRef.current = true
    const off = collieClient.on((event) => {
      if (!mountedRef.current) return
      if (event.type === 'ready') setWsConnected(true)
    })
    setWsConnected(collieClient.connected)
    void collieClient
      .getProviderCatalogue()
      .then((data) => {
        if (mountedRef.current) setCatalogue(data.providers)
      })
      .catch(() => undefined)
    // Local-model card: only shown when an Ollama install is actually there.
    void collieClient
      .detectLocalModels()
      .then((result) => {
        if (!mountedRef.current) return
        setLocalModels(result)
        if (result.available && result.models.length > 0) {
          setLocalModel(result.models[0])
        }
      })
      .catch(() => undefined)
    // Which OS keychain we are on, for the storage-failure guidance copy.
    void window.collie
      ?.secureStorageStatus?.()
      .then((status) => {
        if (mountedRef.current) setSecureStoragePlatform(status.platform)
      })
      .catch(() => undefined)
    return () => {
      mountedRef.current = false
      off()
    }
  }, [])

  useEffect(() => {
    const onPointerDown = (event: MouseEvent): void => {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) {
        setPickerOpen(false)
      }
    }
    window.addEventListener('pointerdown', onPointerDown)
    return () => window.removeEventListener('pointerdown', onPointerDown)
  }, [])

  const finish = useCallback(async (): Promise<void> => {
    const result = await collieClient.configure()
    if (!result.configured) {
      throw new Error(result.error || "That didn't work. Double-check and try again?")
    }
    if (!mountedRef.current) return
    onDone()
  }, [onDone])

  const cancelOAuth = useCallback((kind: 'chatgpt' | 'claude') => {
    oauthAttemptRef.current += 1
    setBusyOAuth(null)
    setError('')
    collieClient.cancelOAuthLogin(kind).catch(() => {})
  }, [])

  const signInOAuth = useCallback(async (kind: 'chatgpt' | 'claude'): Promise<void> => {
    if (!collieClient.connected) {
      setError("Collie isn't connected yet. Give it a moment and try again.")
      return
    }
    const attempt = oauthAttemptRef.current + 1
    oauthAttemptRef.current = attempt
    setBusyOAuth(kind)
    setError('')
    setSuccess('')
    try {
      await collieClient.oauthLogin(kind)
      if (oauthAttemptRef.current !== attempt || !mountedRef.current) return
      await finish()
    } catch (e) {
      if (oauthAttemptRef.current !== attempt || !mountedRef.current) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (oauthAttemptRef.current === attempt && mountedRef.current) setBusyOAuth(null)
    }
  }, [finish])

  const pickProvider = useCallback(
    (value: string) => {
      setProvider(value)
      setPickerOpen(false)
      setPickerQuery('')
      setError('')
      setCustomModels([])
      if (value !== CUSTOM_PROVIDER_ID) {
        setDetectHint('')
        setModel('')
        setBaseUrl('')
      }
    },
    []
  )

  const filteredProviders = useMemo(() => {
    const query = pickerQuery.trim().toLowerCase()
    const list = query
      ? catalogue.filter((item) => item.name.toLowerCase().includes(query))
      : catalogue
    return [...list, { id: CUSTOM_PROVIDER_ID, name: 'Custom (OpenAI-compatible)' } as CatalogueProvider]
  }, [catalogue, pickerQuery])

  /** Paste-time provider auto-detection: prefixes are hints, the probe decides. */
  const detectFromKey = useCallback(
    async (key: string): Promise<void> => {
      if (!key.trim() || custom) {
        setDetectHint('')
        return
      }
      // Instant local hint from the catalogue's key prefixes (longest wins).
      const matches: { length: number; id: string }[] = []
      for (const item of catalogue) {
        for (const prefix of item.key_prefixes || []) {
          if (prefix && key.startsWith(prefix)) {
            matches.push({ length: prefix.length, id: item.id })
            break
          }
        }
      }
      matches.sort((a, b) => b.length - a.length)
      const longest = matches[0]?.length
      const candidates = matches.filter((item) => item.length === longest)
      if (candidates.length === 1) {
        const id = candidates[0].id
        if (id !== provider) {
          setProvider(id)
          setDetectHint(`${catalogue.find((item) => item.id === id)?.name || id} — looks like this key belongs to them.`)
        }
        return
      }
      if (candidates.length > 1) {
        // Ambiguous (sk-): the core never probes candidate endpoints with
        // the user's key — ask the user to pick instead.
        setDetectHint('Checking which provider this key belongs to…')
        try {
          const result = await collieClient.detectProviderForKey(key)
          if (!mountedRef.current) return
          if (result.detected && result.provider_id && result.provider_id !== provider) {
            setProvider(result.provider_id)
            setDetectHint(
              `Looks like this key belongs to ${catalogue.find((item) => item.id === result.provider_id)?.name || result.provider_id}.`
            )
          } else if (result.reason === 'ambiguous' && result.candidates?.length) {
            const names = result.candidates
              .map((id) => catalogue.find((item) => item.id === id)?.name || id)
              .join(' or ')
            setDetectHint(`This key format matches ${names} — choose the right provider above.`)
          } else if (result.detected) {
            setDetectHint('')
          } else {
            setDetectHint('')
          }
        } catch {
          if (mountedRef.current) setDetectHint('')
        }
      }
    },
    [catalogue, custom, provider]
  )

  const saveApiKey = useCallback(async (): Promise<void> => {
    if (!apiKey.trim()) return
    const connectionName = displayName.trim() || provider
    if (custom && (!baseUrl.trim() || !model.trim())) {
      setError('A custom API needs a base URL and a model. Use "Detect models" to find one.')
      return
    }
    setBusyKey(true)
    setError('')
    setSuccess('')
    setSecureStorageBlocked(false)
    try {
      const result = await configureWelcomeApiKey({
        provider,
        displayName: connectionName,
        protocol: protocolForProvider,
        apiKey,
        model: custom ? model : model || undefined,
        baseUrl: custom ? baseUrl : baseUrl || undefined
      })
      if (!mountedRef.current) return
      const label = result.model_label || model || 'your model'
      setSuccess(`Connected — using ${label}. You can switch models in chat anytime.`)
      window.setTimeout(() => {
        if (mountedRef.current) void onDone()
      }, 1100)
    } catch (e) {
      if (!mountedRef.current) return
      if (e instanceof SecureStorageUnavailableError) {
        setSecureStorageBlocked(true)
        setError(secureStorageGuidance(secureStoragePlatform))
        return
      }
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current) setBusyKey(false)
    }
  }, [apiKey, displayName, provider, protocolForProvider, custom, model, baseUrl, onDone, secureStoragePlatform])

  /**
   * Graceful fallback for locked/unavailable OS keychains: the provider is
   * configured for THIS session only — the key is never written to disk and
   * must be re-entered after a restart. Better than a dead end, and the copy
   * says so honestly.
   */
  const connectSessionOnly = useCallback(async (): Promise<void> => {
    if (!apiKey.trim()) return
    setSessionOnlyBusy(true)
    setError('')
    setSuccess('')
    try {
      const result = await configureWelcomeApiKey(
        {
          provider,
          displayName: displayName.trim() || provider,
          protocol: protocolForProvider,
          apiKey,
          model: custom ? model : model || undefined,
          baseUrl: custom ? baseUrl : baseUrl || undefined
        },
        undefined,
        undefined,
        { persistSecret: false }
      )
      if (!mountedRef.current) return
      const label = result.model_label || model || 'your model'
      setSecureStorageBlocked(false)
      setSuccess(
        `Connected — using ${label}. Heads up: your key isn't saved on this computer, so you'll add it again after you restart Collie.`
      )
      window.setTimeout(() => {
        if (mountedRef.current) void onDone()
      }, 2600)
    } catch (e) {
      if (!mountedRef.current) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current) setSessionOnlyBusy(false)
    }
  }, [apiKey, displayName, provider, protocolForProvider, custom, model, baseUrl, onDone])

  const detectCustomModels = useCallback(async (): Promise<void> => {
    if (!baseUrl.trim()) {
      setError('Paste the base URL first, then I can look for models on it.')
      return
    }
    setDetectingModels(true)
    setError('')
    try {
      const result = await collieClient.detectModels(
        baseUrl.trim(),
        protocolForProvider,
        apiKey.trim() || undefined
      )
      if (!mountedRef.current) return
      if (result.detected && result.models.length > 0) {
        setCustomModels(result.models)
        setModel(result.models[0])
      } else {
        setError(
          "I couldn't find a model list on that URL. You can still type the model name below if you know it."
        )
      }
    } catch {
      if (mountedRef.current) {
        setError("I couldn't reach that URL to look for models. Check it and try again.")
      }
    } finally {
      if (mountedRef.current) setDetectingModels(false)
    }
  }, [baseUrl, protocolForProvider, apiKey])

  const connectLocal = useCallback(async (): Promise<void> => {
    if (!localModel) return
    setBusyLocal(true)
    setError('')
    setSuccess('')
    try {
      const result = await configureWelcomeApiKey({
        provider: CUSTOM_PROVIDER_ID,
        displayName: 'Local model (Ollama)',
        protocol: 'openai',
        apiKey: '',
        model: localModel,
        baseUrl: OLLAMA_BASE_URL
      })
      if (!mountedRef.current) return
      setSuccess(`Connected — using ${localModel} on this computer.`)
      window.setTimeout(() => {
        if (mountedRef.current) void onDone()
      }, 1100)
    } catch (e) {
      if (!mountedRef.current) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current) setBusyLocal(false)
    }
  }, [localModel, onDone])

  const modelOptions = custom
    ? customModels
    : (selectedProvider?.models || []).map((item) => item.id)

  return (
    <div className="onboarding-shell flex h-full flex-col overflow-y-auto">
      <div className="collie-header h-14 shrink-0" />
      <div className="onboarding-panel mx-auto flex w-full max-w-xl flex-1 flex-col justify-center gap-4 px-6 py-10">
        {onCancel && (
          <button className="onboarding-back" onClick={onCancel}>
            <ArrowLeft size={15} /> Back to settings
          </button>
        )}
        <div className="onboarding-hero mb-2 text-center">
          <div className="flex justify-center">
            <CollieFace size={80} />
          </div>
          <h1 className="mt-3 text-2xl font-semibold">Welcome to Collie</h1>
          <p className="mt-1" style={{ color: 'var(--collie-text-muted)' }}>
            Your personal AI. With a dog.
          </p>
        </div>

        <button
          onClick={() => void signInOAuth('chatgpt')}
          disabled={busyOAuth !== null || busyKey || busyLocal}
          className="rounded-xl border p-4 text-left transition hover:shadow-md disabled:opacity-50"
          style={{ borderColor: 'var(--collie-border)', background: 'var(--collie-surface)' }}
        >
          <div className="flex items-center gap-3">
            <BrandLogo brand="chatgpt" size={32} />
            <div>
              <div className="font-medium">I have a ChatGPT subscription</div>
              <div className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                {busyOAuth === 'chatgpt'
                  ? 'Check your browser — finishing sign-in...'
                  : 'Sign in with your OpenAI account. Opens your browser.'}
              </div>
            </div>
          </div>
        </button>
        {busyOAuth === 'chatgpt' && (
          <button
            onClick={() => cancelOAuth('chatgpt')}
            className="text-xs underline"
            style={{ color: 'var(--collie-text-muted)' }}
          >
            Cancel sign-in
          </button>
        )}

        <button
          onClick={() => void signInOAuth('claude')}
          disabled={busyOAuth !== null || busyKey || busyLocal}
          className="rounded-xl border p-4 text-left transition hover:shadow-md disabled:opacity-50"
          style={{ borderColor: 'var(--collie-border)', background: 'var(--collie-surface)' }}
        >
          <div className="flex items-center gap-3">
            <BrandLogo brand="claude" size={32} />
            <div>
              <div className="font-medium">I have a Claude subscription</div>
              <div className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                {busyOAuth === 'claude'
                  ? 'Check your browser — finishing sign-in...'
                  : 'Sign in with your Anthropic account. Opens your browser.'}
              </div>
            </div>
          </div>
        </button>
        {busyOAuth === 'claude' && (
          <button
            onClick={() => cancelOAuth('claude')}
            className="text-xs underline"
            style={{ color: 'var(--collie-text-muted)' }}
          >
            Cancel sign-in
          </button>
        )}

        <div
          className="rounded-xl border p-4"
          style={{ borderColor: 'var(--collie-border)', background: 'var(--collie-surface)' }}
        >
          <button
            onClick={() => {
              setShowKeyForm(!showKeyForm)
              if (busyOAuth) cancelOAuth(busyOAuth as 'chatgpt' | 'claude')
            }}
            disabled={busyKey || busyLocal}
            className="flex w-full items-center gap-3 text-left"
          >
            <span
              aria-hidden="true"
              className="flex shrink-0 items-center justify-center rounded-xl border bg-white"
              style={{ width: 32, height: 32, borderColor: 'var(--collie-border)' }}
            >
              <Key size={16} style={{ color: 'var(--collie-snoot)' }} />
            </span>
            <div>
              <div className="font-medium">I have an API key</div>
              <div className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                Paste a key, pick your provider, and Collie figures out the rest.
              </div>
            </div>
          </button>
          {showKeyForm && (
            <div className="mt-4 flex flex-col gap-3">
              <div ref={pickerRef} className="relative">
                <button
                  type="button"
                  onClick={() => setPickerOpen(!pickerOpen)}
                  className="flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left"
                  style={{ borderColor: 'var(--collie-border)' }}
                >
                  <Search size={15} style={{ color: 'var(--collie-text-muted)' }} />
                  <span className="flex-1">
                    {selectedProvider?.name || 'Choose a provider'}
                  </span>
                  <ChevronDown size={15} style={{ color: 'var(--collie-text-muted)' }} />
                </button>
                {pickerOpen && (
                  <div
                    className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border bg-white shadow-lg"
                    style={{ borderColor: 'var(--collie-border)' }}
                  >
                    <input
                      autoFocus
                      type="text"
                      placeholder="Search providers…"
                      value={pickerQuery}
                      onChange={(event) => setPickerQuery(event.target.value)}
                      className="w-full border-b px-3 py-2 outline-none"
                      style={{ borderColor: 'var(--collie-border)' }}
                    />
                    <div className="max-h-56 overflow-y-auto">
                      {filteredProviders.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => pickProvider(item.id)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-black/5"
                        >
                          <BrandLogo brand={item.id} size={20} />
                          <span className="flex-1">{item.name}</span>
                          {provider === item.id && (
                            <span style={{ color: 'var(--collie-snoot)' }}>✓</span>
                          )}
                        </button>
                      ))}
                      {filteredProviders.length === 0 && (
                        <div className="px-3 py-2 text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                          No match — pick Custom to use any OpenAI-compatible service.
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {custom ? (
                <>
                  <input
                    type="url"
                    placeholder="Base URL (e.g. http://localhost:11434/v1)"
                    value={baseUrl}
                    onChange={(event) => setBaseUrl(event.target.value)}
                    className="rounded-lg border px-3 py-2"
                    style={{ borderColor: 'var(--collie-border)' }}
                  />
                  <select
                    value={protocol}
                    onChange={(event) => setProtocol(event.target.value as 'openai' | 'anthropic')}
                    className="rounded-lg border px-3 py-2"
                    style={{ borderColor: 'var(--collie-border)' }}
                  >
                    <option value="openai">OpenAI-compatible</option>
                    <option value="anthropic">Anthropic-compatible</option>
                  </select>
                  <input
                    type="password"
                    placeholder="API key (optional for local servers)"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    className="rounded-lg border px-3 py-2"
                    style={{ borderColor: 'var(--collie-border)' }}
                  />
                  <button
                    type="button"
                    onClick={() => void detectCustomModels()}
                    disabled={detectingModels || !baseUrl.trim()}
                    className="rounded-lg border px-4 py-2 text-sm transition disabled:opacity-50"
                    style={{ borderColor: 'var(--collie-border)' }}
                  >
                    {detectingModels ? 'Looking for models…' : 'Detect models'}
                  </button>
                  <input
                    type="text"
                    list="custom-model-list"
                    placeholder="Model (e.g. llama3.2)"
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    className="rounded-lg border px-3 py-2"
                    style={{ borderColor: 'var(--collie-border)' }}
                  />
                  <datalist id="custom-model-list">
                    {customModels.map((item) => (
                      <option key={item} value={item} />
                    ))}
                  </datalist>
                </>
              ) : (
                <>
                  <input
                    type="password"
                    placeholder="Paste your API key"
                    value={apiKey}
                    onChange={(event) => {
                      setApiKey(event.target.value)
                      void detectFromKey(event.target.value)
                    }}
                    className="rounded-lg border px-3 py-2"
                    style={{ borderColor: 'var(--collie-border)' }}
                  />
                  {detectHint && (
                    <p className="text-xs" style={{ color: 'var(--collie-text-muted)' }}>
                      {detectHint}
                    </p>
                  )}
                </>
              )}

              <button
                onClick={() => {
                  if (showAdvanced) {
                    setShowAdvanced(false)
                  } else {
                    setShowAdvanced(true)
                  }
                }}
                type="button"
                className="flex items-center gap-1 text-xs underline"
                style={{ color: 'var(--collie-text-muted)' }}
              >
                Advanced
                {showAdvanced ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>

              {showAdvanced && (
                <div className="flex flex-col gap-3 rounded-lg border p-3"
                  style={{ borderColor: 'var(--collie-border)' }}
                >
                  <input
                    type="text"
                    placeholder="Connection name (optional)"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    className="rounded-lg border px-3 py-2"
                    style={{ borderColor: 'var(--collie-border)' }}
                  />
                  {!custom && (
                    <input
                      type="text"
                      list="catalogue-model-list"
                      placeholder="Model (optional — Collie picks a good default)"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                      className="rounded-lg border px-3 py-2"
                      style={{ borderColor: 'var(--collie-border)' }}
                    />
                  )}
                  {!custom && (
                    <datalist id="catalogue-model-list">
                      {modelOptions.map((item) => (
                        <option key={item} value={item} />
                      ))}
                    </datalist>
                  )}
                  {!custom && (
                    <input
                      type="url"
                      placeholder="Custom base URL (optional)"
                      value={baseUrl}
                      onChange={(event) => setBaseUrl(event.target.value)}
                      className="rounded-lg border px-3 py-2"
                      style={{ borderColor: 'var(--collie-border)' }}
                    />
                  )}
                </div>
              )}

              <button
                onClick={() => void saveApiKey()}
                disabled={busyKey || !apiKey.trim() || (custom && (!baseUrl.trim() || !model.trim()))}
                className="rounded-lg px-4 py-2 font-medium text-white transition disabled:opacity-50"
                style={{ background: 'var(--collie-btn-primary-bg)' }}
              >
                {busyKey ? 'Connecting…' : 'Connect'}
              </button>
            </div>
          )}
        </div>

        {localModels.available && (
          <div
            className="rounded-xl border p-4"
            style={{ borderColor: 'var(--collie-border)', background: 'var(--collie-surface)' }}
          >
            <div className="flex items-center gap-3">
              <BrandLogo brand="ollama" size={32} />
              <div>
                <div className="font-medium">Use a model on this computer</div>
                <div className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                  {localModels.models.length > 0
                    ? 'I found a local model installed with Ollama. No internet needed.'
                    : 'A local Ollama install is running.'}
                </div>
              </div>
            </div>
            {localModels.models.length > 0 && (
              <div className="mt-3 flex flex-col gap-3">
                <select
                  value={localModel}
                  onChange={(event) => setLocalModel(event.target.value)}
                  className="rounded-lg border px-3 py-2"
                  style={{ borderColor: 'var(--collie-border)' }}
                >
                  {localModels.models.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => void connectLocal()}
                  disabled={busyLocal}
                  className="rounded-lg px-4 py-2 font-medium text-white transition disabled:opacity-50"
                  style={{ background: 'var(--collie-btn-primary-bg)' }}
                >
                  {busyLocal ? 'Connecting…' : 'Connect'}
                </button>
              </div>
            )}
          </div>
        )}

        <button
          onClick={() => void window.collie?.openExternal(HELP_URL)}
          className="rounded-xl border border-dashed p-4 text-left transition hover:shadow-md"
          style={{ borderColor: 'var(--collie-border)' }}
        >
          <div className="flex items-center gap-3">
            <Sparkles size={20} style={{ color: 'var(--collie-gold)' }} />
            <div>
              <div className="font-medium">I don&apos;t have anything yet</div>
              <div className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                See how to get a ChatGPT or Claude account, or an API key
              </div>
            </div>
          </div>
        </button>

        {error && (
          <div
            className="flex items-start gap-2 rounded-lg border p-3 text-sm"
            style={{ borderColor: 'var(--collie-snoot)', color: 'var(--collie-nose)' }}
          >
            <CollieFace size={16} />
            <span>
              <span>Uh oh. </span>
              {linkify(error)}
            </span>
          </div>
        )}

        {secureStorageBlocked && (
          <div
            className="flex flex-col items-start gap-2 rounded-lg border p-3 text-sm"
            style={{ borderColor: 'var(--collie-gold)', background: 'var(--collie-surface)' }}
          >
            <span style={{ color: 'var(--collie-text)' }}>
              You can still use Collie right now — the key just won't be saved.
            </span>
            <button
              onClick={() => void connectSessionOnly()}
              disabled={sessionOnlyBusy}
              className="rounded-lg px-4 py-2 font-medium text-white transition disabled:opacity-50"
              style={{ background: 'var(--collie-btn-primary-bg)' }}
            >
              {sessionOnlyBusy ? 'Connecting…' : 'Use it for this session only'}
            </button>
            <span className="text-xs" style={{ color: 'var(--collie-text-muted)' }}>
              You'll add your key again next time you open Collie.
            </span>
          </div>
        )}

        {success && (
          <div
            className="flex items-center gap-2 rounded-lg border p-3 text-sm"
            style={{ borderColor: 'var(--collie-gold)', color: 'var(--collie-text)' }}
          >
            <CollieFace size={16} />
            <span>{success}</span>
          </div>
        )}

        <p className="text-center text-xs" style={{ color: 'var(--collie-text-muted)' }}>
          You can add more providers later in Settings → Models &amp; API keys.
        </p>
      </div>
    </div>
  )
}

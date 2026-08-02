import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Sparkles } from 'lucide-react'
import { collieClient } from '../lib/ipc'
import { configureApiKeyProvider } from '../lib/providerConfiguration'
import BrandLogo from '../components/BrandLogo'
import CollieFace from '../components/CollieFace'

const PROVIDERS = [
  { id: 'openai', label: 'OpenAI' },
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'deepseek', label: 'DeepSeek' },
  { id: 'groq', label: 'Groq' },
  { id: 'ollama', label: 'Ollama (local)' },
  { id: 'custom', label: 'Custom (OpenAI-compatible)' }
]

interface Props {
  onDone: () => void
  onCancel?: () => void
}

export const configureWelcomeApiKey = configureApiKeyProvider

export default function WelcomeScreen({ onDone, onCancel }: Props): React.JSX.Element {
  const [busyOAuth, setBusyOAuth] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState(false)
  const [error, setError] = useState('')
  const [showKeyForm, setShowKeyForm] = useState(false)
  const [provider, setProvider] = useState('openai')
  const [displayName, setDisplayName] = useState('')
  const [protocol, setProtocol] = useState<'openai' | 'anthropic'>('openai')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [wsConnected, setWsConnected] = useState(false)
  const mountedRef = useRef(true)
  const oauthAttemptRef = useRef(0)

  useEffect(() => {
    mountedRef.current = true
    const off = collieClient.on((event) => {
      if (!mountedRef.current) return
      if (event.type === 'ready') setWsConnected(true)
    })
    setWsConnected(collieClient.connected)
    return () => {
      mountedRef.current = false
      off()
    }
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

  const saveApiKey = useCallback(async (): Promise<void> => {
    if (!apiKey.trim()) return
    const connectionName = displayName.trim() || provider
    if (provider === 'custom' && (!displayName.trim() || !baseUrl.trim() || !model.trim())) {
      setError('A custom API needs a name, base URL, and model ID.')
      return
    }
    setBusyKey(true)
    setError('')
    try {
      await configureWelcomeApiKey({
        provider,
        displayName: connectionName,
        protocol,
        apiKey,
        model,
        baseUrl
      })
      if (mountedRef.current) onDone()
    } catch (e) {
      if (!mountedRef.current) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (mountedRef.current) setBusyKey(false)
    }
  }, [apiKey, displayName, provider, protocol, model, baseUrl, onDone])

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
          disabled={busyOAuth !== null || busyKey}
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
          disabled={busyOAuth !== null || busyKey}
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
            disabled={busyKey}
            className="flex w-full items-center gap-3 text-left"
          >
            <BrandLogo brand={provider} size={32} />
            <div>
              <div className="font-medium">I have an API key</div>
              <div className="text-sm" style={{ color: 'var(--collie-text-muted)' }}>
                OpenAI, Anthropic, OpenRouter, DeepSeek, Ollama, and more
              </div>
            </div>
          </button>
          {showKeyForm && (
            <div className="mt-4 flex flex-col gap-3">
              <select
                value={provider}
                onChange={(e) => {
                  const value = e.target.value
                  setProvider(value)
                  setProtocol(value === 'anthropic' ? 'anthropic' : 'openai')
                  if (value !== 'custom') setDisplayName('')
                }}
                className="rounded-lg border px-3 py-2"
                style={{ borderColor: 'var(--collie-border)' }}
              >
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder={provider === 'custom' ? 'Connection name' : 'Connection name (optional)'}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="rounded-lg border px-3 py-2"
                style={{ borderColor: 'var(--collie-border)' }}
              />
              {provider === 'custom' && (
                <select
                  value={protocol}
                  onChange={(e) => setProtocol(e.target.value as 'openai' | 'anthropic')}
                  className="rounded-lg border px-3 py-2"
                  style={{ borderColor: 'var(--collie-border)' }}
                >
                  <option value="openai">OpenAI-compatible</option>
                  <option value="anthropic">Anthropic-compatible</option>
                </select>
              )}
              <input
                type="password"
                placeholder="Paste your API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="rounded-lg border px-3 py-2"
                style={{ borderColor: 'var(--collie-border)' }}
              />
              <input
                type="text"
                placeholder="Model (optional — e.g. deepseek-v4-pro, gpt-5.5)"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="rounded-lg border px-3 py-2"
                style={{ borderColor: 'var(--collie-border)' }}
              />
              <input
                type="url"
                placeholder={provider === 'custom'
                  ? 'Base URL (e.g. http://localhost:11434/v1)'
                  : 'Custom base URL (optional)'}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className="rounded-lg border px-3 py-2"
                style={{ borderColor: 'var(--collie-border)' }}
              />
              <button
                onClick={() => void saveApiKey()}
                disabled={busyKey || !apiKey.trim()}
                className="rounded-lg px-4 py-2 font-medium text-white transition disabled:opacity-50"
                style={{ background: 'var(--collie-btn-primary-bg)' }}
              >
                {busyKey ? 'Connecting...' : 'Continue'}
              </button>
            </div>
          )}
        </div>

        <button
          onClick={() => void window.collie?.openExternal('https://collie.ai/get-started')}
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
            className="flex items-center gap-2 rounded-lg border p-3 text-sm"
            style={{ borderColor: 'var(--collie-snoot)', color: 'var(--collie-nose)' }}
          >
            <CollieFace size={16} />
            <span>Uh oh. {error}</span>
          </div>
        )}

        <p className="text-center text-xs" style={{ color: 'var(--collie-text-muted)' }}>
          You can add more providers later in Settings → Account.
        </p>
      </div>
    </div>
  )
}

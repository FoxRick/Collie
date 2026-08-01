import {
  collieClient,
  type ProviderCandidate,
  type ProviderCandidateResult
} from './ipc'

interface ProviderConfigurationClient {
  configureProviderCandidate(candidate: ProviderCandidate): Promise<ProviderCandidateResult>
  finalizeProviderCandidate(transactionId: string): Promise<{ finalized: boolean }>
  rollbackProviderCandidate(
    transactionId: string
  ): Promise<{ rolled_back: boolean; rollback_error?: string | null }>
}

interface SecretTransactionBridge {
  stageSecretChange(
    provider: string,
    key: string
  ): Promise<{ saved: boolean; transactionId?: string }>
  finalizeSecretChange(transactionId: string): Promise<boolean>
  rollbackSecretChange(transactionId: string): Promise<boolean>
}

export interface ApiKeyProviderInput {
  provider: string
  displayName?: string
  protocol?: 'openai' | 'anthropic'
  apiKey: string
  model?: string
  baseUrl?: string
}

export function apiKeyProviderCandidate(input: ApiKeyProviderInput): ProviderCandidate {
  const provider = input.provider.trim().toLowerCase()
  const connectionName = input.displayName?.trim() || provider
  const protocol =
    provider === 'custom'
      ? input.protocol || 'openai'
      : provider === 'anthropic'
        ? 'anthropic'
        : 'openai'
  return {
    provider_id: `api-${connectionName}`,
    name: connectionName,
    auth_type: 'api-key',
    model: input.model?.trim() || null,
    runtime_name: provider,
    protocol,
    api_base: input.baseUrl?.trim() || null,
    secret_name: connectionName,
    api_key: input.apiKey.trim()
  }
}

function failureMessage(result: ProviderCandidateResult): string {
  const base = result.error || 'That provider could not connect.'
  return result.rollback_error ? `${base} Rollback also failed: ${result.rollback_error}` : base
}

let providerConfigurationQueue: Promise<void> = Promise.resolve()

async function runProviderConfiguration(
  candidate: ProviderCandidate,
  client: ProviderConfigurationClient,
  secrets: SecretTransactionBridge
): Promise<ProviderCandidateResult> {
  const result = await client.configureProviderCandidate(candidate)
  if (!result.configured) throw new Error(failureMessage(result))

  const coreTransactionId = result.transaction_id
  if (!coreTransactionId) {
    throw new Error('Provider configuration did not return a transaction ID.')
  }

  let secretTransactionId: string | undefined
  if (candidate.api_key) {
    let staged: { saved: boolean; transactionId?: string }
    try {
      staged = await secrets.stageSecretChange(candidate.secret_name, candidate.api_key)
    } catch {
      staged = { saved: false }
    }
    if (!staged.saved || !staged.transactionId) {
      const rollback = await client.rollbackProviderCandidate(coreTransactionId)
      const detail = rollback.rolled_back
        ? ''
        : ` Core rollback also failed: ${rollback.rollback_error || 'unknown error'}`
      throw new Error(`Collie could not store that API key securely.${detail}`)
    }
    secretTransactionId = staged.transactionId
  }

  try {
    const finalized = await client.finalizeProviderCandidate(coreTransactionId)
    if (!finalized.finalized) throw new Error('Core provider transaction could not be finalized.')
  } catch (error) {
    const rollback = await client.rollbackProviderCandidate(coreTransactionId).catch(() => ({
      rolled_back: false,
      rollback_error: 'core unavailable'
    }))
    if (secretTransactionId) {
      await secrets.rollbackSecretChange(secretTransactionId).catch(() => false)
    }
    const detail = rollback.rolled_back
      ? ''
      : ` Rollback also failed: ${rollback.rollback_error || 'unknown error'}`
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`${message}${detail}`)
  }

  if (secretTransactionId) {
    await secrets.finalizeSecretChange(secretTransactionId).catch(() => false)
  }
  return result
}

export function configureProvider(
  candidate: ProviderCandidate,
  client: ProviderConfigurationClient = collieClient,
  secrets: SecretTransactionBridge = window.collie
): Promise<ProviderCandidateResult> {
  const attempt = providerConfigurationQueue.then(
    () => runProviderConfiguration(candidate, client, secrets),
    () => runProviderConfiguration(candidate, client, secrets)
  )
  providerConfigurationQueue = attempt.then(
    () => undefined,
    () => undefined
  )
  return attempt
}

export function configureApiKeyProvider(
  input: ApiKeyProviderInput,
  client?: ProviderConfigurationClient,
  secrets?: SecretTransactionBridge
): Promise<ProviderCandidateResult> {
  return configureProvider(apiKeyProviderCandidate(input), client, secrets)
}

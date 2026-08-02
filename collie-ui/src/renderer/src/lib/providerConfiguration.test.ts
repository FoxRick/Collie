import { describe, expect, it, vi } from 'vitest'
import {
  apiKeyProviderCandidate,
  configureProvider,
  type ApiKeyProviderInput
} from './providerConfiguration'
import type { ProviderCandidate, ProviderCandidateResult } from './ipc'

function fakes(result: ProviderCandidateResult = {
  configured: true,
  transaction_id: 'core-tx'
}): {
  client: {
    configureProviderCandidate: ReturnType<typeof vi.fn>
    finalizeProviderCandidate: ReturnType<typeof vi.fn>
    rollbackProviderCandidate: ReturnType<typeof vi.fn>
  }
  secrets: {
    stageSecretChange: ReturnType<typeof vi.fn>
    finalizeSecretChange: ReturnType<typeof vi.fn>
    rollbackSecretChange: ReturnType<typeof vi.fn>
  }
} {
  return {
    client: {
      configureProviderCandidate: vi.fn().mockResolvedValue(result),
      finalizeProviderCandidate: vi.fn().mockResolvedValue({ finalized: true }),
      rollbackProviderCandidate: vi.fn().mockResolvedValue({ rolled_back: true })
    },
    secrets: {
      stageSecretChange: vi.fn().mockResolvedValue({ saved: true, transactionId: 'secret-tx' }),
      finalizeSecretChange: vi.fn().mockResolvedValue(true),
      rollbackSecretChange: vi.fn().mockResolvedValue(true)
    }
  }
}

const input: ApiKeyProviderInput = {
  provider: 'custom',
  displayName: 'Work Gateway',
  protocol: 'anthropic',
  apiKey: 'new-secret',
  model: 'company-model',
  baseUrl: 'https://models.example.test/v1'
}

describe('transactional provider configuration', () => {
  it('builds the same normalized custom candidate for every renderer flow', () => {
    expect(apiKeyProviderCandidate(input)).toEqual({
      provider_id: 'api-Work Gateway',
      name: 'Work Gateway',
      auth_type: 'api-key',
      model: 'company-model',
      runtime_name: 'custom',
      protocol: 'anthropic',
      api_base: 'https://models.example.test/v1',
      secret_name: 'Work Gateway',
      api_key: 'new-secret'
    })
  })

  it('persists safeStorage only after backend success and finalizes both transactions', async () => {
    const events: string[] = []
    const { client, secrets } = fakes()
    client.configureProviderCandidate.mockImplementation(async () => {
      events.push('core-configured')
      return { configured: true, transaction_id: 'core-tx' }
    })
    secrets.stageSecretChange.mockImplementation(async () => {
      events.push('secret-staged')
      return { saved: true, transactionId: 'secret-tx' }
    })
    client.finalizeProviderCandidate.mockImplementation(async () => {
      events.push('core-finalized')
      return { finalized: true }
    })
    secrets.finalizeSecretChange.mockImplementation(async () => {
      events.push('secret-finalized')
      return true
    })

    await configureProvider(apiKeyProviderCandidate(input), client, secrets)

    expect(events).toEqual([
      'core-configured',
      'secret-staged',
      'core-finalized',
      'secret-finalized'
    ])
  })

  it('does not touch safeStorage when the core rejects and rolls back a candidate', async () => {
    const { client, secrets } = fakes({
      configured: false,
      error: 'endpoint unreachable',
      rolled_back: true
    })

    await expect(
      configureProvider(apiKeyProviderCandidate(input), client, secrets)
    ).rejects.toThrow('endpoint unreachable')
    expect(secrets.stageSecretChange).not.toHaveBeenCalled()
  })

  it('compensates core state when safeStorage cannot stage the replacement', async () => {
    const { client, secrets } = fakes()
    secrets.stageSecretChange.mockResolvedValue({ saved: false })

    await expect(
      configureProvider(apiKeyProviderCandidate(input), client, secrets)
    ).rejects.toThrow('store that API key securely')
    expect(client.rollbackProviderCandidate).toHaveBeenCalledWith('core-tx')
    expect(client.finalizeProviderCandidate).not.toHaveBeenCalled()
  })

  it('reports when safeStorage failure and core rollback both fail', async () => {
    const { client, secrets } = fakes()
    secrets.stageSecretChange.mockResolvedValue({ saved: false })
    client.rollbackProviderCandidate.mockResolvedValue({
      rolled_back: false,
      rollback_error: 'previous runtime rebuild failed'
    })

    await expect(
      configureProvider(apiKeyProviderCandidate(input), client, secrets)
    ).rejects.toThrow('Core rollback also failed: previous runtime rebuild failed')
  })

  it('rolls back both layers when core finalization fails', async () => {
    const { client, secrets } = fakes()
    client.finalizeProviderCandidate.mockResolvedValue({ finalized: false })

    await expect(
      configureProvider(apiKeyProviderCandidate(input), client, secrets)
    ).rejects.toThrow('could not be finalized')
    expect(client.rollbackProviderCandidate).toHaveBeenCalledWith('core-tx')
    expect(secrets.rollbackSecretChange).toHaveBeenCalledWith('secret-tx')
  })

  it('activates an existing transient key without touching safeStorage', async () => {
    const { client, secrets } = fakes()
    const candidate = apiKeyProviderCandidate(input)
    delete candidate.api_key

    await configureProvider(candidate, client, secrets)

    expect(secrets.stageSecretChange).not.toHaveBeenCalled()
    expect(client.finalizeProviderCandidate).toHaveBeenCalledWith('core-tx')
  })

  it('queues the entire core, safeStorage, and finalize sequence', async () => {
    const first = fakes()
    const second = fakes()
    const events: string[] = []
    let releaseFirst: (() => void) | undefined
    first.client.configureProviderCandidate.mockImplementation(async () => {
      events.push('first-start')
      await new Promise<void>((resolve) => {
        releaseFirst = resolve
      })
      events.push('first-finish')
      return { configured: true, transaction_id: 'first-core' }
    })
    first.secrets.stageSecretChange.mockResolvedValue({
      saved: true,
      transactionId: 'first-secret'
    })
    second.client.configureProviderCandidate.mockImplementation(async () => {
      events.push('second-start')
      return { configured: true, transaction_id: 'second-core' }
    })

    const firstAttempt = configureProvider(apiKeyProviderCandidate(input), first.client, first.secrets)
    const secondAttempt = configureProvider(
      apiKeyProviderCandidate({ ...input, displayName: 'Second' }),
      second.client,
      second.secrets
    )
    await vi.waitFor(() => expect(events).toEqual(['first-start']))
    releaseFirst?.()
    await Promise.all([firstAttempt, secondAttempt])

    expect(events).toEqual(['first-start', 'first-finish', 'second-start'])
  })

  it('routes both Welcome and Settings through the shared transaction helper', async () => {
    const { configureWelcomeApiKey } = await import('../screens/WelcomeScreen')
    const { configureSettingsApiKey } = await import('../components/settings/ProviderManager')
    const { client, secrets } = fakes()

    await configureWelcomeApiKey(input, client, secrets)
    await configureSettingsApiKey(input, client, secrets)

    expect(client.configureProviderCandidate).toHaveBeenCalledTimes(2)
    const candidates = client.configureProviderCandidate.mock.calls.map(
      (call) => call[0] as ProviderCandidate
    )
    expect(candidates[0]).toEqual(candidates[1])
    expect(secrets.stageSecretChange).toHaveBeenCalledTimes(2)
  })
})

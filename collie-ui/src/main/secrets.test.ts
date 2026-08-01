import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const testState = vi.hoisted(() => ({ userData: '' }))

vi.mock('electron', () => ({
  app: { getPath: () => testState.userData },
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (value: string) => Buffer.from(`encrypted:${value}`, 'utf8'),
    decryptString: (value: Buffer) => value.toString('utf8').replace(/^encrypted:/, '')
  }
}))

import {
  finalizeSecretChange,
  loadSecrets,
  resetSecretsConsumption,
  rollbackSecretChange,
  saveSecret,
  stageSecretChange
} from './secrets'

describe('encrypted provider secret transactions', () => {
  beforeEach(() => {
    testState.userData = mkdtempSync(join(tmpdir(), 'collie-secrets-'))
    resetSecretsConsumption()
  })

  afterEach(() => {
    rmSync(testState.userData, { recursive: true, force: true })
  })

  it('restores the prior encrypted value without returning its plaintext', () => {
    expect(saveSecret('work', 'old-secret')).toBe(true)
    const staged = stageSecretChange('work', 'replacement-secret')

    expect(staged.saved).toBe(true)
    expect(staged).not.toHaveProperty('previous')
    expect(rollbackSecretChange(staged.transactionId!)).toBe(true)
    resetSecretsConsumption()
    expect(loadSecrets()).toEqual({ work: 'old-secret' })
  })

  it('keeps the replacement after finalization', () => {
    expect(saveSecret('work', 'old-secret')).toBe(true)
    const staged = stageSecretChange('work', 'replacement-secret')

    expect(finalizeSecretChange(staged.transactionId!)).toBe(true)
    expect(rollbackSecretChange(staged.transactionId!)).toBe(false)
    resetSecretsConsumption()
    expect(loadSecrets()).toEqual({ work: 'replacement-secret' })
  })
})

import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { safeStorage } from 'electron'

const testState = vi.hoisted(() => ({ userData: '' }))

vi.mock('electron', () => ({
  app: { getPath: () => testState.userData },
  safeStorage: {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => 'gnome_libsecret',
    encryptString: (value: string) => Buffer.from(`encrypted:${value}`, 'utf8'),
    decryptString: (value: Buffer) => value.toString('utf8').replace(/^encrypted:/, '')
  }
}))

import {
  finalizeSecretChange,
  loadSecrets,
  resetSecureStorageCache,
  resetSecretsConsumption,
  rollbackSecretChange,
  saveSecret,
  secureStorageAvailable,
  stageSecretChange
} from './secrets'

describe('encrypted provider secret transactions', () => {
  beforeEach(() => {
    testState.userData = mkdtempSync(join(tmpdir(), 'collie-secrets-'))
    resetSecretsConsumption()
    resetSecureStorageCache()
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

  it('treats a throwing safeStorage probe as unavailable (broken keyring)', () => {
    // The Linux no-keyring-bus failure mode: isEncryptionAvailable() throws
    // instead of returning false. The wrapper must not throw, and the write
    // paths must decline cleanly so the UI can explain instead of crashing.
    const original = safeStorage.isEncryptionAvailable
    safeStorage.isEncryptionAvailable = () => {
      throw new Error('no keyring bus')
    }
    resetSecureStorageCache()
    try {
      expect(secureStorageAvailable()).toBe(false)
      expect(saveSecret('work', 'key')).toBe(false)
      expect(stageSecretChange('work', 'key')).toEqual({ saved: false })
    } finally {
      safeStorage.isEncryptionAvailable = original
      resetSecureStorageCache()
    }
  })

  it('caches the probe result so it runs at most once per process', () => {
    const probe = vi.fn(() => true)
    const original = safeStorage.isEncryptionAvailable
    safeStorage.isEncryptionAvailable = probe
    resetSecureStorageCache()
    try {
      expect(secureStorageAvailable()).toBe(true)
      expect(secureStorageAvailable()).toBe(true)
      expect(probe).toHaveBeenCalledTimes(1)
    } finally {
      safeStorage.isEncryptionAvailable = original
      resetSecureStorageCache()
    }
  })

  it.skipIf(process.platform !== 'linux')(
    'rejects the Linux basic_text backend (hardcoded-password "encryption")',
    () => {
    // Without a keyring daemon Electron falls back to basic_text, which
    // "encrypts" with a hardcoded password shipped in the Electron source.
    // isEncryptionAvailable() still reports true — the wrapper must refuse.
    const original = safeStorage.getSelectedStorageBackend
    safeStorage.getSelectedStorageBackend = () => 'basic_text'
    resetSecureStorageCache()
    try {
      expect(secureStorageAvailable()).toBe(false)
      expect(saveSecret('work', 'key')).toBe(false)
      expect(loadSecrets()).toEqual({})
    } finally {
      safeStorage.getSelectedStorageBackend = original
      resetSecureStorageCache()
    }
  })

  it('accepts a real Linux keyring backend', () => {
    const original = safeStorage.getSelectedStorageBackend
    safeStorage.getSelectedStorageBackend = () => 'gnome_libsecret'
    resetSecureStorageCache()
    try {
      expect(secureStorageAvailable()).toBe(true)
      expect(saveSecret('work', 'key')).toBe(true)
    } finally {
      safeStorage.getSelectedStorageBackend = original
      resetSecureStorageCache()
    }
  })
})

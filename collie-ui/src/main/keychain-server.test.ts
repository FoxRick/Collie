import { afterEach, describe, expect, it, vi } from 'vitest'
import { safeStorage } from 'electron'

const testState = vi.hoisted(() => ({ userData: '' }))

/**
 * XOR ciphertext stands in for real safeStorage: it is reversible and its
 * output never contains the plaintext (the data is transformed), so the
 * no-plaintext-leak guard in keychain-server is tested meaningfully.
 */
const XORED_BUFFER = (value: string): Buffer =>
  Buffer.from(
    [...value].map((ch) => String.fromCharCode(ch.charCodeAt(0) ^ 0x5a)).join(''),
    'utf8'
  )
const XORED_TEXT = (value: Buffer): string =>
  [...value.toString('utf8')]
    .map((ch) => String.fromCharCode(ch.charCodeAt(0) ^ 0x5a))
    .join('')

vi.mock('electron', () => ({
  app: { getPath: () => testState.userData },
  safeStorage: {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => 'gnome_libsecret',
    encryptString: (value: string) => XORED_BUFFER(value),
    decryptString: (value: Buffer) => XORED_TEXT(value)
  }
}))

import {
  keychainAddress,
  startKeychainServer,
  stopKeychainServer
} from './keychain-server'
import { resetSecureStorageCache, secureStorageAvailable } from './secrets'

function call(
  port: number,
  token: string,
  path: string,
  data: { data: string }
): Promise<{ status: number; body: { data?: string; error?: string } }> {
  return fetch(`http://127.0.0.1:${port}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(data)
  }).then(async (res) => ({
    status: res.status,
    body: (await res.json()) as { data?: string; error?: string }
  }))
}

afterEach(() => {
  stopKeychainServer()
  resetSecureStorageCache()
})

describe('main-process keychain bridge', () => {
  it('does not start when secure storage is unavailable', async () => {
    // Force the probe to fail (broken keyring), then restore the mock so the
    // remaining tests re-probe against the healthy backend again.
    const original = safeStorage.isEncryptionAvailable
    safeStorage.isEncryptionAvailable = () => false
    try {
      const address = await startKeychainServer()
      expect(address).toBeNull()
      expect(keychainAddress()).toBeNull()
    } finally {
      safeStorage.isEncryptionAvailable = original
    }
  })

  it('starts a loopback endpoint guarded by a bearer token', async () => {
    const address = await startKeychainServer()
    expect(address).not.toBeNull()
    expect(address!.port).toBeGreaterThan(0)
    expect(address!.token).toBeTruthy()
    expect(keychainAddress()?.port).toBe(address!.port)
  })

  it('rejects requests without the token and unknown routes', async () => {
    const { port, token } = (await startKeychainServer())!
    const noAuth = await call(port, 'wrong-token', '/encrypt', { data: 'AA==' })
    expect(noAuth.status).toBe(401)
    const badPath = await call(port, token, '/nope', { data: 'AA==' })
    expect(badPath.status).toBe(404)
  })

  it('encrypts then decrypts a payload losslessly', async () => {
    const { port, token } = (await startKeychainServer())!
    const payload = Buffer.from('{"access_token":"secret-access-token"}', 'utf8')
    const enc = await call(port, token, '/encrypt', {
      data: payload.toString('base64')
    })
    expect(enc.status).toBe(200)
    expect(enc.body.data).toBeTruthy()
    const ciphertext = Buffer.from(enc.body.data!, 'base64')
    expect(ciphertext.toString('utf8')).not.toBe(payload.toString('utf8'))
    const dec = await call(port, token, '/decrypt', { data: enc.body.data! })
    expect(dec.status).toBe(200)
    expect(Buffer.from(dec.body.data!, 'base64').toString('utf8')).toBe(
      '{"access_token":"secret-access-token"}'
    )
  })

  it('leaks no plaintext into the encrypted blob', async () => {
    const { port, token } = (await startKeychainServer())!
    const payload = Buffer.from('secret-access-token', 'utf8')
    const enc = await call(port, token, '/encrypt', {
      data: payload.toString('base64')
    })
    expect(Buffer.from(enc.body.data!, 'base64').toString('utf8')).not.toContain(
      'secret-access-token'
    )
  })

  it('secureStorageAvailable probes the real backend', () => {
    resetSecureStorageCache()
    expect(secureStorageAvailable()).toBe(true)
  })
})

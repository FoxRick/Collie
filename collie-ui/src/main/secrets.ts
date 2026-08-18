/**
 * Secrets: provider API keys encrypted at rest with Electron safeStorage
 * (DPAPI on Windows, Keychain on macOS). Never written in plaintext (F020).
 *
 * The encrypted blobs live in userData/secrets.json. Decrypted values are
 * only ever held in memory and handed to the Python core over localhost IPC.
 * `loadSecrets` is one-shot: it decrypts once at the core handshake and then
 * returns nothing until the core process is (re)spawned, so a compromised
 * renderer cannot keep pulling plaintext keys on demand.
 */
import { app, safeStorage } from 'electron'
import { randomUUID } from 'crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { dirname, join } from 'path'

type SecretFile = Record<string, string> // provider -> base64(encrypted)

let secretsConsumed = false
const pendingSecretChanges = new Map<
  string,
  { provider: string; previous: string | undefined }
>()

let secureStorageKnown: boolean | null = null

/**
 * Defensive safeStorage probe: on some Linux setups (no keyring bus, or a
 * broken keyring daemon) `isEncryptionAvailable()` throws instead of
 * returning false. Never throw from the storage path — treat as unavailable
 * and let the UI explain. Cached so the probe (which can block) runs at
 * most once per process.
 */
export function secureStorageAvailable(): boolean {
  if (secureStorageKnown === null) {
    try {
      secureStorageKnown = safeStorage.isEncryptionAvailable()
    } catch {
      secureStorageKnown = false
    }
  }
  return secureStorageKnown
}

export function resetSecretsConsumption(): void {
  secretsConsumed = false
}

/** Test hook: drop the cached probe result so a fresh environment is re-read. */
export function resetSecureStorageCache(): void {
  secureStorageKnown = null
}

function secretsPath(): string {
  return join(app.getPath('userData'), 'secrets.json')
}

function readFile(): SecretFile {
  try {
    if (!existsSync(secretsPath())) return {}
    return JSON.parse(readFileSync(secretsPath(), 'utf-8')) as SecretFile
  } catch {
    return {}
  }
}

function writeFile(data: SecretFile): void {
  const path = secretsPath()
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(data, null, 2), 'utf-8')
}

export function saveSecret(provider: string, key: string): boolean {
  if (!provider || !key) return false
  if (!secureStorageAvailable()) return false
  const data = readFile()
  data[provider.toLowerCase()] = safeStorage.encryptString(key).toString('base64')
  writeFile(data)
  return true
}

export function stageSecretChange(
  provider: string,
  key: string
): { saved: boolean; transactionId?: string } {
  if (!provider || !key || !secureStorageAvailable()) return { saved: false }
  const canonical = provider.toLowerCase()
  const data = readFile()
  const previous = data[canonical]
  const transactionId = randomUUID()
  try {
    data[canonical] = safeStorage.encryptString(key).toString('base64')
    writeFile(data)
    pendingSecretChanges.set(transactionId, { provider: canonical, previous })
    return { saved: true, transactionId }
  } catch {
    if (previous === undefined) delete data[canonical]
    else data[canonical] = previous
    try {
      writeFile(data)
    } catch {
      // The caller receives false and keeps the core transaction rollbackable.
    }
    return { saved: false }
  }
}

export function finalizeSecretChange(transactionId: string): boolean {
  return pendingSecretChanges.delete(transactionId)
}

export function rollbackSecretChange(transactionId: string): boolean {
  const change = pendingSecretChanges.get(transactionId)
  if (!change) return false
  const data = readFile()
  if (change.previous === undefined) delete data[change.provider]
  else data[change.provider] = change.previous
  try {
    writeFile(data)
    pendingSecretChanges.delete(transactionId)
    return true
  } catch {
    return false
  }
}

export function deleteSecret(provider: string): boolean {
  const data = readFile()
  if (provider.toLowerCase() in data) {
    delete data[provider.toLowerCase()]
    writeFile(data)
    return true
  }
  return false
}

export function listSecretProviders(): string[] {
  return Object.keys(readFile())
}

/** Decrypt all stored secrets once, for the core startup handshake. */
export function loadSecrets(): Record<string, string> {
  if (secretsConsumed) return {}
  secretsConsumed = true
  const out: Record<string, string> = {}
  if (!secureStorageAvailable()) return out
  const data = readFile()
  for (const [provider, blob] of Object.entries(data)) {
    try {
      out[provider] = safeStorage.decryptString(Buffer.from(blob, 'base64'))
    } catch {
      // skip unreadable entries
    }
  }
  return out
}

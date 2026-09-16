import type {
  ConnectorCredentialSubmission,
  ConnectorImportPreview,
  ConnectorOperationResult,
  ConnectorSecretInput
} from '../shared/connectors'
import { commandWithCore } from './core-client'
import { randomUUID } from 'crypto'

const MAX_SECRET_LENGTH = 16_384
const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$/
const IMPORT_SECRET_TTL_MS = 5 * 60_000
const stagedImportSecrets = new Map<string, { secret: ConnectorSecretInput; expiresAt: number }>()

function stageImportSecret(secret: ConnectorSecretInput): string {
  const now = Date.now()
  for (const [handle, staged] of stagedImportSecrets) {
    if (staged.expiresAt <= now) stagedImportSecrets.delete(handle)
  }
  const handle = `cis_${randomUUID()}`
  stagedImportSecrets.set(handle, { secret, expiresAt: now + IMPORT_SECRET_TTL_MS })
  return handle
}

function consumeImportSecret(handle: unknown): ConnectorSecretInput | undefined {
  if (handle === undefined) return undefined
  if (typeof handle !== 'string') throw new Error('Imported credential handle is invalid')
  const staged = stagedImportSecrets.get(handle)
  stagedImportSecrets.delete(handle)
  if (!staged || staged.expiresAt <= Date.now()) {
    throw new Error('The imported credential preview expired. Choose the file again.')
  }
  return staged.secret
}

export function discardConnectorImportSecrets(handles: unknown): number {
  if (!Array.isArray(handles)) return 0
  let discarded = 0
  for (const handle of handles.slice(0, 100)) {
    if (typeof handle === 'string' && stagedImportSecrets.delete(handle)) discarded += 1
  }
  return discarded
}

function requiredText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== 'string' || !value.trim() || value.length > maxLength) {
    throw new Error(`${field} is invalid`)
  }
  return value
}

function secretPayload(value: unknown): ConnectorSecretInput | undefined {
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Credential input is invalid')
  }
  const secret = value as Record<string, unknown>
  const keys = Object.keys(secret)
  if (keys.length !== 1) throw new Error('Credential input is invalid')
  if ('token' in secret) {
    return { token: requiredText(secret.token, 'Token', MAX_SECRET_LENGTH) }
  }
  if ('headers' in secret && secret.headers && typeof secret.headers === 'object') {
    const entries = Object.entries(secret.headers as Record<string, unknown>)
    if (entries.length < 1 || entries.length > 16) throw new Error('Credential headers are invalid')
    const headers: Record<string, string> = {}
    for (const [name, raw] of entries) {
      if (!HEADER_NAME.test(name)) throw new Error('Credential header name is invalid')
      headers[name] = requiredText(raw, 'Credential header', MAX_SECRET_LENGTH)
    }
    return { headers }
  }
  throw new Error('Credential input is invalid')
}

/** Dedicated renderer -> main -> core path for fresh credential input. */
export async function submitConnectorCredentials(
  value: unknown
): Promise<ConnectorOperationResult> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Credential submission is invalid')
  }
  const submission = value as ConnectorCredentialSubmission
  const importHandle = submission.action === 'begin_auth'
    ? submission.import_secret_handle
    : undefined
  if (submission.secret !== undefined && importHandle !== undefined) {
    throw new Error('Choose one credential source')
  }
  const secret = submission.secret !== undefined
    ? secretPayload(submission.secret)
    : consumeImportSecret(importHandle)
  if (submission.action === 'begin_auth') {
    const definitionId = requiredText(submission.definition_id, 'Definition ID', 200)
    const displayName =
      submission.display_name === undefined
        ? undefined
        : requiredText(submission.display_name, 'Display name', 200)
    return commandWithCore('begin_definition_auth', {
      definition_id: definitionId,
      display_name: displayName,
      origin: submission.origin === 'chat' ? 'chat' : 'connectors_ui',
      secret
    }) as Promise<ConnectorOperationResult>
  }
  if (submission.action === 'reconnect') {
    const connectionId = requiredText(submission.connection_id, 'Connection ID', 200)
    return commandWithCore('reconnect_connector', {
      connection_id: connectionId,
      operation_id: submission.operation_id,
      operation_revision: submission.operation_revision,
      origin: submission.origin === 'chat' ? 'chat' : 'connectors_ui',
      secret
    }) as Promise<ConnectorOperationResult>
  }
  throw new Error('Credential submission action is invalid')
}

/** Imported configuration can contain inline secrets, so it uses the protected path too. */
export function previewConnectorImport(
  source: string | Record<string, unknown>
): Promise<ConnectorImportPreview> {
  if (typeof source !== 'string' && (!source || typeof source !== 'object' || Array.isArray(source))) {
    return Promise.reject(new Error('Connection import must be JSON text or an object'))
  }
  if (typeof source === 'string' && source.length > 1024 * 1024) {
    return Promise.reject(new Error('Connection import is larger than 1 MB'))
  }
  return commandWithCore('preview_connector_import', { source }).then((raw) => {
    const preview = raw as ConnectorImportPreview
    let parsed: unknown = source
    if (typeof source === 'string') {
      try { parsed = JSON.parse(source) } catch { return preview }
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return preview
    const root = parsed as Record<string, unknown>
    const serversValue = root.mcpServers ?? root
    if (!serversValue || typeof serversValue !== 'object' || Array.isArray(serversValue)) return preview
    const servers = serversValue as Record<string, unknown>
    return {
      ...preview,
      definitions: preview.definitions.map((entry) => {
        const rawEntry = servers[entry.key]
        if (!rawEntry || typeof rawEntry !== 'object' || Array.isArray(rawEntry)) return entry
        const data = rawEntry as Record<string, unknown>
        let importedSecret: ConnectorSecretInput | undefined
        if (data.headers && typeof data.headers === 'object' && !Array.isArray(data.headers)) {
          importedSecret = secretPayload({ headers: data.headers })
        } else {
          const token = data.token ?? data.apiKey ?? data.api_key ?? data.secret
          if (typeof token === 'string' && token) importedSecret = secretPayload({ token })
          else if (typeof data.authorization === 'string' && data.authorization) {
            importedSecret = secretPayload({ headers: { Authorization: data.authorization } })
          }
        }
        return importedSecret ? { ...entry, secret_handle: stageImportSecret(importedSecret) } : entry
      })
    }
  })
}

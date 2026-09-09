import { authenticatedUser, bodyText, env, json } from '../_shared/backend.ts'

const MAX_FILE_BYTES = 5 * 1024 * 1024

async function command(authorization: string, commandName: string, payload: Record<string, unknown>): Promise<any> {
  const response = await fetch(`${env('SUPABASE_URL')}/rest/v1/rpc/collaboration_files_command`, {
    method: 'POST',
    headers: { apikey: env('SUPABASE_ANON_KEY'), Authorization: authorization, 'Content-Type': 'application/json' },
    body: JSON.stringify({ p_command: commandName, p_payload: payload }),
    signal: AbortSignal.timeout(15000),
  })
  const value = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof value?.message === 'string' ? value.message : 'FILE_COMMAND_FAILED')
  return value
}

async function serviceRpc(name: string, body: Record<string, unknown>): Promise<any> {
  const key = env('SUPABASE_SERVICE_ROLE_KEY')
  const response = await fetch(`${env('SUPABASE_URL')}/rest/v1/rpc/${name}`, {
    method: 'POST', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal: AbortSignal.timeout(15000),
  })
  if (!response.ok) throw new Error('FILE_COORDINATION_FAILED')
  return await response.json()
}

async function readBytes(request: Request, expected: number): Promise<Uint8Array<ArrayBuffer>> {
  if (!Number.isSafeInteger(expected) || expected < 1 || expected > MAX_FILE_BYTES) throw new Error('INVALID_FILE')
  const declared = Number(request.headers.get('content-length') || 0)
  if (declared && declared !== expected) throw new Error('CONTENT_MISMATCH')
  const reader = request.body?.getReader()
  if (!reader) throw new Error('CONTENT_MISMATCH')
  const chunks: Uint8Array[] = []
  let length = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    length += value.byteLength
    if (length > expected || length > MAX_FILE_BYTES) { await reader.cancel(); throw new Error('CONTENT_MISMATCH') }
    chunks.push(value)
  }
  if (length !== expected) throw new Error('CONTENT_MISMATCH')
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  return bytes
}

async function digest(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
    .map((value) => value.toString(16).padStart(2, '0')).join('')
}

async function readStored(response: Response): Promise<Uint8Array<ArrayBuffer>> {
  const declared = Number(response.headers.get('content-length') || 0)
  if (declared > MAX_FILE_BYTES) throw new Error('STORED_FILE_INVALID')
  const reader = response.body?.getReader()
  if (!reader) throw new Error('STORED_FILE_INVALID')
  const chunks: Uint8Array[] = []
  let length = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    length += value.byteLength
    if (length > MAX_FILE_BYTES) { await reader.cancel(); throw new Error('STORED_FILE_INVALID') }
    chunks.push(value)
  }
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  return bytes
}

async function storage(path: string, init: RequestInit): Promise<Response> {
  const key = env('SUPABASE_SERVICE_ROLE_KEY')
  return fetch(`${env('SUPABASE_URL')}/storage/v1/object/collaboration-files/${path.split('/').map(encodeURIComponent).join('/')}`, {
    ...init, headers: { apikey: key, Authorization: `Bearer ${key}`, ...(init.headers || {}) }, signal: AbortSignal.timeout(30000),
  })
}

function safeName(value: unknown): string {
  const name = String(value || '').replace(/[\\/]/g, '_')
  return name && name.length <= 255 ? name : 'shared-file'
}

export async function handleCollaborationFiles(request: Request): Promise<Response> {
  if (!['POST', 'PUT', 'GET'].includes(request.method)) return json({ error: 'method_not_allowed' }, 405)
  const authorization = request.headers.get('authorization') || ''
  try {
    await authenticatedUser(request)
    if (request.method === 'POST') {
      const body = JSON.parse(await bodyText(request, 16384))
      if (!body || typeof body !== 'object' || !['reserve', 'list', 'cancel'].includes(body.action)) return json({ error: 'invalid_request' }, 400)
      const { action, ...payload } = body
      return json(await command(authorization, action, payload))
    }
    const fileId = new URL(request.url).searchParams.get('file_id') || ''
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(fileId)) return json({ error: 'invalid_file_id' }, 400)
    const metadata = await command(authorization, request.method === 'PUT' ? 'authorize_upload' : 'authorize_download', { file_id: fileId })
    if (request.method === 'GET') {
      const response = await storage(metadata.object_path, { method: 'GET' })
      if (!response.ok || !response.body) return json({ error: 'file_unavailable' }, response.status === 404 ? 404 : 503)
      return new Response(response.body, { headers: {
        'Content-Type': metadata.content_type || 'application/octet-stream', 'Content-Length': String(metadata.byte_length),
        'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(safeName(metadata.name))}`,
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
      } })
    }

    const incoming = await readBytes(request, Number(metadata.byte_length))
    const incomingDigest = await digest(incoming)
    if (incomingDigest !== metadata.sha256) {
      await serviceRpc('collaboration_files_verify', { p_file_id: fileId, p_byte_length: incoming.byteLength, p_sha256: incomingDigest })
      await serviceRpc('collaboration_files_cleanup_complete', { p_file_id: fileId })
      return json({ error: 'content_mismatch' }, 422)
    }
    const uploaded = await storage(metadata.object_path, { method: 'POST', headers: { 'Content-Type': metadata.content_type, 'x-upsert': 'false' }, body: incoming })
    if (!uploaded.ok) {
      if (uploaded.status !== 409) return json({ error: 'upload_retry_required' }, 503)
    }
    // Verify the bytes retrieved from Storage, including after the first write.
    // This also recovers a prior invocation that uploaded but lost its response.
    const existing = await storage(metadata.object_path, { method: 'GET' })
    if (!existing.ok) return json({ error: 'upload_retry_required' }, 503)
    const stored = await readStored(existing)
    const storedDigest = await digest(stored)
    const result = await serviceRpc('collaboration_files_verify', { p_file_id: fileId, p_byte_length: stored.byteLength, p_sha256: storedDigest })
    if (!result.ok) {
      const removed = await storage(metadata.object_path, { method: 'DELETE' }).catch(() => null)
      if (removed && (removed.ok || removed.status === 404)) {
        await serviceRpc('collaboration_files_cleanup_complete', { p_file_id: fileId })
      }
      return json({ error: 'content_mismatch' }, 422)
    }
    return json({ ...result, name: metadata.name, content_type: metadata.content_type, version: metadata.version })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'FILE_REQUEST_FAILED'
    const status = /FORBIDDEN|NOT_AUTHORIZED/.test(message) ? 403 : /AUTH_REQUIRED|Sign in/.test(message) ? 401 : /NOT_FOUND/.test(message) ? 404 : /QUOTA/.test(message) ? 409 : 400
    return json({ error: message }, status)
  }
}

if (import.meta.main) Deno.serve(handleCollaborationFiles)

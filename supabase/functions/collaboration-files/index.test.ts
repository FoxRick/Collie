import { assertEquals } from 'jsr:@std/assert@1.0.14'
import { handleCollaborationFiles } from './index.ts'

const originalFetch = globalThis.fetch
Deno.env.set('SUPABASE_URL', 'https://example.supabase.co')
Deno.env.set('SUPABASE_ANON_KEY', 'anon-test')
Deno.env.set('SUPABASE_SERVICE_ROLE_KEY', 'service-test')

function user(): Response {
  return Response.json({ id: '10000000-0000-4000-8000-000000000001', is_anonymous: false })
}

Deno.test('an account cannot upload another member file reservation', async () => {
  globalThis.fetch = (input) => {
    const url = String(input)
    if (url.endsWith('/auth/v1/user')) return Promise.resolve(user())
    return Promise.resolve(Response.json({ message: 'UPLOAD_NOT_AUTHORIZED' }, { status: 400 }))
  }
  try {
    const response = await handleCollaborationFiles(new Request(
      'https://edge.test/collaboration-files?file_id=40000000-0000-4000-8000-000000000001',
      { method: 'PUT', headers: { Authorization: 'Bearer account-token' }, body: new Uint8Array([1]) },
    ))
    assertEquals(response.status, 403)
    assertEquals((await response.json()).error, 'UPLOAD_NOT_AUTHORIZED')
  } finally { globalThis.fetch = originalFetch }
})

Deno.test('reservation quota errors are preserved for the desktop', async () => {
  globalThis.fetch = (input) => {
    const url = String(input)
    if (url.endsWith('/auth/v1/user')) return Promise.resolve(user())
    return Promise.resolve(Response.json({ message: 'FILE_QUOTA_EXCEEDED' }, { status: 400 }))
  }
  try {
    const response = await handleCollaborationFiles(new Request('https://edge.test/collaboration-files', {
      method: 'POST', headers: { Authorization: 'Bearer account-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'reserve', session_id: crypto.randomUUID() }),
    }))
    assertEquals(response.status, 409)
    assertEquals((await response.json()).error, 'FILE_QUOTA_EXCEEDED')
  } finally { globalThis.fetch = originalFetch }
})

Deno.test('claimed digest mismatch fails before object upload and releases the reservation', async () => {
  let storageWrites = 0
  let verificationCalls = 0
  globalThis.fetch = async (input, init) => {
    const url = String(input)
    if (url.endsWith('/auth/v1/user')) return user()
    if (url.endsWith('/rpc/collaboration_files_command')) return Response.json({
      file_id: '40000000-0000-4000-8000-000000000001', object_path: 'org/session/user/file/v1',
      byte_length: 3, sha256: '00'.repeat(32), content_type: 'application/octet-stream', name: 'bad.bin', version: 1,
    })
    if (url.endsWith('/rpc/collaboration_files_verify')) {
      verificationCalls++
      const body = JSON.parse(String(init?.body))
      assertEquals(body.p_byte_length, 3)
      return Response.json({ ok: false, status: 'failed' })
    }
    if (url.endsWith('/rpc/collaboration_files_cleanup_complete')) return Response.json({ ok: true })
    if (url.includes('/storage/v1/object/')) storageWrites++
    return Response.json({})
  }
  try {
    const response = await handleCollaborationFiles(new Request(
      'https://edge.test/collaboration-files?file_id=40000000-0000-4000-8000-000000000001',
      { method: 'PUT', headers: { Authorization: 'Bearer account-token', 'Content-Type': 'application/octet-stream' }, body: new Uint8Array([1, 2, 3]) },
    ))
    assertEquals(response.status, 422)
    assertEquals(verificationCalls, 1)
    assertEquals(storageWrites, 0)
  } finally { globalThis.fetch = originalFetch }
})

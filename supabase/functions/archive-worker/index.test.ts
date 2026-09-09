import { handleArchiveWorker } from './index.ts'

Deno.test('expired upload cleanup checkpoints only after successful storage deletion', async () => {
  const original = globalThis.fetch
  Deno.env.set('SUPABASE_URL', 'https://fixture.invalid')
  Deno.env.set('SUPABASE_SERVICE_ROLE_KEY', 'fixture-only')
  Deno.env.set('COLLABORATION_WORKER_SECRET', 'worker-fixture')
  try {
    for (const status of [503, 404, 200]) {
      const calls: string[] = []
      globalThis.fetch = async (url, init) => {
        if (String(url).includes('/storage/')) { calls.push('delete'); return Response.json({}, { status }) }
        const body = JSON.parse(String(init?.body)); calls.push(body.p_command)
        if (body.p_command === 'advance_archives') return Response.json({ file_cleanup: { objects: [{ file_id: 'f1', bucket: 'collaboration-files', path: 's1/f1' }] } })
        return Response.json(null)
      }
      const response = await handleArchiveWorker(new Request('https://fixture.invalid', { method: 'POST', headers: { Authorization: 'Bearer worker-fixture' } }))
      assert(response.status === (status === 503 ? 503 : 200))
      assert(calls.includes('checkpoint_file_cleanup') === (status !== 503))
      if (status !== 503) assert(calls.indexOf('delete') < calls.indexOf('checkpoint_file_cleanup'))
    }
  } finally {
    globalThis.fetch = original
    for (const key of ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'COLLABORATION_WORKER_SECRET']) Deno.env.delete(key)
  }
})

function assert(value: unknown): asserts value { if (!value) throw new Error('assertion failed') }
Deno.test('purge authorization is checked before object deletion and failed deletes never finish', async () => {
  const original = globalThis.fetch
  Deno.env.set('SUPABASE_URL', 'https://fixture.invalid')
  Deno.env.set('SUPABASE_SERVICE_ROLE_KEY', 'fixture-only')
  Deno.env.set('COLLABORATION_WORKER_SECRET', 'worker-fixture')
  try {
    for (const failure of ['check', 'delete']) {
      const calls: string[] = []
      let claimed = false
      globalThis.fetch = async (url, init) => {
        const body = JSON.parse(String(init?.body))
        if (String(url).includes('/storage/')) {
          calls.push('delete_object')
          return Response.json({}, { status: 503 })
        }
        calls.push(body.p_command)
        if (body.p_command === 'claim_purge') {
          if (claimed) return Response.json(null)
          claimed = true
          return Response.json({ session_id: 's1', fence: 1, manifest_digest: 'digest', objects: [{ id: 'f1', bucket: 'collaboration-files', path: 's1/f1' }] })
        }
        if (body.p_command === 'check_purge' && failure === 'check') return Response.json({}, { status: 403 })
        return Response.json({ ok: true })
      }
      const response = await handleArchiveWorker(new Request('https://fixture.invalid', { method: 'POST', headers: { Authorization: 'Bearer worker-fixture' } }))
      assert(response.status === 200)
      assert(!calls.includes('finish_purge'))
      assert(calls.includes('retry_purge'))
      assert(calls.includes('delete_object') === (failure === 'delete'))
      if (failure === 'delete') assert(calls.indexOf('check_purge') < calls.indexOf('delete_object'))
    }
  } finally {
    globalThis.fetch = original
    for (const key of ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'COLLABORATION_WORKER_SECRET']) Deno.env.delete(key)
  }
})

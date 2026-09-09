import { env, json } from '../_shared/backend.ts'
import { constantEqual } from '../_shared/slack-security.ts'

async function maintenance(command: string, payload: Record<string, unknown>): Promise<any> {
  const key = env('SUPABASE_SERVICE_ROLE_KEY')
  const response = await fetch(`${env('SUPABASE_URL')}/rest/v1/rpc/collaboration_maintenance_command`, {
    method: 'POST', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ p_command: command, p_payload: payload }), signal: AbortSignal.timeout(15000),
  })
  if (!response.ok) throw new Error('Archive coordination unavailable')
  return await response.json()
}

export async function handleArchiveWorker(request: Request): Promise<Response> {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)
  if (!constantEqual(request.headers.get('authorization') || '', `Bearer ${env('COLLABORATION_WORKER_SECRET')}`)) return json({ error: 'unauthorized' }, 401)
  try {
    const advanced = await maintenance('advance_archives', { limit: 10 })
    for (const object of advanced?.file_cleanup?.objects || []) {
      // These are expired, unpublished uploads; SQL excludes verified shared files.
      if (object.bucket !== 'collaboration-files' || typeof object.path !== 'string' || !object.path || object.path.includes('..') || !object.file_id) throw new Error('Invalid expired upload')
      const key = env('SUPABASE_SERVICE_ROLE_KEY')
      const response = await fetch(`${env('SUPABASE_URL')}/storage/v1/object/${object.bucket}`, {
        method: 'DELETE', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefixes: [object.path] }), signal: AbortSignal.timeout(15000),
      })
      if (!response.ok && response.status !== 404) throw new Error('Expired upload deletion needs retry')
      await maintenance('checkpoint_file_cleanup', { file_id: object.file_id })
    }
    let completed = 0
    for (let i = 0; i < 10; i++) {
      const job = await maintenance('claim_purge', {})
      if (!job?.session_id) break
      try {
        for (const object of job.objects || []) {
          // Authorization is rechecked before each irreversible external step.
          await maintenance('check_purge', { session_id: job.session_id, fence: job.fence, manifest_digest: job.manifest_digest, object_id: object.id })
          if (object.bucket !== 'collaboration-files' || typeof object.path !== 'string' || !object.path || object.path.includes('..')) throw new Error('Invalid archive object')
          const key = env('SUPABASE_SERVICE_ROLE_KEY')
          const response = await fetch(`${env('SUPABASE_URL')}/storage/v1/object/${object.bucket}`, {
            method: 'DELETE', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ prefixes: [object.path] }), signal: AbortSignal.timeout(15000),
          })
          if (!response.ok && response.status !== 404) throw new Error('Object deletion needs retry')
          await maintenance('checkpoint_purge', { session_id: job.session_id, fence: job.fence, object_id: object.id, manifest_digest: job.manifest_digest })
        }
        await maintenance('finish_purge', { session_id: job.session_id, fence: job.fence, manifest_digest: job.manifest_digest })
        completed++
      } catch {
        await maintenance('retry_purge', { session_id: job.session_id, fence: job.fence })
      }
    }
    return json({ completed })
  } catch { return json({ error: 'archive_worker_retry_required' }, 503) }
}
if (import.meta.main) Deno.serve(handleArchiveWorker)

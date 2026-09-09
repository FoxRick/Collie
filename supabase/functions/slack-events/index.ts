import { bodyText, env, json, rpc } from '../_shared/backend.ts'
import { verifySlack } from '../_shared/slack-security.ts'

export async function handleSlackEvent(request: Request): Promise<Response> {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)
  try {
    const raw = await bodyText(request)
    if (!await verifySlack(raw, request.headers.get('x-slack-request-timestamp'), request.headers.get('x-slack-signature'), env('SLACK_SIGNING_SECRET'))) return json({ error: 'invalid_signature' }, 401)
    const event = JSON.parse(raw)
    if (event.type === 'url_verification' && typeof event.challenge === 'string') return json({ challenge: event.challenge })
    if (event.type !== 'event_callback' || typeof event.event_id !== 'string' || typeof event.team_id !== 'string') return json({ error: 'invalid_event' }, 400)
    // Only durable intake occurs before acknowledgement. All API/model work is in the worker.
    await rpc('enqueue_event', { team_id: event.team_id, event_id: event.event_id, event }, 2000)
    return json({ ok: true })
  } catch { return json({ error: 'retry_delivery' }, 503) }
}
if (import.meta.main) Deno.serve(handleSlackEvent)

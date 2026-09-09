import { bodyText, env, json, rpc } from '../_shared/backend.ts'
import { sha256, verifySlack } from '../_shared/slack-security.ts'

Deno.serve(async request => {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)
  try {
    const raw = await bodyText(request)
    if (!await verifySlack(raw, request.headers.get('x-slack-request-timestamp'), request.headers.get('x-slack-signature'), env('SLACK_SIGNING_SECRET'))) return json({ error: 'invalid_signature' }, 401)
    const payload = JSON.parse(new URLSearchParams(raw).get('payload') || '{}')
    if (!payload.team?.id || !payload.user?.id) return json({ error: 'invalid_interaction' }, 400)
    // Slack buttons never approve private tools. The desktop must perform that approval.
    await rpc('enqueue_event', { team_id: payload.team.id, event_id: `interaction:${await sha256(raw)}`, event: { type: 'interaction', payload } }, 2000)
    return json({ response_type: 'ephemeral', text: 'Open Collie to review this request with your own permissions.' })
  } catch { return json({ error: 'retry_delivery' }, 503) }
})

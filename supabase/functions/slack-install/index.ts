import { authenticatedUser, bodyText, env, json, rpc } from '../_shared/backend.ts'
import { sha256 } from '../_shared/slack-security.ts'

Deno.serve(async request => {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)
  try {
    const actor_id = await authenticatedUser(request)
    const { org_id } = JSON.parse(await bodyText(request, 4096))
    if (typeof org_id !== 'string') return json({ error: 'invalid_organization' }, 400)
    const state = Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('')
    await rpc('begin_install', { actor_id, org_id, state_hash: await sha256(state), expires_at: new Date(Date.now() + 600000).toISOString() })
    const url = new URL('https://slack.com/oauth/v2/authorize')
    url.search = new URLSearchParams({ client_id: env('SLACK_CLIENT_ID'), scope: 'app_mentions:read,chat:write,channels:history,channels:read,users:read', state, redirect_uri: env('SLACK_REDIRECT_URI') }).toString()
    return json({ url: url.toString() })
  } catch { return json({ error: 'slack_install_unavailable' }, 403) }
})

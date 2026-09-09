import { env, json, rpc } from '../_shared/backend.ts'
import { sealToken, sha256 } from '../_shared/slack-security.ts'

Deno.serve(async request => {
  if (request.method !== 'GET') return json({ error: 'method_not_allowed' }, 405)
  try {
    const query = new URL(request.url).searchParams
    const state = query.get('state') || ''
    const code = query.get('code') || ''
    if (!/^[a-f0-9]{64}$/.test(state) || !code || code.length > 2048) return json({ error: 'invalid_callback' }, 400)
    // Consume before exchanging; a replay cannot bind an installation twice.
    const pending = await rpc('consume_install', { state_hash: await sha256(state) })
    const response = await fetch('https://slack.com/api/oauth.v2.access', {
      method: 'POST', body: new URLSearchParams({ code, client_id: env('SLACK_CLIENT_ID'), client_secret: env('SLACK_CLIENT_SECRET'), redirect_uri: env('SLACK_REDIRECT_URI') }),
      signal: AbortSignal.timeout(10000),
    })
    const result = await response.json()
    if (!response.ok || !result.ok || !result.access_token || !result.team?.id || result.is_enterprise_install) throw new Error('Install unavailable')
    await rpc('save_install', { org_id: pending.org_id, actor_id: pending.actor_id, state_hash: await sha256(state), team_id: result.team.id, slack_user_id: result.authed_user?.id, bot_user_id: result.bot_user_id, token_ciphertext: await sealToken(result.access_token, env('SLACK_TOKEN_ENCRYPTION_KEY'), result.team.id) })
    return new Response('<!doctype html><meta charset="utf-8"><title>Collie and Slack</title><h1>Slack is connected</h1><p>Return to Collie to select a channel and link your own account. Shared conversations require linked desktop participants. Archiving in Collie does not delete Slack messages.</p>', { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'" } })
  } catch { return json({ error: 'slack_callback_failed_start_again' }, 400) }
})

import { env, json, rpc } from '../_shared/backend.ts'
import { constantEqual, openToken } from '../_shared/slack-security.ts'

class RetryLater extends Error { constructor(public seconds: number) { super('retry_later') } }
async function slack(method: string, token: string, data: Record<string, unknown>): Promise<any> {
  const response = await fetch(`https://slack.com/api/${method}`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(data), signal: AbortSignal.timeout(10000),
  })
  if (response.status === 429) throw new RetryLater(Math.max(1, Math.min(86400, Number(response.headers.get('retry-after')) || 60)))
  const result = await response.json()
  if (!response.ok || !result.ok) throw new Error('Slack result requires reconciliation')
  return result
}

/** Scheduled, bounded worker. Inbox and outbound claims are durable/fenced in SQL. */
Deno.serve(async request => {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)
  if (!constantEqual(request.headers.get('authorization') || '', `Bearer ${env('COLLABORATION_WORKER_SECRET')}`)) return json({ error: 'unauthorized' }, 401)
  let incoming = 0
  let outgoing = 0
  try {
    for (let i = 0; i < 10; i++) {
      const job = await rpc('claim_inbox', {})
      if (!job?.id) break
      try {
        const event = job.event?.event
        if (event?.type === 'app_uninstalled' || event?.type === 'tokens_revoked') {
          await rpc('uninstall', { team_id: job.team_id })
        } else if (event && ['app_mention', 'message'].includes(event.type) && !event.bot_id && !event.subtype && !event.hidden) {
          const token = await openToken(job.token_ciphertext, env('SLACK_TOKEN_ENCRYPTION_KEY'), job.team_id)
          const [user, channel] = await Promise.all([
            slack('users.info', token, { user: event.user }),
            slack('conversations.info', token, { channel: event.channel }),
          ])
          // External organizations/guests are outside the first pilot authority model.
          if (!user.user?.deleted && !user.user?.is_bot && !user.user?.is_restricted && !user.user?.is_ultra_restricted && !user.user?.is_stranger && user.user?.team_id === job.team_id && !channel.channel?.is_shared && !channel.channel?.is_ext_shared) {
            const link = String(event.text || '').match(/^(?:<@[A-Z0-9]+>\s*)?link\s+([A-Za-z0-9_-]{20,128})\s*$/i)
            if (link) await rpc('redeem_link', { team_id: job.team_id, slack_user_id: event.user, code: link[1] })
            else {
              const accepted = await rpc('ingest_message', { installation_id: job.installation_id, channel_id: event.channel, thread_ts: event.thread_ts || event.ts, slack_user_id: event.user, event_id: job.event_id, content: event.text || '', source_ts: event.ts, is_mention: event.type === 'app_mention' })
              if (accepted?.waiting_for_executor) throw new RetryLater(60)
            }
          }
        }
        await rpc('finish_inbox', { id: job.id, fence: job.fence, status: 'complete' })
        incoming++
      } catch (error) {
        await rpc('finish_inbox', { id: job.id, fence: job.fence, status: 'retry', retry_after: error instanceof RetryLater ? error.seconds : 60 })
      }
    }
    for (let i = 0; i < 10; i++) {
      const job = await rpc('claim_outbox', {})
      if (!job?.id) break
      try {
        const token = await openToken(job.token_ciphertext, env('SLACK_TOKEN_ENCRYPTION_KEY'), job.team_id)
        if (job.status === 'uncertain') {
          // Rate-aware bounded backfill, used only to reconcile our own operation.
          // Never import a purged transcript from Slack history.
          const history = await slack('conversations.replies', token, { channel: job.channel_id, ts: job.thread_ts, limit: 15, cursor: job.reconcile_cursor || undefined })
          const delivered = history.messages?.find((message: any) => message.client_msg_id === job.id)
          const next = history.response_metadata?.next_cursor || ''
          await rpc('complete_outbox', { id: job.id, fence: job.fence, status: delivered ? 'complete' : next ? 'uncertain' : 'manual_review', slack_ts: delivered?.ts, reconcile_cursor: next, retry_after: 60 })
          continue
        }
        // A deterministic client_msg_id lets reconciliation identify an uncertain send.
        const result = await slack('chat.postMessage', token, { channel: job.channel_id, thread_ts: job.thread_ts, text: job.content, client_msg_id: job.id, unfurl_links: false, unfurl_media: false })
        await rpc('complete_outbox', { id: job.id, fence: job.fence, status: 'complete', slack_ts: result.ts })
        outgoing++
      } catch (error) {
        // Network failures can follow successful delivery. Quarantine for reconciliation,
        // never blindly repeat an external write on the basis of an absent response.
        await rpc('complete_outbox', { id: job.id, fence: job.fence, status: error instanceof RetryLater ? 'retry' : 'uncertain', retry_after: error instanceof RetryLater ? error.seconds : 60 })
      }
    }
    return json({ incoming, outgoing })
  } catch { return json({ error: 'worker_retry_required' }, 503) }
})

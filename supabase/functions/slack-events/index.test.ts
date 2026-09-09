import { handleSlackEvent } from './index.ts'
import { hex } from '../_shared/slack-security.ts'

function assert(value: unknown): asserts value { if (!value) throw new Error('assertion failed') }
async function signed(body: string): Promise<Request> {
  const timestamp = String(Math.floor(Date.now() / 1000))
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('fixture'), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const signature = 'v0=' + hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`v0:${timestamp}:${body}`)))
  return new Request('https://example.invalid/slack-events', { method: 'POST', body, headers: { 'x-slack-request-timestamp': timestamp, 'x-slack-signature': signature } })
}
Deno.test('durable intake is required before acknowledging a signed Slack event', async () => {
  Deno.env.set('SLACK_SIGNING_SECRET', 'fixture')
  Deno.env.set('SUPABASE_URL', 'https://fixture.invalid')
  Deno.env.set('SUPABASE_SERVICE_ROLE_KEY', 'fixture-only')
  const original = globalThis.fetch
  const calls: any[] = []
  const body = JSON.stringify({ type: 'event_callback', team_id: 'T1', event_id: 'Ev1', event: { type: 'app_mention', text: 'hello' } })
  try {
    globalThis.fetch = async (_url, init) => {
      calls.push(JSON.parse(String(init?.body)))
      return Response.json({ ok: true })
    }
    assert((await handleSlackEvent(await signed(body))).status === 200)
    assert(calls.length === 1 && calls[0].p_command === 'enqueue_event')
    assert(calls[0].p_payload.event.event.text === 'hello')
    globalThis.fetch = async () => Response.json({}, { status: 503 })
    assert((await handleSlackEvent(await signed(body))).status === 503)
    const forged = new Request('https://example.invalid', { method: 'POST', body })
    assert((await handleSlackEvent(forged)).status === 401)
  } finally {
    globalThis.fetch = original
    for (const key of ['SLACK_SIGNING_SECRET', 'SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY']) Deno.env.delete(key)
  }
})

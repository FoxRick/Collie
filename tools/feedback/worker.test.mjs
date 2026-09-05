import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import worker from './worker.mjs'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
const body = { id: 'b9d6f334-3d32-4d57-87d3-8ed1056ce4b0', message: 'Please add shortcuts.' }
const env = () => ({
  FEEDBACK_RATE_LIMITER: { limit: async () => ({ success: true }) },
  SUPABASE_URL: 'https://example.supabase.co', SUPABASE_SECRET_KEY: 'sb_secret_test',
  RESEND_API_KEY: 'test', FEEDBACK_FROM: 'Collie <hello@example.com>', FEEDBACK_TO: 'team@example.com'
})
const request = (payload = body) => new Request('https://feedback.heycollie.com/api/feedback', {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'CF-Connecting-IP': '192.0.2.1' },
  body: JSON.stringify(payload)
})

test('stores privately, then sends plain text to the configured inbox', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => { calls.push([String(url), options]); return Response.json({ id: 'accepted' }) }
  const response = await worker.fetch(request({ ...body, email: 'ignored', to: 'attacker', logs: 'ignored' }), env())
  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), { ok: true })
  assert.deepEqual(JSON.parse(calls[0][1].body), body)
  assert.equal(calls[0][1].headers.apikey, 'sb_secret_test')
  assert.deepEqual(JSON.parse(calls[1][1].body), {
    from: 'Collie <hello@example.com>', to: ['team@example.com'], subject: 'New Collie feedback', text: body.message
  })
  assert.equal(calls[1][1].headers['Idempotency-Key'], `feedback/${body.id}`)
})

test('fails closed with missing configuration, missing IP, or unavailable limiter', async () => {
  globalThis.fetch = () => { throw new Error('must not send') }
  assert.equal((await worker.fetch(request(), {})).status, 503)
  const noIp = request(); noIp.headers.delete('CF-Connecting-IP')
  assert.equal((await worker.fetch(noIp, env())).status, 503)
  const unavailable = env(); unavailable.FEEDBACK_RATE_LIMITER.limit = async () => { throw new Error('down') }
  assert.equal((await worker.fetch(request(), unavailable)).status, 503)
})

test('rate limits before storing or mailing', async () => {
  let called = false
  globalThis.fetch = async () => { called = true }
  const limited = env(); limited.FEEDBACK_RATE_LIMITER.limit = async () => ({ success: false })
  const result = await worker.fetch(request(), limited)
  assert.equal(result.status, 429)
  assert.equal(result.headers.get('Retry-After'), '60')
  assert.equal(called, false)
})

test('rejects blank, oversized, malformed and unbounded bodies', async () => {
  for (const payload of [null, {}, { ...body, id: 'bad' }, { ...body, message: ' \n ' },
    { ...body, message: 'x'.repeat(4001) }, { ...body, extra: 'x'.repeat(33_000) }]) {
    assert.equal((await worker.fetch(request(payload), env())).status, 400)
  }
  const malformed = new Request(request(), { body: '{' })
  assert.equal((await worker.fetch(malformed, env())).status, 400)
})

test('never sends email when persistence fails', async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return new Response('', { status: 500 }) }
  assert.equal((await worker.fetch(request(), env())).status, 503)
  assert.equal(calls, 1)
})

test('failed email is reported as a retryable failure; retry keeps original ID and content', async () => {
  const responses = [new Response('', { status: 201 }), new Response('', { status: 500 }),
    new Response('', { status: 409 }), Response.json([{ message: body.message }]), Response.json({ id: 'mail' })]
  const mailKeys = []
  globalThis.fetch = async (url, options) => {
    if (String(url).includes('resend.com')) mailKeys.push(options.headers['Idempotency-Key'])
    return responses.shift()
  }
  assert.equal((await worker.fetch(request(), env())).status, 503)
  assert.equal((await worker.fetch(request(), env())).status, 200)
  assert.deepEqual(mailKeys, [`feedback/${body.id}`, `feedback/${body.id}`])
})

test('rejects reuse of an ID with different content without sending mail', async () => {
  let count = 0
  globalThis.fetch = async () => ++count === 1
    ? new Response('', { status: 409 }) : Response.json([{ message: 'original message' }])
  assert.equal((await worker.fetch(request(), env())).status, 409)
  assert.equal(count, 2)
})

test('upstream exceptions return a generic response', async () => {
  globalThis.fetch = async () => { throw new Error('secret data') }
  const response = await worker.fetch(request(), env())
  assert.equal(response.status, 503)
  assert.equal((await response.text()).includes('secret'), false)
})

test('does not expose a read endpoint or accept browser form posts', async () => {
  assert.equal((await worker.fetch(new Request('https://feedback.heycollie.com/api/feedback'), env())).status, 405)
  const form = request(); form.headers.set('Content-Type', 'text/plain')
  assert.equal((await worker.fetch(form, env())).status, 415)
})

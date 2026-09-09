import { hex, openToken, sealToken, verifySlack } from './slack-security.ts'

function assert(value: unknown, message = 'assertion failed'): asserts value { if (!value) throw new Error(message) }
Deno.test('Slack signs exact raw bytes; rejects stale and altered requests', async () => {
  const timestamp = '1788923000'
  const body = '{ "text":"hello 世界" }'
  const secret = 'test-only-signing-secret'
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const signature = 'v0=' + hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`v0:${timestamp}:${body}`)))
  assert(await verifySlack(body, timestamp, signature, secret, Number(timestamp) * 1000))
  assert(!await verifySlack(body + ' ', timestamp, signature, secret, Number(timestamp) * 1000))
  assert(!await verifySlack(body, timestamp, signature, secret, Number(timestamp) * 1000 + 301000))
  assert(!await verifySlack(body, 'NaN', signature, secret))
})
Deno.test('Installation encryption binds the workspace and detects corrupt ciphertext', async () => {
  const key = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
  const encrypted = await sealToken('test-only-bot-credential', key, 'T1')
  assert(!encrypted.includes('test-only'))
  assert(await openToken(encrypted, key, 'T1') === 'test-only-bot-credential')
  for (const [value, team] of [[encrypted, 'T2'], [encrypted.slice(0, -4) + 'AAAA', 'T1']]) {
    let rejected = false
    try { await openToken(value, key, team) } catch { rejected = true }
    assert(rejected)
  }
})

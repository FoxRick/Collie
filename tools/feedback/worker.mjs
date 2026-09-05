const MAX_BODY_BYTES = 32_768
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function json(body, status = 200) {
  return Response.json(body, { status, headers: {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    ...(status === 429 ? { 'Retry-After': '60' } : {})
  } })
}

async function readBody(request) {
  if (!request.body) return null
  const reader = request.body.getReader()
  const chunks = []
  let size = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > MAX_BODY_BYTES) {
        await reader.cancel()
        return null
      }
      chunks.push(value)
    }
    const bytes = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return JSON.parse(new TextDecoder().decode(bytes))
  } catch {
    return null
  } finally {
    reader.releaseLock()
  }
}

// Public, anonymous intake. All privileged credentials remain in Worker secrets.
export default {
  async fetch(request, env) {
    if (new URL(request.url).pathname !== '/api/feedback') return json({ ok: false }, 404)
    if (request.method !== 'POST') return json({ ok: false }, 405)
    if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
      return json({ ok: false }, 415)
    }
    const ip = request.headers.get('CF-Connecting-IP')
    if (!ip || !env.FEEDBACK_RATE_LIMITER || !env.SUPABASE_URL ||
        !env.SUPABASE_SECRET_KEY || !env.RESEND_API_KEY || !env.FEEDBACK_TO || !env.FEEDBACK_FROM) {
      return json({ ok: false }, 503)
    }
    try {
      // Fail closed: an unavailable limiter must not turn into an open mail relay.
      const { success } = await env.FEEDBACK_RATE_LIMITER.limit({ key: ip })
      if (!success) return json({ ok: false }, 429)
      const body = await readBody(request)
      if (!body || typeof body.id !== 'string' || !UUID.test(body.id) ||
          typeof body.message !== 'string' || !body.message.trim() || body.message.length > 4000) {
        return json({ ok: false }, 400)
      }
      const id = body.id.toLowerCase()
      const message = body.message.trim()
      const base = new URL(env.SUPABASE_URL)
      if (base.protocol !== 'https:' || base.username || base.password) return json({ ok: false }, 503)
      const endpoint = new URL('/rest/v1/app_feedback', base)
      const headers = { apikey: env.SUPABASE_SECRET_KEY, 'Content-Type': 'application/json' }
      // Legacy service_role keys additionally require the bearer header.
      if (!env.SUPABASE_SECRET_KEY.startsWith('sb_secret_')) {
        headers.Authorization = `Bearer ${env.SUPABASE_SECRET_KEY}`
      }
      const stored = await fetch(endpoint, {
        method: 'POST', headers: { ...headers, Prefer: 'return=minimal' },
        body: JSON.stringify({ id, message }), redirect: 'error', signal: AbortSignal.timeout(8000)
      })
      if (stored.status === 409) {
        // A retry must use the original content. Never overwrite an accepted message.
        endpoint.search = new URLSearchParams({ id: `eq.${id}`, select: 'message' }).toString()
        const existing = await fetch(endpoint, {
          headers, redirect: 'error', signal: AbortSignal.timeout(8000)
        })
        if (!existing.ok) return json({ ok: false }, 503)
        const rows = await existing.json()
        if (!Array.isArray(rows) || rows.length !== 1 || rows[0].message !== message) {
          return json({ ok: false }, 409)
        }
      } else if (!stored.ok) {
        return json({ ok: false }, 503)
      }
      const emailed = await fetch('https://api.resend.com/emails', {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(8000),
        headers: {
          Authorization: `Bearer ${env.RESEND_API_KEY}`,
          'Content-Type': 'application/json',
          'Idempotency-Key': `feedback/${id}`
        },
        body: JSON.stringify({
          from: env.FEEDBACK_FROM, to: [env.FEEDBACK_TO],
          subject: 'New Collie feedback', text: message
        })
      })
      // Database save alone is insufficient: the inbox notification must be accepted too.
      if (!emailed.ok) return json({ ok: false }, 503)
      return json({ ok: true })
    } catch {
      // Never log user text, addresses, IPs, provider responses, or credentials.
      return json({ ok: false }, 503)
    }
  }
}

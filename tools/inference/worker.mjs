// Collie managed inference: one explicitly approved free Groq route, no paid fallback.
// Account grants + shared quota are reserved atomically BEFORE contacting Groq.
const MODEL = 'openai/gpt-oss-20b'
const MAX_BODY = 128 * 1024
const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
})
const error = (status, code, message) => json(status, { error: { code, message } })

export async function readLimited(request) {
  const reader = request.body?.getReader()
  if (!reader) throw new Error('empty')
  let size = 0
  const chunks = []
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > MAX_BODY) throw new Error('large')
      chunks.push(value)
    }
  } finally { await reader.cancel().catch(() => {}) }
  const bytes = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  return new TextDecoder().decode(bytes)
}

export function payloadFor(body) {
  if (!body || typeof body !== 'object' || body.model !== 'collie-auto' ||
      !Array.isArray(body.messages) || body.messages.length < 1 || body.messages.length > 100) {
    throw new Error('invalid')
  }
  if (body.messages.some(m => !m || !['system','developer','user','assistant','tool'].includes(m.role) ||
      (m.content != null && typeof m.content !== 'string'))) throw new Error('text_only')
  if (body.tools != null && (!Array.isArray(body.tools) || body.tools.length > 32 ||
      body.tools.some(t => t?.type !== 'function' || !t.function ||
        typeof t.function.name !== 'string'))) throw new Error('tools')
  // Never forward client-supplied upstream, credentials, price, or arbitrary extras.
  const output = body.max_completion_tokens ?? body.max_tokens ?? 512
  if (!Number.isInteger(output) || output < 1 || output > 1024) throw new Error('output')
  const messages = body.messages.map(m => Object.fromEntries(
    ['role','content','name','tool_call_id','tool_calls'].filter(k => m[k] !== undefined).map(k => [k,m[k]])))
  const payload = { model: MODEL, messages, max_completion_tokens: output,
    stream: body.stream === true, ...(body.tools?.length ? { tools: body.tools } : {}) }
  // UTF-8 bytes bound text tokens, plus conservative template/tool framing headroom.
  // This is charged reservation units, not a claim of exact vendor token usage.
  const units = new TextEncoder().encode(JSON.stringify({messages, tools: payload.tools})).length + 2048 + output
  if (units > 6000) throw new Error('context')
  return { payload, units }
}

export function createWorker(fetcher = fetch) {
  async function rpc(env, name, data) {
    const res = await fetcher(env.SUPABASE_URL + '/rest/v1/rpc/' + name, {
      method: 'POST', headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: 'Bearer ' + env.SUPABASE_SERVICE_ROLE_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify(data), redirect: 'error', signal: AbortSignal.timeout(10000)
    })
    if (!res.ok) throw new Error('accounting')
    return res.json()
  }
  return {
    async fetch(request, env, ctx) {
      if (env.FREE_TIER_CONFIRMED !== 'true' || !env.GROQ_API_KEY ||
          !env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
        return error(503, 'not_available', 'Collie AI is not available yet.')
      }
      const path = new URL(request.url).pathname
      if (!['/v1/me/entitlements','/v1/chat/completions'].includes(path)) return error(404,'not_found','Not found.')
      if ((path.endsWith('entitlements') && request.method !== 'GET') ||
          (path.endsWith('completions') && request.method !== 'POST')) return error(405,'method','Method not allowed.')
      const auth = request.headers.get('authorization')
      if (!auth?.startsWith('Bearer ')) return error(401,'auth_required','Sign in to Collie.')
      try {
        // Online validation also rejects deleted users; never trust decoded JWT claims.
        const identity = await fetcher(env.SUPABASE_URL + '/auth/v1/user', {
          headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: auth },
          redirect: 'error', signal: AbortSignal.timeout(10000)
        })
        if (!identity.ok) return error(401,'auth_required','Sign in to Collie.')
        const user = await identity.json()
        if (typeof user.id !== 'string' || !user.email_confirmed_at) return error(403,'account_required','A verified Collie account is required.')
        if (path.endsWith('entitlements')) {
          return json(200, await rpc(env,'collie_inference_allowance',{p_user:user.id}))
        }
        let parsed
        try { parsed = payloadFor(JSON.parse(await readLimited(request))) }
        catch { return error(400,'unsupported_request','Use a shorter text request with up to 1,024 output tokens.') }
        const id = request.headers.get('idempotency-key')
        if (!id || !/^[a-zA-Z0-9-]{16,80}$/.test(id)) return error(400,'request_id_required','Request ID is required.')
        const admission = await rpc(env,'collie_inference_reserve',{
          p_user:user.id, p_request:id, p_units:parsed.units
        })
        if (!admission.allowed) {
          return error(admission.reason === 'duplicate' ? 409 : 402,
            'allowance_exhausted','Included AI is unavailable or its allowance is used up. No paid fallback was used.')
        }
        let upstream
        try {
          upstream = await fetcher('https://api.groq.com/openai/v1/chat/completions', {
            method:'POST', headers:{Authorization:'Bearer '+env.GROQ_API_KEY,'Content-Type':'application/json'},
            body:JSON.stringify(parsed.payload), redirect:'error',
            signal:AbortSignal.any([request.signal, AbortSignal.timeout(90000)])
          })
        } catch {
          // Reservations remain spent on uncertain attempts; never retry billable work blindly.
          return error(503,'capacity_unavailable','Collie AI is unavailable. Please try again later.')
        }
        if (!upstream.ok) {
          await upstream.body?.cancel()
          return error(503,'capacity_unavailable','Collie AI is unavailable. No paid fallback was used.')
        }
        // Stream directly. Reservations are durable already, even if client/Worker disconnects.
        return new Response(upstream.body, {headers:{
          'Content-Type': parsed.payload.stream ? 'text/event-stream' : 'application/json',
          'Cache-Control':'no-store'
        }})
      } catch {
        return error(503,'capacity_unavailable','Collie AI is unavailable. Your own providers still work.')
      }
    }
  }
}
export default createWorker()

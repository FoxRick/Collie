export type Payload = Record<string, unknown>
export function env(name: string): string {
  const value = Deno.env.get(name)
  if (!value) throw new Error('Service configuration is incomplete')
  return value
}
export function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { 'Cache-Control': 'no-store' } })
}
export async function bodyText(request: Request, limit = 262144): Promise<string> {
  if (Number(request.headers.get('content-length') || 0) > limit) throw new Error('Request too large')
  const reader = request.body?.getReader()
  if (!reader) return ''
  const chunks: Uint8Array[] = []
  let length = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    length += value.byteLength
    if (length > limit) { await reader.cancel(); throw new Error('Request too large') }
    chunks.push(value)
  }
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
}
export async function rpc(command: string, payload: Payload, timeout = 15000): Promise<any> {
  const key = env('SUPABASE_SERVICE_ROLE_KEY')
  const response = await fetch(`${env('SUPABASE_URL')}/rest/v1/rpc/collaboration_slack_command`, {
    method: 'POST', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ p_command: command, p_payload: payload }), signal: AbortSignal.timeout(timeout),
  })
  if (!response.ok) throw new Error('Coordination unavailable')
  return await response.json()
}
export async function authenticatedUser(request: Request): Promise<string> {
  const authorization = request.headers.get('authorization') || ''
  if (!/^Bearer \S+$/.test(authorization)) throw new Error('Sign in required')
  const response = await fetch(`${env('SUPABASE_URL')}/auth/v1/user`, {
    headers: { apikey: env('SUPABASE_ANON_KEY'), Authorization: authorization }, signal: AbortSignal.timeout(10000),
  })
  if (!response.ok) throw new Error('Sign in required')
  const user = await response.json()
  if (!user.id || user.is_anonymous) throw new Error('Sign in required')
  return user.id
}

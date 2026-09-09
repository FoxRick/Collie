/** Collie-funded inference. Account credentials never cross into the renderer/core. */
import type { IncomingMessage, ServerResponse } from 'http'
import { once } from 'events'
import { randomUUID } from 'crypto'
import { getAccountState, getStoredSession } from './account-auth'

const endpoint = (process.env.COLLIE_INFERENCE_URL ?? '').trim()
const MAX_BODY = 128 * 1024

export interface ManagedStatus {
  configured: boolean
  available: boolean
  signedIn: boolean
  remaining: number
  limit: number
  resetsAt: string | null
  message: string
}

function baseUrl(): string {
  if (!endpoint) throw new Error('Collie AI is not available in this build yet.')
  const url = new URL(endpoint)
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new Error('Collie AI is not configured correctly.')
  }
  return url.toString().replace(/\/$/, '')
}

async function accessToken(): Promise<string> {
  const state = await getAccountState()
  const session = getStoredSession()
  if (!state.signedIn || !session?.access_token) throw new Error('Sign in to Collie to use Collie AI.')
  return session.access_token
}

export async function managedStatus(): Promise<ManagedStatus> {
  const empty: ManagedStatus = { configured: Boolean(endpoint), available: false, signedIn: false, remaining: 0,
    limit: 0, resetsAt: null, message: 'Collie AI is not available in this build yet.' }
  if (!endpoint) return empty
  let token: string
  try {
    token = await accessToken()
  } catch {
    // Signed out (or no saved session) — a build with an endpoint but no account.
    // Tell the user to sign in; do not conflate this with the not-available case.
    return { ...empty, configured: true, signedIn: false, message: 'Sign in to Collie to use Collie AI.' }
  }
  try {
    const response = await fetch(baseUrl() + '/v1/me/entitlements', {
      headers: { Authorization: 'Bearer ' + token },
      redirect: 'error', signal: AbortSignal.timeout(10_000)
    })
    if (!response.ok) {
      return { ...empty, configured: true, signedIn: true, message: 'Collie AI is unavailable. Your own providers still work.' }
    }
    const data = await response.json() as Record<string, unknown>
    if (typeof data.remaining !== 'number' || typeof data.limit !== 'number' ||
        !Number.isFinite(data.remaining) || !Number.isFinite(data.limit)) throw new Error('Invalid allowance')
    return { configured: true, available: data.available === true, signedIn: true,
      remaining: Math.max(0, data.remaining), limit: Math.max(0, data.limit),
      resetsAt: typeof data.resetsAt === 'string' ? data.resetsAt : null,
      message: data.available === true ? 'AI included with your Collie account.' : 'Your included allowance is unavailable.' }
  } catch {
    // Transport/parse failure — the user IS signed in; don't send them through
    // sign-in again for a transient outage.
    return { ...empty, configured: true, signedIn: true, message: "We couldn't check your included allowance right now. Try again in a moment." }
  }
}

function error(res: ServerResponse, status: number, code: string, message: string): void {
  if (res.headersSent) { res.destroy(); return }
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(JSON.stringify({ error: { code, message } }))
}

/** Called only AFTER the keychain bridge authenticates its per-boot bearer token. */
export async function forwardManagedInference(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 120_000)
  res.on('close', () => controller.abort())
  try {
    const chunks: Buffer[] = []
    let size = 0
    for await (const chunk of req) {
      const bytes = Buffer.from(chunk)
      size += bytes.length
      if (size > MAX_BODY) { error(res, 413, 'request_too_large', 'This request is too large.'); return }
      chunks.push(bytes)
    }
    let token: string
    try { token = await accessToken() } catch {
      error(res, 401, 'auth_required', 'Sign in to Collie to use Collie AI.'); return
    }
    const response = await fetch(baseUrl() + '/v1/chat/completions', {
      method: 'POST', headers: { Authorization: 'Bearer ' + token,
        'Content-Type': 'application/json', 'Idempotency-Key': randomUUID() },
      body: Buffer.concat(chunks).toString('utf8'), redirect: 'error', signal: controller.signal
    })
    res.writeHead(response.status, {
      'Content-Type': response.headers.get('content-type') ?? 'application/json',
      'Cache-Control': 'no-store'
    })
    if (!response.body) { res.end(); return }
    const reader = response.body.getReader()
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (!res.write(Buffer.from(value))) await once(res, 'drain', { signal: controller.signal })
      }
      res.end()
    } finally { await reader.cancel().catch(() => undefined) }
  } catch {
    error(res, 503, 'capacity_unavailable', 'Collie AI is unavailable. Please try again later.')
  } finally { clearTimeout(timeout) }
}

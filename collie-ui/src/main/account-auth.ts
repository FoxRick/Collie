/**
 * Collie account sign-in (account-system-spec.md §6): PKCE + localhost
 * callback, Claude-Desktop style — the user signs in in their system browser
 * and the app picks the session back up from a local redirect.
 *
 * Flow: Settings → "Sign in" → the app generates a PKCE verifier/challenge,
 * starts a single-use HTTP listener on 127.0.0.1:8123, opens the Supabase
 * authorize URL in the system browser, and waits. The browser redirects to
 * `http://127.0.0.1:8123/callback?code=...`, the app exchanges the code for
 * tokens with Supabase's PKCE token endpoint, and the session is stored
 * encrypted at rest with Electron safeStorage (same pattern as secrets.ts).
 *
 * The localhost port is FIXED (8123) because Supabase validates `redirect_to`
 * against a configured allowlist — a wildcard port cannot be guaranteed. If
 * the port is taken, sign-in fails with a clear message instead of silently
 * moving ports.
 */
import { app, safeStorage, shell } from 'electron'
import { createHash, randomBytes } from 'crypto'
import { createServer, type Server } from 'http'
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'fs'
import { dirname, join } from 'path'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from '../shared/account-config'
import { secureStorageAvailable } from './secrets'

/** Fixed callback port — must match the Supabase redirect allowlist. */
export const CALLBACK_PORT = 8123
export const CALLBACK_HOST = '127.0.0.1'
export const SIGN_IN_TIMEOUT_MS = 5 * 60 * 1000

const CALLBACK_SUCCESS_HTML =
  '<!doctype html><html><head><meta charset="utf-8"><title>Collie sign-in</title></head>' +
  '<body style="font-family:system-ui,sans-serif;padding:48px;max-width:520px">' +
  '<h1>You are signed in 🐕</h1>' +
  '<p>You can close this tab and go back to Collie.</p></body></html>'
const CALLBACK_CANCELLED_HTML =
  '<!doctype html><html><head><meta charset="utf-8"><title>Collie sign-in</title></head>' +
  '<body style="font-family:system-ui,sans-serif;padding:48px;max-width:520px">' +
  '<h1>Sign-in cancelled</h1>' +
  '<p>No problem — you can close this tab and go back to Collie.</p></body></html>'

export interface AccountState {
  signedIn: boolean
  email: string | null
  /** Epoch milliseconds, or null when unknown. */
  expiresAt: number | null
  /**
   * Early-access status from the `early_access` table (spec §4): 'granted'
   * when the row says so, 'waiting' while on the list, 'unknown' when the
   * lookup can't run (offline, unconfigured build, not signed in).
   */
  access: 'granted' | 'waiting' | 'unknown'
}

interface StoredSession {
  access_token: string
  refresh_token: string
  /** Epoch milliseconds. */
  expires_at: number
  email: string
}

/** All fields stored as safeStorage-encrypted base64 blobs (secrets.ts style). */
interface AuthFile {
  access_token: string
  refresh_token: string
  expires_at: string
  email: string
}

interface TokenResponse {
  access_token?: unknown
  refresh_token?: unknown
  expires_in?: unknown
  user?: { email?: unknown }
}

/* ------------------------------------------------------------------ *
 * PKCE
 * ------------------------------------------------------------------ */

function base64UrlEncode(buffer: Buffer): string {
  return buffer.toString('base64url')
}

/** PKCE pair per RFC 7636: 32 random bytes → S256 challenge. */
export function createPkcePair(): { verifier: string; challenge: string } {
  const verifier = base64UrlEncode(randomBytes(32))
  const challenge = base64UrlEncode(createHash('sha256').update(verifier).digest())
  return { verifier, challenge }
}

/**
 * Decode a JWT payload for display purposes only (email / exp). No signature
 * verification — Supabase tokens are trusted because they came over HTTPS
 * from the real authorize endpoint; this is UI convenience, not auth.
 */
export function decodeJwtPayload(
  token: string
): { email?: unknown; exp?: unknown } | null {
  const parts = token.split('.')
  if (parts.length !== 3) return null
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')) as Record<
      string,
      unknown
    >
    return payload
  } catch {
    return null
  }
}

/* ------------------------------------------------------------------ *
 * Encrypted token store (mirrors the secrets.ts file style)
 * ------------------------------------------------------------------ */

function authFilePath(): string {
  return join(app.getPath('userData'), 'auth.json')
}

function readAuthFile(): AuthFile | null {
  try {
    const path = authFilePath()
    if (!existsSync(path)) return null
    const raw = JSON.parse(readFileSync(path, 'utf-8')) as Record<string, unknown>
    if (typeof raw.access_token !== 'string' || typeof raw.refresh_token !== 'string') {
      return null
    }
    return {
      access_token: raw.access_token,
      refresh_token: raw.refresh_token,
      expires_at: typeof raw.expires_at === 'string' ? raw.expires_at : '',
      email: typeof raw.email === 'string' ? raw.email : ''
    }
  } catch {
    return null
  }
}

/** Staged write: write a temp file, then rename over the target (atomic). */
function writeAuthFile(file: AuthFile): void {
  const path = authFilePath()
  mkdirSync(dirname(path), { recursive: true })
  const tmpPath = `${path}.tmp`
  // Defense-in-depth: fields are safeStorage-encrypted, but keep the session
  // file owner-only anyway (0600). Windows ignores POSIX modes; DPAPI already
  // scopes decryption to the user account there.
  writeFileSync(tmpPath, JSON.stringify(file, null, 2), { encoding: 'utf-8', mode: 0o600 })
  renameSync(tmpPath, path)
  try {
    chmodSync(path, 0o600)
  } catch {
    // best effort: mode enforcement is defense-in-depth, not correctness
  }
}

/** Persist a session, encrypting every field with safeStorage. */
export function saveAccountSession(session: StoredSession): boolean {
  if (!session.access_token || !secureStorageAvailable()) return false
  const encrypt = (value: string): string =>
    safeStorage.encryptString(value).toString('base64')
  const file: AuthFile = {
    access_token: encrypt(session.access_token),
    refresh_token: encrypt(session.refresh_token),
    expires_at: encrypt(String(session.expires_at)),
    email: encrypt(session.email)
  }
  try {
    writeAuthFile(file)
    return true
  } catch {
    return false
  }
}

/** Read the stored session back, decrypting defensively. */
export function getStoredSession(): StoredSession | null {
  if (!secureStorageAvailable()) return null
  const file = readAuthFile()
  if (!file) return null
  try {
    const decrypt = (blob: string): string =>
      safeStorage.decryptString(Buffer.from(blob, 'base64'))
    return {
      access_token: decrypt(file.access_token),
      refresh_token: decrypt(file.refresh_token),
      expires_at: Number.parseInt(decrypt(file.expires_at), 10) || 0,
      email: decrypt(file.email)
    }
  } catch {
    // Unreadable/corrupt blob → treat as signed out rather than crash.
    return null
  }
}

/** Remove the stored session (no-op when nothing is stored). */
export function clearAccountSession(): boolean {
  try {
    rmSync(authFilePath(), { force: true })
    return true
  } catch {
    return false
  }
}

/** Derive the user-facing account state from a stored session. */
function accountStateFromSession(
  session: StoredSession,
  access: AccountState['access'] = 'unknown'
): AccountState {
  const payload = session.access_token ? decodeJwtPayload(session.access_token) : null
  const email =
    typeof payload?.email === 'string' && payload.email ? payload.email : session.email
  const jwtExp =
    typeof payload?.exp === 'number' && Number.isFinite(payload.exp)
      ? payload.exp * 1000
      : null
  const expiresAt = session.expires_at > 0 ? session.expires_at : jwtExp
  if (expiresAt !== null && expiresAt <= Date.now()) {
    return { signedIn: false, email: email || null, expiresAt, access }
  }
  return { signedIn: true, email: email || null, expiresAt, access }
}

/**
 * Look up the signed-in user's early-access status (spec §4: `early_access`
 * row keyed by auth email; RLS limits SELECT to the owner's own row).
 * Returns 'unknown' on anything unexpected — the UI treats that as neutral.
 */
export async function fetchAccessStatus(accessToken: string): Promise<AccountState['access']> {
  const baseUrl = SUPABASE_URL.replace(/\/+$/, '')
  if (!baseUrl || !SUPABASE_ANON_KEY || !accessToken) return 'unknown'
  const payload = decodeJwtPayload(accessToken)
  const email = typeof payload?.email === 'string' ? payload.email : ''
  if (!email) return 'unknown'
  try {
    const url =
      `${baseUrl}/rest/v1/early_access?select=status&email=eq.${encodeURIComponent(email)}`
    const response = await fetch(url, {
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken}`
      },
      signal: AbortSignal.timeout(8_000)
    })
    if (!response.ok) return 'unknown'
    const rows = (await response.json().catch(() => null)) as
      | { status?: unknown }[]
      | null
    const status = rows?.[0]?.status
    return status === 'granted' || status === 'waiting' ? status : 'unknown'
  } catch {
    return 'unknown'
  }
}

/**
 * One silent refresh attempt for an expired session. Supabase access tokens
 * live ~1h; without this the user would be signed out every hour while the
 * refresh token sat unused. Returns the refreshed session (already persisted)
 * or null when there is nothing to refresh or Supabase rejects the token.
 */
async function refreshExpiredSession(session: StoredSession): Promise<StoredSession | null> {
  const baseUrl = SUPABASE_URL.replace(/\/+$/, '')
  const anonKey = SUPABASE_ANON_KEY
  if (!baseUrl || !anonKey || !session.refresh_token) return null
  try {
    const response = await fetch(`${baseUrl}/auth/v1/token?grant_type=refresh_token`, {
      method: 'POST',
      headers: {
        apikey: anonKey,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ refresh_token: session.refresh_token }),
      signal: AbortSignal.timeout(10_000)
    })
    if (!response.ok) return null
    const data = (await response.json().catch(() => null)) as TokenResponse | null
    if (!data || typeof data.access_token !== 'string' || !data.access_token) return null
    const refreshed: StoredSession = {
      access_token: data.access_token,
      refresh_token:
        typeof data.refresh_token === 'string' && data.refresh_token
          ? data.refresh_token
          : session.refresh_token,
      expires_at:
        typeof data.expires_in === 'number' && Number.isFinite(data.expires_in)
          ? Date.now() + data.expires_in * 1000
          : 0,
      email: typeof data.user?.email === 'string' ? data.user.email : session.email
    }
    return saveAccountSession(refreshed) ? refreshed : null
  } catch {
    return null
  }
}

/**
 * Current account state for the Settings UI. The JWT payload (email/exp) is
 * decoded client-side for display only; an expired session gets one silent
 * refresh attempt before it reads as signed out. Early-access status is a
 * best-effort lookup — 'unknown' must never block showing signed-in state.
 */
export async function getAccountState(): Promise<AccountState> {
  const session = getStoredSession()
  if (!session) return { signedIn: false, email: null, expiresAt: null, access: 'unknown' }
  const state = accountStateFromSession(session)
  if (!state.signedIn) {
    const refreshed = await refreshExpiredSession(session)
    if (refreshed) {
      const fresh = accountStateFromSession(refreshed)
      return { ...fresh, access: await fetchAccessStatus(refreshed.access_token) }
    }
    return state
  }
  return { ...state, access: await fetchAccessStatus(session.access_token) }
}

/* ------------------------------------------------------------------ *
 * Sign-in / sign-out
 * ------------------------------------------------------------------ */

/** Supabase's authorize endpoint with `redirect_to` (not `redirect_uri`). */
function buildAuthorizeUrl(
  baseUrl: string,
  anonKey: string,
  challenge: string,
  callbackUrl: string
): string {
  const url = new URL(`${baseUrl}/auth/v1/authorize`)
  url.searchParams.set('aud', 'authenticated')
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('redirect_to', callbackUrl)
  url.searchParams.set('code_challenge', challenge)
  url.searchParams.set('code_challenge_method', 'S256')
  url.searchParams.set('client_id', anonKey)
  return url.toString()
}

/**
 * Single-use callback wait: resolve with the `code` from the browser's
 * redirect, close the server, or reject after `timeoutMs`.
 */
function waitForCallbackCode(server: Server, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      server.close()
      server.closeAllConnections()
      reject(new Error("Sign-in didn't finish. Please try again."))
    }, timeoutMs)
    server.on('request', (req, res) => {
      if (req.method !== 'GET') {
        res.writeHead(405).end('Method not allowed')
        return
      }
      let url: URL
      try {
        url = new URL(req.url ?? '/', `http://${CALLBACK_HOST}`)
      } catch {
        res.writeHead(400).end('Bad request')
        return
      }
      if (url.pathname !== '/callback') {
        res.writeHead(404).end('Not found')
        return
      }
      // Supabase redirects with `?error=...` when the user cancels in the
      // browser — surface that as a friendly cancellation instead of a
      // "Bad request" page and a 5-minute wait.
      const oauthError = url.searchParams.get('error')
      if (oauthError) {
        clearTimeout(timer)
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
        res.end(CALLBACK_CANCELLED_HTML)
        server.close()
        server.closeAllConnections()
        reject(new Error('Sign-in was cancelled.'))
        return
      }
      const code = url.searchParams.get('code')
      if (!code) {
        res.writeHead(400).end('Missing code')
        return
      }
      clearTimeout(timer)
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
      res.end(CALLBACK_SUCCESS_HTML)
      server.close()
      server.closeAllConnections()
      resolve(code)
    })
    server.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
  })
}

function closeServer(server: Server): void {
  try {
    server.close()
    server.closeAllConnections()
  } catch {
    // already closed
  }
}

/**
 * Start the browser sign-in flow and return the resulting account state.
 * Throws a user-facing error when the app is not configured or the fixed
 * callback port is already in use.
 */
export async function startAccountSignIn(
  options: { timeoutMs?: number } = {}
): Promise<AccountState> {
  const baseUrl = SUPABASE_URL.replace(/\/+$/, '')
  const anonKey = SUPABASE_ANON_KEY
  if (!baseUrl || !anonKey) {
    throw new Error('Collie account sign-in is not configured for this build yet.')
  }
  const timeoutMs = options.timeoutMs ?? SIGN_IN_TIMEOUT_MS
  const { verifier, challenge } = createPkcePair()

  const server = createServer()
  const port = await new Promise<number>((resolve, reject) => {
    server.once('error', (error: NodeJS.ErrnoException) => {
      if (error.code === 'EADDRINUSE') {
        reject(
          new Error(
            `Another app is using the port Collie needs for sign-in ` +
              `(${CALLBACK_HOST}:${CALLBACK_PORT}). Close that app and try again.`
          )
        )
        return
      }
      reject(error)
    })
    server.listen(CALLBACK_PORT, CALLBACK_HOST, () => {
      resolve(CALLBACK_PORT)
    })
  })

  const callbackUrl = `http://${CALLBACK_HOST}:${port}/callback`
  try {
    await shell.openExternal(buildAuthorizeUrl(baseUrl, anonKey, challenge, callbackUrl))
  } catch (error) {
    closeServer(server)
    throw error
  }

  const code = await waitForCallbackCode(server, timeoutMs)

  const response = await fetch(`${baseUrl}/auth/v1/token?grant_type=pkce`, {
    method: 'POST',
    headers: {
      apikey: anonKey,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
    signal: AbortSignal.timeout(10_000)
  })
  if (!response.ok) {
    throw new Error(
      `Sign-in could not be completed (${response.status}). Please try again.`
    )
  }
  const data = (await response.json().catch(() => null)) as TokenResponse | null
  if (!data || typeof data.access_token !== 'string' || !data.access_token) {
    throw new Error('Sign-in came back without a session token. Please try again.')
  }
  const expiresAt =
    typeof data.expires_in === 'number' && Number.isFinite(data.expires_in)
      ? Date.now() + data.expires_in * 1000
      : 0
  const saved = saveAccountSession({
    access_token: data.access_token,
    refresh_token: typeof data.refresh_token === 'string' ? data.refresh_token : '',
    expires_at: expiresAt,
    email: typeof data.user?.email === 'string' ? data.user.email : ''
  })
  if (!saved) {
    throw new Error(
      "Your sign-in is secure — Collie never stores it as plain text. This " +
        "computer couldn't save it, so you'll sign in again next time you open " +
        "Collie. To make saving work next time: unlock your Mac's Keychain, " +
        "sign back in to Windows, or restart your computer and sign in " +
        "normally on Linux."
    )
  }
  return await getAccountState()
}

/** Sign out: best-effort Supabase session revocation, then clear locally. */
export async function signOut(): Promise<AccountState> {
  const session = getStoredSession()
  const baseUrl = SUPABASE_URL.replace(/\/+$/, '')
  if (session?.access_token && baseUrl && SUPABASE_ANON_KEY) {
    try {
      await fetch(`${baseUrl}/auth/v1/logout`, {
        method: 'POST',
        headers: {
          apikey: SUPABASE_ANON_KEY,
          Authorization: `Bearer ${session.access_token}`,
          'Content-Type': 'application/json'
        }
      })
    } catch {
      // Best-effort: local sign-out proceeds even if the server call fails.
    }
  }
  clearAccountSession()
  return await getAccountState()
}

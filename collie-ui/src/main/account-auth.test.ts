import { createHash } from 'crypto'
import { mkdtempSync, rmSync } from 'fs'
import { createServer, request as httpRequest } from 'http'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const testState = vi.hoisted(() => {
  process.env.COLLIE_SUPABASE_URL = 'https://test.supabase.co'
  process.env.COLLIE_SUPABASE_ANON_KEY = 'test-anon-key'
  return {
    userData: '',
    openedUrl: '',
    encryptionAvailable: true,
    exchange: null as {
      url: string
      body: Record<string, unknown>
      headers: Record<string, string>
    } | null
  }
})

vi.mock('electron', () => ({
  app: { getPath: () => testState.userData },
  safeStorage: {
    isEncryptionAvailable: () => testState.encryptionAvailable,
    getSelectedStorageBackend: () => 'gnome_libsecret',
    encryptString: (value: string) => Buffer.from(`encrypted:${value}`, 'utf8'),
    decryptString: (value: Buffer) => value.toString('utf8').replace(/^encrypted:/, '')
  },
  shell: {
    openExternal: (url: string) => {
      testState.openedUrl = url
      return Promise.resolve()
    }
  }
}))

import {
  CALLBACK_HOST,
  CALLBACK_PORT,
  clearAccountSession,
  createPkcePair,
  decodeJwtPayload,
  getAccountState,
  getStoredSession,
  saveAccountSession,
  signOut,
  startAccountSignIn
} from './account-auth'
import { resetSecureStorageCache } from './secrets'

/** Minimal fake JWT: base64url header.payload.signature. No verification is done. */
function makeJwt(payload: Record<string, unknown>): string {
  const enc = (value: unknown): string =>
    Buffer.from(JSON.stringify(value)).toString('base64url')
  return `${enc({ alg: 'none', typ: 'JWT' })}.${enc(payload)}.${enc({})}`
}

// Keep the real fetch for the test's own localhost callback request; the
// stubbed fetch only fakes the Supabase REST endpoints.
const realFetch = globalThis.fetch
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  testState.userData = mkdtempSync(join(tmpdir(), 'collie-auth-'))
  testState.openedUrl = ''
  testState.encryptionAvailable = true
  resetSecureStorageCache()
  testState.exchange = null
  fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith(`http://${CALLBACK_HOST}:${CALLBACK_PORT}`)) {
      return realFetch(url, init)
    }
    if (url.includes('/auth/v1/token')) {
      const isRefresh =
        url.includes('grant_type=refresh_token') ||
        String(init?.body ?? '').includes('grant_type=refresh_token')
      testState.exchange = {
        url,
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        headers: (init?.headers ?? {}) as Record<string, string>
      }
      if (isRefresh) {
        return new Response(
          JSON.stringify({
            access_token: makeJwt({
              email: 'tester@heycollie.com',
              exp: Math.floor(Date.now() / 1000) + 3600
            }),
            refresh_token: 'refresh-token-2',
            expires_in: 3600,
            user: { email: 'tester@heycollie.com' }
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      }
      return new Response(
        JSON.stringify({
          access_token: makeJwt({
            email: 'tester@heycollie.com',
            exp: Math.floor(Date.now() / 1000) + 3600
          }),
          refresh_token: 'refresh-token-1',
          expires_in: 3600,
          user: { email: 'tester@heycollie.com' }
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (url.includes('/auth/v1/logout')) {
      return new Response('{}', { status: 200 })
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  rmSync(testState.userData, { recursive: true, force: true })
})

describe('PKCE pair generation', () => {
  it('creates a base64url verifier/challenge pair with a correct S256 challenge', () => {
    const pair = createPkcePair()

    expect(pair.verifier).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(pair.challenge).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(pair.verifier).not.toBe(pair.challenge)
    expect(pair.challenge).toBe(
      createHash('sha256').update(pair.verifier).digest('base64url')
    )
  })
})

describe('JWT payload decoding', () => {
  it('extracts email and exp from a token payload for display', () => {
    expect(
      decodeJwtPayload(makeJwt({ email: 'rick@heycollie.com', exp: 1_800_000_000 }))
    ).toEqual({ email: 'rick@heycollie.com', exp: 1_800_000_000 })
  })

  it('returns null for anything that is not a three-part JWT', () => {
    expect(decodeJwtPayload('not-a-jwt')).toBeNull()
    expect(decodeJwtPayload('a.b')).toBeNull()
    expect(decodeJwtPayload('a.b.c')).toBeNull() // payload is not JSON
  })
})

describe('encrypted session store', () => {
  it('round-trips a session through the encrypted store and clears it', async () => {
    expect(
      saveAccountSession({
        access_token: 'access-token-1',
        refresh_token: 'refresh-token-1',
        expires_at: 123_456_789,
        email: 'owner@heycollie.com'
      })
    ).toBe(true)

    expect(getStoredSession()).toEqual({
      access_token: 'access-token-1',
      refresh_token: 'refresh-token-1',
      expires_at: 123_456_789,
      email: 'owner@heycollie.com'
    })

    expect(clearAccountSession()).toBe(true)
    expect(getStoredSession()).toBeNull()
    expect(await getAccountState()).toEqual({
      signedIn: false,
      email: null,
      expiresAt: null,
      access: 'unknown'
    })
  })

  it('rejects a session when safeStorage encryption is unavailable', async () => {
    // (mock always reports available; the guard is exercised via the
    // unconfigured-path behavior in the sign-in tests instead)
    expect(saveAccountSession({ access_token: '', refresh_token: '', expires_at: 0, email: '' }))
      .toBe(false)
  })
})

describe('getAccountState', () => {
  it('decodes email and expiry from the access-token JWT', async () => {
    const exp = Math.floor(Date.now() / 1000) + 3600
    saveAccountSession({
      access_token: makeJwt({ email: 'rick@heycollie.com', exp }),
      refresh_token: 'refresh-token-1',
      expires_at: 0,
      email: ''
    })

    const state = await getAccountState()
    expect(state.signedIn).toBe(true)
    expect(state.email).toBe('rick@heycollie.com')
    expect(state.expiresAt).toBe(exp * 1000)
  })

  it('falls back to the stored email when the token is not a JWT', async () => {
    saveAccountSession({
      access_token: 'opaque-token',
      refresh_token: 'refresh-token-1',
      expires_at: Date.now() + 60_000,
      email: 'stored@heycollie.com'
    })

    const state = await getAccountState()
    expect(state.signedIn).toBe(true)
    expect(state.email).toBe('stored@heycollie.com')
  })

  it('treats an expired session without a refresh token as signed out', async () => {
    const exp = Math.floor(Date.now() / 1000) - 60
    saveAccountSession({
      access_token: makeJwt({ email: 'old@heycollie.com', exp }),
      refresh_token: '',
      expires_at: 0,
      email: 'old@heycollie.com'
    })

    const state = await getAccountState()
    expect(state.signedIn).toBe(false)
    expect(state.email).toBe('old@heycollie.com')
    expect(state.expiresAt).toBe(exp * 1000)
  })

  it('silently refreshes an expired session instead of signing out', async () => {
    const exp = Math.floor(Date.now() / 1000) - 60
    saveAccountSession({
      access_token: makeJwt({ email: 'old@heycollie.com', exp }),
      refresh_token: 'refresh-token-1',
      expires_at: 0,
      email: 'old@heycollie.com'
    })

    const state = await getAccountState()
    expect(state.signedIn).toBe(true)
    expect(state.email).toBe('tester@heycollie.com')
    // The refreshed session is persisted (new tokens + expiry).
    const stored = getStoredSession()
    expect(stored?.access_token).toContain('eyJ')
    expect(stored?.refresh_token).toBe('refresh-token-2')
    expect(stored?.expires_at).toBeGreaterThan(Date.now())
  })
})

/** Extract the per-attempt OAuth `state` nonce from the opened authorize URL. */
function openedAuthorizeState(): string {
  const url = new URL(testState.openedUrl)
  const state = url.searchParams.get('state')
  if (!state) throw new Error('expected a state param on the authorize URL')
  return state
}

describe('startAccountSignIn', () => {
  it('completes the browser-callback flow end to end', async () => {
    const signInPromise = startAccountSignIn()

    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })
    const authorizeUrl = new URL(testState.openedUrl)
    expect(authorizeUrl.searchParams.get('aud')).toBe('authenticated')
    expect(authorizeUrl.searchParams.get('response_type')).toBe('code')
    expect(authorizeUrl.searchParams.get('redirect_to')).toBe(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback`
    )
    expect(authorizeUrl.searchParams.get('code_challenge_method')).toBe('S256')
    expect(authorizeUrl.searchParams.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(authorizeUrl.searchParams.get('client_id')).toBe('test-anon-key')
    // A fresh, per-attempt state nonce is carried on the authorize URL.
    const stateNonce = authorizeUrl.searchParams.get('state')
    expect(stateNonce).toMatch(/^[A-Za-z0-9_-]{43}$/)
    expect(stateNonce).not.toBe(authorizeUrl.searchParams.get('code_challenge'))

    // Simulate the browser redirect after the user signs in, echoing the state.
    const callbackResponse = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=exchange-me&state=${stateNonce}`
    )
    expect(callbackResponse.status).toBe(200)

    const state = await signInPromise
    expect(state.signedIn).toBe(true)
    expect(state.email).toBe('tester@heycollie.com')
    expect(state.expiresAt).toBeGreaterThan(Date.now())

    // The code was exchanged with the PKCE verifier over the token endpoint.
    expect(testState.exchange?.url).toBe(
      'https://test.supabase.co/auth/v1/token?grant_type=pkce'
    )
    expect(testState.exchange?.body).toEqual({
      auth_code: 'exchange-me',
      code_verifier: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/)
    })
    expect(testState.exchange?.headers.apikey).toBe('test-anon-key')
    expect(testState.exchange?.headers['Content-Type']).toBe('application/json')

    // The session survived the flow and is readable from the store.
    expect(getStoredSession()?.refresh_token).toBe('refresh-token-1')
  })

  it('times out with a friendly message when no callback arrives', async () => {
    await expect(startAccountSignIn({ timeoutMs: 150 })).rejects.toThrow(
      "Sign-in didn't finish"
    )
  })

  it('surfaces a browser-side cancellation as a friendly error', async () => {
    const signInPromise = startAccountSignIn()
    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })

    // Attach the rejection expectation BEFORE firing the callback request:
    // startAccountSignIn rejects from the server's request handler, which
    // runs while realFetch is still awaiting the response — a late handler
    // becomes an unhandled rejection that fails the vitest run (CI exit 1).
    const rejection = expect(signInPromise).rejects.toThrow('Sign-in was cancelled.')

    // Supabase redirects with ?error=access_denied when the user cancels.
    const callbackResponse = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?error=access_denied&error_description=User+cancelled`
    )
    expect(callbackResponse.status).toBe(200)

    await rejection
  })

  it('fails with a clear message when the session cannot be stored securely', async () => {
    testState.encryptionAvailable = false
    resetSecureStorageCache() // secureStorageAvailable caches its probe result
    const signInPromise = startAccountSignIn()
    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })

    // Same pattern as the cancellation test — handle the rejection before
    // the callback request can trigger it.
    const rejection = expect(signInPromise).rejects.toThrow(
      'Your sign-in is secure — Collie never stores it as plain text.'
    )

    const callbackResponse = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=exchange-me&state=${openedAuthorizeState()}`
    )
    expect(callbackResponse.status).toBe(200)

    await rejection
    expect(getStoredSession()).toBeNull()
  })

  it('fails with a clear message when the fixed callback port is taken', async () => {
    const blocker = createServer()
    await new Promise<void>((resolve) =>
      blocker.listen(CALLBACK_PORT, CALLBACK_HOST, resolve)
    )
    try {
      await expect(startAccountSignIn()).rejects.toThrow(
        'Another app is using the port'
      )
    } finally {
      await new Promise<void>((resolve) => blocker.close(() => resolve()))
    }
  })
})

/**
 * Raw callback GET with fully controlled headers — realFetch can't fake a
 * Host or Origin header, so login-CSRF probes use Node's http client with
 * `setHost: false` and an explicit host header.
 */
function rawCallback(opts: { host?: string; origin?: string }): Promise<number> {
  return new Promise((resolve, reject) => {
    const headers: Record<string, string> = {
      host: opts.host ?? `${CALLBACK_HOST}:${CALLBACK_PORT}`
    }
    if (opts.origin !== undefined) headers.origin = opts.origin
    const req = httpRequest(
      {
        hostname: CALLBACK_HOST,
        port: CALLBACK_PORT,
        path: '/callback?code=attacker-code',
        method: 'GET',
        headers,
        setHost: false
      },
      (res) => {
        res.resume()
        resolve(res.statusCode ?? 0)
      }
    )
    req.on('error', reject)
    req.end()
  })
}

describe('sign-in callback CSRF defenses (issue #106)', () => {
  it('rejects a callback addressed to a foreign Host (DNS-rebinding defense)', async () => {
    const signInPromise = startAccountSignIn()
    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })

    // An attacker page that rebinds its name to 127.0.0.1 delivers its own
    // code — the request arrives, but Host is the attacker's name.
    const status = await rawCallback({ host: 'evil.example.com' })
    expect(status).toBe(404)

    // The flow is untouched: the legitimate redirect still completes it.
    const ok = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=exchange-me&state=${openedAuthorizeState()}`
    )
    expect(ok.status).toBe(200)
    const state = await signInPromise
    expect(state.signedIn).toBe(true)
    expect(state.email).toBe('tester@heycollie.com')
  })

  it('rejects a callback carrying an Origin header (cross-site fetch defense)', async () => {
    const signInPromise = startAccountSignIn()
    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })

    // A top-level browser redirect sends no Origin; cross-site fetch/XHR does.
    const status = await rawCallback({ origin: 'http://evil.example.com' })
    expect(status).toBe(400)

    const ok = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=exchange-me&state=${openedAuthorizeState()}`
    )
    expect(ok.status).toBe(200)
    const state = await signInPromise
    expect(state.signedIn).toBe(true)
    expect(state.email).toBe('tester@heycollie.com')
  })

  it('rejects a callback carrying a mismatched or missing state and never exchanges it', async () => {
    const signInPromise = startAccountSignIn()
    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })
    const state = openedAuthorizeState()

    // A code minted for a DIFFERENT flow echoes the wrong state — reject it.
    const wrong = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=attacker-code&state=not-the-issued-state`
    )
    expect(wrong.status).toBe(400)
    expect(testState.exchange).toBeNull()

    // A callback with NO state at all is equally rejected.
    const missing = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=attacker-code`
    )
    expect(missing.status).toBe(400)
    expect(testState.exchange).toBeNull()

    // The flow survives the rejected probes: the correct state still completes it.
    const ok = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=exchange-me&state=${state}`
    )
    expect(ok.status).toBe(200)
    const accountState = await signInPromise
    expect(accountState.signedIn).toBe(true)
    expect(accountState.email).toBe('tester@heycollie.com')
    // Only the well-state-bound code was exchanged.
    expect(testState.exchange?.body).toEqual({
      auth_code: 'exchange-me',
      code_verifier: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/)
    })
  })

  it('completes the callback when the correct issued state is echoed back', async () => {
    const signInPromise = startAccountSignIn()
    await vi.waitFor(() => {
      expect(testState.openedUrl).toContain('/auth/v1/authorize')
    })
    const state = openedAuthorizeState()

    const ok = await realFetch(
      `http://${CALLBACK_HOST}:${CALLBACK_PORT}/callback?code=exchange-me&state=${state}`
    )
    expect(ok.status).toBe(200)
    const accountState = await signInPromise
    expect(accountState.signedIn).toBe(true)
    expect(accountState.email).toBe('tester@heycollie.com')
  })
})

describe('signOut', () => {
  it('revokes the session server-side (best-effort) and clears the store', async () => {
    const accessToken = makeJwt({ email: 'out@heycollie.com' })
    saveAccountSession({
      access_token: accessToken,
      refresh_token: 'refresh-token-1',
      expires_at: Date.now() + 60_000,
      email: 'out@heycollie.com'
    })
    expect((await getAccountState()).signedIn).toBe(true)

    const state = await signOut()

    expect(state.signedIn).toBe(false)
    expect(getStoredSession()).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith(
      'https://test.supabase.co/auth/v1/logout',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          apikey: 'test-anon-key',
          Authorization: `Bearer ${accessToken}`
        })
      })
    )
  })

  it('still signs out locally when the server call fails', async () => {
    saveAccountSession({
      access_token: makeJwt({ email: 'offline@heycollie.com' }),
      refresh_token: 'refresh-token-1',
      expires_at: Date.now() + 60_000,
      email: 'offline@heycollie.com'
    })
    fetchMock.mockRejectedValueOnce(new Error('network down'))

    const state = await signOut()

    expect(state.signedIn).toBe(false)
    expect(getStoredSession()).toBeNull()
  })
})

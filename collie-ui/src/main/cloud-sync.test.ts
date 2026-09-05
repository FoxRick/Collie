import { mkdtempSync, readFileSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const testState = vi.hoisted(() => {
  return {
    userData: '',
    coreCalls: [] as Array<{ type: string; payload: Record<string, unknown> }>,
    fetches: [] as Array<{ url: string; init: RequestInit }>,
    events: [] as string[],
    toggleValue: false,
    failCoreType: null as string | null,
    nextFetchResponse: null as Response | null,
    nextFetchPromise: null as Promise<Response> | null,
    cfg: { url: 'https://test.supabase.co', key: 'test-anon-key' }
  }
})

vi.mock('electron', () => ({
  app: {
    getPath: () => testState.userData,
    getVersion: () => '0.1.0-alpha.7.2'
  },
  safeStorage: {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => 'gnome_libsecret',
    encryptString: (value: string) => Buffer.from(`encrypted:${value}`, 'utf8'),
    decryptString: (value: Buffer) => value.toString('utf8').replace(/^encrypted:/, '')
  }
}))

vi.mock('../shared/account-config', () => ({
  get SUPABASE_URL(): string {
    return testState.cfg.url
  },
  get SUPABASE_ANON_KEY(): string {
    return testState.cfg.key
  }
}))

vi.mock('./core-client', () => ({
  commandWithCore: (type: string, payload: Record<string, unknown>) => {
    testState.coreCalls.push({ type, payload })
    if (testState.failCoreType === type) {
      return Promise.reject(new Error(`core ${type} failed`))
    }
    if (type === 'get_settings') {
      return Promise.resolve({
        settings: testState.toggleValue ? { 'account.sync_enabled': true } : {}
      })
    }
    if (type === 'set_setting') {
      testState.toggleValue = payload.value === true
      testState.events.push(`toggle:${String(payload.value)}`)
      return Promise.resolve({})
    }
    if (type === 'get_profile') return Promise.resolve({ profile: { favorite_color: 'blue' } })
    if (type === 'get_people')
      return Promise.resolve({ people: [{ id: 'p1', name: 'Maya', birthday: '05-14' }] })
    if (type === 'get_dates')
      return Promise.resolve({
        dates: [{ date: '05-14', label: "Maya's birthday", recurring: 1 }]
      })
    if (type === 'read_file')
      return Promise.resolve({
        content: payload.path === 'AGENTS.md' ? '# About Me' : '# Personality'
      })
    if (type === 'set_profile_memory') return Promise.resolve({ profile: {} })
    if (type === 'update_person_memory') return Promise.resolve({ person: {} })
    if (type === 'add_person_memory') return Promise.resolve({ person: {} })
    if (type === 'add_date_memory') return Promise.resolve({ date: {} })
    if (type === 'write_file')
      return Promise.resolve({ saved: true, version_id: 'v1', diff_text: '' })
    return Promise.resolve({})
  }
}))

/** Minimal fake JWT with a `sub` claim. */
function makeJwt(sub: string): string {
  const enc = (value: unknown): string =>
    Buffer.from(JSON.stringify(value)).toString('base64url')
  return `${enc({ alg: 'none', typ: 'JWT' })}.${enc({ sub })}.${enc({})}`
}

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason?: unknown) => void
} {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

const globalFetch = global.fetch

import {
  enableSync,
  gatherSnapshot,
  getSyncStatus,
  listSnapshots,
  restoreFromDevice,
  restoreSnapshot,
  uploadSnapshot
} from './cloud-sync'
import { clearAccountSession, saveAccountSession } from './account-auth'
import { resetSecureStorageCache } from './secrets'

function signInAs(sub: string): void {
  saveAccountSession({
    access_token: makeJwt(sub),
    refresh_token: 'r',
    expires_at: Date.now() + 3_600_000,
    email: 'rick@example.com'
  })
}

beforeEach(() => {
  testState.userData = mkdtempSync(join(tmpdir(), 'collie-cloud-sync-'))
  testState.coreCalls = []
  testState.fetches = []
  testState.events = []
  testState.toggleValue = false
  testState.failCoreType = null
  testState.nextFetchResponse = null
  testState.nextFetchPromise = null
  resetSecureStorageCache()
  global.fetch = ((url: string | URL, init: RequestInit = {}): Promise<Response> => {
    const urlString = String(url)
    if (urlString.startsWith('https://test.supabase.co')) {
      testState.fetches.push({ url: urlString, init })
      testState.events.push(`fetch:${String(init.method ?? 'GET')}:started`)
      const response =
        testState.nextFetchPromise ??
        Promise.resolve(
          testState.nextFetchResponse ??
            new Response(JSON.stringify([{ created_at: '2026-08-22T00:00:00Z' }]), {
              status: 200
            })
        )
      return response.then((result) => {
        testState.events.push(`fetch:${String(init.method ?? 'GET')}:resolved`)
        return result
      })
    }
    return globalFetch(url, init)
  }) as typeof fetch
})

afterEach(() => {
  clearAccountSession()
  resetSecureStorageCache()
  rmSync(testState.userData, { recursive: true, force: true })
  global.fetch = globalFetch
})

describe('cloud sync status + toggle', () => {
  it('reports signed-out and disabled without a session', async () => {
    const status = await getSyncStatus()
    expect(status).toEqual({
      configured: true,
      enabled: false,
      signedIn: false,
      email: null
    })
  })

  it('refuses to enable while signed out', async () => {
    await expect(enableSync(true)).rejects.toThrow(/Sign in/)
  })

  it('persists true only after the baseline upload succeeds', async () => {
    signInAs('user-1')
    const uploadGate = deferred<Response>()
    testState.nextFetchPromise = uploadGate.promise

    const transition = enableSync(true)
    await vi.waitFor(() => {
      expect(testState.fetches.some((entry) => entry.init.method === 'POST')).toBe(true)
    })
    expect(testState.coreCalls.some((call) => call.type === 'set_setting')).toBe(false)

    uploadGate.resolve(
      new Response(JSON.stringify([{ created_at: '2026-08-22T00:00:00Z' }]), { status: 200 })
    )
    await transition

    const setCall = testState.coreCalls.find((c) => c.type === 'set_setting')
    expect(setCall?.payload).toEqual({ key: 'account.sync_enabled', value: true })
    expect(testState.coreCalls.some((c) => c.type === 'get_profile')).toBe(true)
    const upload = testState.fetches.find((f) => f.init.method === 'POST')
    expect(upload?.url).toContain('on_conflict=user_id,device_id')
    expect(upload?.init.headers).toMatchObject({
      Prefer: 'resolution=merge-duplicates,return=representation'
    })
    expect(testState.events.indexOf('fetch:POST:resolved')).toBeLessThan(
      testState.events.indexOf('toggle:true')
    )
  })

  it('leaves sync off when the baseline upload fails', async () => {
    signInAs('user-1')
    testState.nextFetchResponse = new Response('unavailable', { status: 503 })

    await expect(enableSync(true)).rejects.toThrow(/503/)

    expect(testState.toggleValue).toBe(false)
    expect(
      testState.coreCalls.some(
        (call) => call.type === 'set_setting' && call.payload.value === true
      )
    ).toBe(false)

    // A rejected enable must not poison the serialized transition queue.
    await expect(enableSync(false)).resolves.toMatchObject({ enabled: false })
  })

  it('leaves sync off when gathering the baseline fails', async () => {
    signInAs('user-1')
    testState.failCoreType = 'get_profile'

    await expect(enableSync(true)).rejects.toThrow(/get_profile/)

    expect(testState.toggleValue).toBe(false)
    expect(testState.fetches).toHaveLength(0)
    expect(testState.coreCalls.some((call) => call.type === 'set_setting')).toBe(false)
  })

  it('lets a newer disable supersede an in-flight enable', async () => {
    signInAs('user-1')
    const upload = deferred<Response>()
    testState.nextFetchPromise = upload.promise

    const enabling = enableSync(true)
    await vi.waitFor(() => {
      expect(testState.fetches.some((entry) => entry.init.method === 'POST')).toBe(true)
    })
    const disabling = enableSync(false)

    upload.resolve(
      new Response(JSON.stringify([{ created_at: '2026-08-22T00:00:00Z' }]), { status: 200 })
    )
    const [enableStatus, disableStatus] = await Promise.all([enabling, disabling])

    expect(enableStatus.enabled).toBe(false)
    expect(disableStatus.enabled).toBe(false)
    expect(
      testState.coreCalls
        .filter((call) => call.type === 'set_setting')
        .map((call) => call.payload.value)
    ).toEqual([false])
  })

  it('refuses to enable when Supabase is unconfigured', async () => {
    const savedUrl = testState.cfg.url
    testState.cfg.url = ''
    try {
      const status = await getSyncStatus()
      expect(status.configured).toBe(false)
      signInAs('user-1')
      await expect(enableSync(true)).rejects.toThrow()
    } finally {
      testState.cfg.url = savedUrl
    }
  })

  it('disabling stops without any network call', async () => {
    signInAs('user-1')
    const status = await enableSync(false)
    expect(status.enabled).toBe(false)
    expect(testState.fetches.filter((f) => f.init.method === 'POST')).toHaveLength(0)
  })
})

describe('snapshot gather/restore', () => {
  it('gathers memory rows and both authored files', async () => {
    const payload = await gatherSnapshot()
    expect(payload.version).toBe(1)
    expect(payload.profile).toEqual({ favorite_color: 'blue' })
    expect(payload.people).toHaveLength(1)
    expect(payload.dates).toHaveLength(1)
    expect(payload.agents_md).toBe('# About Me')
    expect(payload.vision_md).toBe('# Personality')
  })

  it('restores people by name (update existing) and writes only non-empty files', async () => {
    await restoreSnapshot({
      version: 1,
      profile: { favorite_color: 'red' },
      people: [
        { id: 'remote-1', name: 'maya', relationship: 'sister' },
        { name: 'New Friend', gift_ideas: 'tea' }
      ],
      dates: [{ date: '12-24', label: 'Trip', recurring: false }],
      agents_md: '# About Me (from laptop)',
      vision_md: ''
    })
    // Existing person matched case-insensitively → update, not duplicate.
    const update = testState.coreCalls.find((c) => c.type === 'update_person_memory')
    expect(update?.payload).toEqual({ person_id: 'p1', fields: { relationship: 'sister' } })
    expect(testState.coreCalls.some((c) => c.type === 'add_person_memory')).toBe(true)
    // Empty VISION.md content is skipped, never wipes the local file.
    const writes = testState.coreCalls.filter((c) => c.type === 'write_file')
    expect(writes).toHaveLength(1)
    expect(writes[0].payload).toEqual({ path: 'AGENTS.md', content: '# About Me (from laptop)' })
  })

  it('rejects unknown payload versions', async () => {
    await expect(
      restoreSnapshot({ version: 99 } as unknown as Parameters<typeof restoreSnapshot>[0])
    ).rejects.toThrow(/format/)
  })

  it('rolls back to the pre-restore state when the restore fails partway', async () => {
    // The restore fails at the new-person add (the mock rejects every
    // 'add_person_memory'), AFTER it has already half-written the incoming
    // profile color. The wrapper must then replay the pre-restore snapshot.
    testState.failCoreType = 'add_person_memory'

    await expect(
      restoreSnapshot({
        version: 1,
        profile: { favorite_color: 'red' },
        people: [{ name: 'New Friend', gift_ideas: 'tea' }],
        dates: [{ date: '12-24', label: 'Trip', recurring: false }],
        agents_md: '# About Me (from laptop)',
        vision_md: '# Personality (from laptop)'
      })
    ).rejects.toThrow(/add_person_memory/)

    const calls = testState.coreCalls
    // The error still propagates — the caller knows the restore didn't finish.
    // The failing add was only attempted once: the rollback matches the
    // pre-existing 'Maya' by name (update), so the new person is not re-added.
    expect(calls.filter((c) => c.type === 'add_person_memory')).toHaveLength(1)
    // Pre-restore profile color (blue) replays over the half-written 'red'.
    expect(
      calls
        .filter((c) => c.type === 'set_profile_memory')
        .some((c) => c.payload.value === 'blue')
    ).toBe(true)
    // Pre-existing people are updated, not duplicated.
    expect(calls.some((c) => c.type === 'update_person_memory')).toBe(true)
    // Both authored files are rewritten with their pre-restore content.
    const writes = calls.filter((c) => c.type === 'write_file')
    expect(
      writes.some(
        (c) => c.payload.path === 'AGENTS.md' && c.payload.content === '# About Me'
      )
    ).toBe(true)
    expect(
      writes.some(
        (c) => c.payload.path === 'VISION.md' && c.payload.content === '# Personality'
      )
    ).toBe(true)
    // Dates are content-addressed/duplicate-skipped, so the pre-existing
    // date is preserved rather than stacked — no add_date_memory for it.
    expect(calls.filter((c) => c.type === 'add_date_memory')).toHaveLength(0)
  })
})

describe('supabase REST', () => {
  beforeEach(() => {
    signInAs('user-42')
  })

  it('uploads under the user token with the anon key and device identity', async () => {
    const token = makeJwt('user-42')
    await uploadSnapshot()
    const post = testState.fetches.find((f) => f.init.method === 'POST')
    expect(post).toBeDefined()
    const headers = post!.init.headers as Record<string, string>
    expect(headers.Authorization).toBe(`Bearer ${token}`)
    expect(headers.apikey).toBe('test-anon-key')
    const body = JSON.parse(String(post!.init.body)) as Record<string, unknown>
    expect(body.user_id).toBe('user-42')
    expect(typeof body.device_id).toBe('string')
    expect(typeof body.device_name).toBe('string')
    expect(body.payload).toMatchObject({ version: 1 })
  })

  it('keeps one stable device id across uploads', async () => {
    await uploadSnapshot()
    await uploadSnapshot()
    const posts = testState.fetches.filter((f) => f.init.method === 'POST')
    expect(posts).toHaveLength(2)
    const first = JSON.parse(String(posts[0].init.body)) as { device_id: string }
    const second = JSON.parse(String(posts[1].init.body)) as { device_id: string }
    expect(first.device_id).toBe(second.device_id)
  })

  it('lists snapshots flagging this device', async () => {
    await uploadSnapshot() // creates the local device identity file
    const deviceId = (
      JSON.parse(readFileSync(join(testState.userData, 'sync-device.json'), 'utf-8')) as {
        deviceId: string
      }
    ).deviceId
    testState.nextFetchResponse = new Response(
      JSON.stringify([
        { device_id: 'dev-a', device_name: 'Laptop', created_at: '2026-08-21T10:00:00Z' },
        { device_id: deviceId, device_name: 'This', created_at: '2026-08-20T09:00:00Z' }
      ]),
      { status: 200 }
    )
    const snapshots = await listSnapshots()
    expect(snapshots).toHaveLength(2)
    expect(snapshots.filter((s) => s.isThisDevice)).toHaveLength(1)
    // GET carries the user token + anon key (RLS does the scoping).
    const get = testState.fetches[testState.fetches.length - 1]
    const getHeaders = get.init.headers as Record<string, string>
    expect(getHeaders.apikey).toBe('test-anon-key')
    expect(getHeaders.Authorization).toMatch(/^Bearer /)
    expect(get.url).toContain('order=created_at.desc&limit=50')
  })

  it('restores only the picked device snapshot through versioned writes', async () => {
    testState.nextFetchResponse = new Response(
      JSON.stringify([
        {
          payload: {
            version: 1,
            profile: {},
            people: [],
            dates: [],
            agents_md: 'A',
            vision_md: 'V'
          }
        }
      ]),
      { status: 200 }
    )
    await restoreFromDevice('dev-a')
    const url = testState.fetches[testState.fetches.length - 1].url
    expect(url).toContain('device_id=eq.dev-a')
    expect(url).toContain('order=created_at.desc&limit=1')
    expect(testState.coreCalls.filter((c) => c.type === 'write_file')).toHaveLength(2)
  })

  it('surfaces a friendly error when nothing is there', async () => {
    testState.nextFetchResponse = new Response('[]', { status: 200 })
    await expect(restoreFromDevice('dev-none')).rejects.toThrow(/backup/)
  })
})

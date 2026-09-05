import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ directory: '', packaged: true, url: 'https://test.supabase.co' }))
vi.mock('electron', () => ({ app: {
  getPath: () => state.directory,
  getVersion: () => '0.1.0-test',
  get isPackaged() { return state.packaged }
} }))
vi.mock('../shared/account-config', () => ({
  get SUPABASE_URL() { return state.url }, SUPABASE_ANON_KEY: 'public-key'
}))
// Accessing either dependency is a regression: presence must work without them.
vi.mock('./account-auth', () => { throw new Error('Heartbeat depends on sign-in') })
vi.mock('./core-client', () => { throw new Error('Heartbeat depends on core settings') })
import { HEARTBEAT_INTERVAL_MS, sendHeartbeat, startHeartbeat, stopHeartbeat } from './install-heartbeat'

beforeEach(() => {
  state.directory = mkdtempSync(join(tmpdir(), 'collie-install-'))
  state.packaged = true
  state.url = 'https://test.supabase.co'
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
  vi.useFakeTimers()
})
afterEach(() => {
  stopHeartbeat()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  rmSync(state.directory, { recursive: true, force: true })
})

it('registers a fresh signed-out install with only presence data and public credentials', async () => {
  await sendHeartbeat()
  const [url, request] = vi.mocked(fetch).mock.calls[0]
  expect(url).toBe('https://test.supabase.co/rest/v1/rpc/record_install_heartbeat')
  expect(request?.method).toBe('POST')
  expect(request?.headers).toEqual({ apikey: 'public-key', 'Content-Type': 'application/json' })
  expect(JSON.parse(String(request?.body))).toEqual({
    p_install_id: readFileSync(join(state.directory, 'install-id'), 'utf8'),
    p_version: '0.1.0-test', p_platform: process.platform
  })
  expect(request?.signal).toBeInstanceOf(AbortSignal)
})

it('keeps the same identity across repeat sends and scheduler restarts', async () => {
  startHeartbeat()
  await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS)
  stopHeartbeat()
  startHeartbeat()
  await vi.advanceTimersByTimeAsync(0)
  expect(fetch).toHaveBeenCalledTimes(3)
  const ids = vi.mocked(fetch).mock.calls.map(([, r]) => JSON.parse(String(r?.body)).p_install_id)
  expect(new Set(ids).size).toBe(1)
})

it('starts once, retries after failure, and stops on quit', async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error('offline'))
  startHeartbeat()
  startHeartbeat()
  await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS)
  expect(fetch).toHaveBeenCalledTimes(2)
  stopHeartbeat()
  await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS * 2)
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('does not overlap requests', async () => {
  let resolve!: (response: Response) => void
  vi.mocked(fetch).mockReturnValueOnce(new Promise(r => { resolve = r }))
  startHeartbeat()
  await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS * 2)
  expect(fetch).toHaveBeenCalledTimes(1)
  resolve(new Response(null, { status: 204 }))
  await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS)
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('does not count development or unconfigured builds', async () => {
  state.packaged = false
  startHeartbeat()
  state.packaged = true
  state.url = ''
  startHeartbeat()
  await sendHeartbeat()
  expect(fetch).not.toHaveBeenCalled()
})

it('does not send an unpersisted identity when local storage fails', async () => {
  const blocker = join(state.directory, 'file')
  writeFileSync(blocker, '')
  state.directory = blocker
  await expect(sendHeartbeat()).rejects.toThrow()
  expect(fetch).not.toHaveBeenCalled()
  state.directory = join(blocker, '..')
})

it('reports HTTP rejection to the scheduler instead of treating it as success', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 503 }))
  await expect(sendHeartbeat()).rejects.toThrow('503')
})

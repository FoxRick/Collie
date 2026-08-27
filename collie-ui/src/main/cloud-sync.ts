/**
 * Collie account cloud sync — per-device snapshots, strictly opt-in
 * (docs/engineering/architecture/account-cloud-sync.md).
 *
 * Product rules:
 * - Sync is OFF by default; the user flips it in Settings → Account.
 * - Each computer keeps ONE snapshot online under its own device id —
 *   uploading again replaces that device's snapshot (never accumulates).
 * - Restoring is an explicit choice: the user sees their devices' snapshots
 *   and picks one. Restore writes through the same versioned paths as manual
 *   edits (artifact versioning), so it is reviewable and undoable.
 * - Nothing sensitive ever uploads: no API keys (OS keychain only), no
 *   conversations, plans, approvals, connectors, or messenger data.
 *
 * Online shape: one row per (user, device) in `user_sync_snapshots`, RLS-
 * scoped to auth.uid(). Access uses the signed-in user's access token with
 * the public anon key over HTTPS. No service role anywhere.
 */
import { randomUUID } from 'crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { app } from 'electron'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from '../shared/account-config'
import { getStoredSession } from './account-auth'
import { commandWithCore } from './core-client'

/** Settings key (local SQLite via the core) for the opt-in toggle. */
const SYNC_TOGGLE_KEY = 'account.sync_enabled'

/**
 * Maker liveness heartbeat — piggybacks on the opt-in sync toggle. When the
 * user is signed in AND sync is on, the Electron main process PATCHes this
 * device's row in `user_sync_snapshots` with `last_seen` (+ version/platform)
 * every HEARTBEAT_INTERVAL_MS while the app runs.
 *
 * PATCH (not a merge-upsert POST): the row is created once by the baseline
 * `uploadSnapshot` (run before the toggle turns on), so here we update it in
 * place. An UPDATE only overwrites the columns supplied, so `payload` (NOT
 * NULL, must NEVER be re-uploaded) and `created_at` are preserved, and one row
 * per device is guaranteed by the row already existing. A partial merge-upsert
 * POST would 400 (`payload` is NOT NULL with no default) — that's exactly why
 * this is a PATCH.
 * Privacy: reports a device id + version + platform — no content, no
 * conversations, no PII beyond the device name the user already chose. That's
 * why it lives inside the same opt-in toggle as cloud sync.
 */
export const HEARTBEAT_INTERVAL_MS = 4 * 60_000

const SYNCED_FILES = ['AGENTS.md', 'VISION.md'] as const

/** REST timeout — bounded so a hanging network never hangs the UI. */
const HTTP_TIMEOUT_MS = 15_000

/**
 * Toggle transitions may overlap when the renderer changes its mind while a
 * baseline upload is still in flight. Keep writes ordered, and let each
 * transition detect whether a newer request superseded it before it persists
 * state. A rejected transition must not poison the queue for later requests.
 */
let syncToggleGeneration = 0
let syncToggleQueue: Promise<void> = Promise.resolve()

export interface SyncSnapshotSummary {
  deviceId: string
  deviceName: string
  createdAt: string | null
  isThisDevice: boolean
}

export interface SyncStatus {
  configured: boolean
  enabled: boolean
  signedIn: boolean
  email: string | null
}

interface StoredSessionLike {
  access_token: string
  expires_at: number
  email: string
}

/* ------------------------------------------------------------------ *
 * Device identity
 * ------------------------------------------------------------------ */

function deviceFilePath(): string {
  return join(app.getPath('userData'), 'sync-device.json')
}

/**
 * Stable per-install device id + readable name. Created on first use and
 * persisted; the name can be re-derived if the file is deleted.
 */
function loadDeviceIdentity(): { deviceId: string; deviceName: string } {
  const path = deviceFilePath()
  try {
    if (existsSync(path)) {
      const raw = JSON.parse(readFileSync(path, 'utf-8')) as {
        deviceId?: unknown
        deviceName?: unknown
      }
      if (typeof raw.deviceId === 'string' && raw.deviceId) {
        return {
          deviceId: raw.deviceId,
          deviceName:
            typeof raw.deviceName === 'string' && raw.deviceName
              ? raw.deviceName
              : defaultDeviceName()
        }
      }
    }
  } catch {
    // Corrupt file → regenerate below.
  }
  const identity = { deviceId: randomUUID(), deviceName: defaultDeviceName() }
  try {
    mkdirSync(app.getPath('userData'), { recursive: true })
    writeFileSync(path, JSON.stringify(identity, null, 2), 'utf-8')
  } catch {
    // Best effort: an unreadable userData dir fails later at upload time.
  }
  return identity
}

function defaultDeviceName(): string {
  const osLabel =
    process.platform === 'win32'
      ? 'Windows'
      : process.platform === 'darwin'
        ? 'Mac'
        : process.platform === 'linux'
          ? 'Linux'
          : process.platform
  return `${osLabel} computer`
}

/* ------------------------------------------------------------------ *
 * Toggle persistence (local settings table via the core)
 * ------------------------------------------------------------------ */

async function readToggle(): Promise<boolean> {
  try {
    const data = (await commandWithCore('get_settings', {})) as {
      settings?: Record<string, unknown>
    }
    return data?.settings?.[SYNC_TOGGLE_KEY] === true
  } catch {
    return false
  }
}

async function writeToggle(value: boolean): Promise<void> {
  // `account.sync_enabled` is on the core's shell-settable allowlist
  // (_IPC_SETTABLE_SETTINGS) — a plain boolean opt-in, deliberately not
  // guarded like permission/messenger settings.
  await commandWithCore('set_setting', { key: SYNC_TOGGLE_KEY, value })
}

/* ------------------------------------------------------------------ *
 * Snapshot payload (gather / restore)
 * ------------------------------------------------------------------ */

export interface SyncPayload {
  version: 1
  profile: Record<string, unknown>
  people: unknown[]
  dates: unknown[]
  agents_md: string
  vision_md: string
}

async function coreCommand<T>(type: string, payload: Record<string, unknown>): Promise<T> {
  return (await commandWithCore(type, payload)) as T
}

/** Gather everything in §3 of the spec through the existing core IPC. */
export async function gatherSnapshot(): Promise<SyncPayload> {
  const [profileData, peopleData, datesData, agents, vision] = await Promise.all([
    coreCommand<{ profile?: Record<string, unknown> }>('get_profile', {}),
    coreCommand<{ people?: unknown[] }>('get_people', {}),
    coreCommand<{ dates?: unknown[] }>('get_dates', {}),
    coreCommand<{ content?: string }>('read_file', { path: 'AGENTS.md' }),
    coreCommand<{ content?: string }>('read_file', { path: 'VISION.md' })
  ])
  return {
    version: 1,
    profile: profileData?.profile ?? {},
    people: Array.isArray(peopleData?.people) ? peopleData.people : [],
    dates: Array.isArray(datesData?.dates) ? datesData.dates : [],
    agents_md: typeof agents?.content === 'string' ? agents.content : '',
    vision_md: typeof vision?.content === 'string' ? vision.content : ''
  }
}

/**
 * Plain sequential restore through the app's normal write paths: MD files
 * go through `write_file` (versioned + undoable), memory rows through the
 * same person/date/profile commands the Settings UI itself uses. This is
 * the raw writer — `restoreSnapshot` wraps it so a failed restore can roll
 * back to the pre-restore snapshot.
 */
async function applyRestore(payload: SyncPayload): Promise<void> {
  // Memory first (structured rows), then the two authored files.
  const profile = isRecord(payload.profile) ? payload.profile : {}
  for (const [key, value] of Object.entries(profile)) {
    await coreCommand('set_profile_memory', { key, value: String(value ?? '') })
  }

  const people = Array.isArray(payload.people) ? payload.people : []
  const existingPeople = await coreCommand<{ people?: Array<Record<string, unknown>> }>(
    'get_people',
    {}
  )
  const existingByName = new Map<string, string>()
  for (const person of existingPeople?.people ?? []) {
    if (isRecord(person) && typeof person.name === 'string') {
      existingByName.set(person.name.toLowerCase(), String(person.id ?? ''))
    }
  }
  for (const entry of people) {
    if (!isRecord(entry) || typeof entry.name !== 'string' || !entry.name.trim()) continue
    const fields = pickPersonFields(entry)
    const knownId = existingByName.get(entry.name.toLowerCase())
    if (knownId) {
      await coreCommand('update_person_memory', { person_id: knownId, fields })
    } else {
      await coreCommand('add_person_memory', { fields: { ...fields, name: entry.name } })
    }
  }

  const dates = Array.isArray(payload.dates) ? payload.dates : []
  for (const entry of dates) {
    if (!isRecord(entry)) continue
    const date = typeof entry.date === 'string' ? entry.date : ''
    const label = typeof entry.label === 'string' ? entry.label : ''
    if (!date || !label) continue
    // Dates are content-addressed enough for v1: skip exact duplicates the
    // machine already has instead of stacking copies on every restore.
    const existing = await coreCommand<{ dates?: Array<Record<string, unknown>> }>(
      'get_dates',
      {}
    )
    const duplicate = (existing?.dates ?? []).some(
      (d) => isRecord(d) && d.date === date && d.label === label
    )
    if (duplicate) continue
    await coreCommand('add_date_memory', { date, label, recurring: Boolean(entry.recurring) })
  }

  for (const [file, key] of [
    ['AGENTS.md', 'agents_md'],
    ['VISION.md', 'vision_md']
  ] as const) {
    const content = payload[key]
    if (typeof content !== 'string' || !content.trim()) continue
    await coreCommand('write_file', { path: file, content })
  }
}

/**
 * Restore a payload atomically: snapshot the current on-device state, then
 * apply the incoming payload. If the restore fails partway (a mid-way error
 * leaves only some memory rows / files written), replay the pre-restore
 * snapshot as a best-effort rollback so the device isn't left half-restored,
 * then rethrow the original error to tell the user the restore didn't finish.
 */
export async function restoreSnapshot(payload: SyncPayload): Promise<void> {
  if (!payload || payload.version !== 1) {
    throw new Error("This backup isn't a format this Collie understands.")
  }

  const before = await gatherSnapshot()
  try {
    await applyRestore(payload)
  } catch (err) {
    // Best-effort rollback: if the rollback itself fails, there's nothing
    // more we can safely do — surface the original restore error.
    try {
      await applyRestore(before)
    } catch {
      // best effort
    }
    throw err
  }
}

function pickPersonFields(person: Record<string, unknown>): Record<string, string> {
  const fields: Record<string, string> = {}
  for (const field of [
    'relationship',
    'birthday',
    'allergies',
    'preferences',
    'gift_ideas',
    'notes'
  ] as const) {
    const value = person[field]
    if (typeof value === 'string' && value.trim()) fields[field] = value
  }
  return fields
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/* ------------------------------------------------------------------ *
 * Supabase REST (user token + anon key, RLS does the rest)
 * ------------------------------------------------------------------ */

function requireSession(): StoredSessionLike {
  const session = getStoredSession()
  if (!session?.access_token) throw new Error('Sign in to Collie first.')
  return session as StoredSessionLike
}

function supabaseConfig(): { base: string; anonKey: string } {
  const base = SUPABASE_URL.replace(/\/+$/, '')
  const anonKey = SUPABASE_ANON_KEY
  if (!base || !anonKey) throw new Error('Collie account sync is not configured yet.')
  return { base, anonKey }
}

async function supabaseFetch(
  path: string,
  init: RequestInit & { session: StoredSessionLike }
): Promise<Response> {
  const session = init.session
  const { base, anonKey } = supabaseConfig()
  const headers: Record<string, string> = {
    apikey: anonKey,
    Authorization: `Bearer ${session.access_token}`,
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) ?? {})
  }
  return fetch(`${base}${path}`, {
    ...init,
    headers,
    signal: AbortSignal.timeout(HTTP_TIMEOUT_MS)
  })
}

/* ------------------------------------------------------------------ *
 * Maker liveness heartbeat (starts/stops from main/index.ts)
 * ------------------------------------------------------------------ */

let heartbeatTimer: ReturnType<typeof setInterval> | null = null

/**
 * Send one presence ping for THIS device. Requires a signed-in session (the
 * same one cloud sync uses). PATCHes the device's existing row (created by the
 * baseline upload) with only the presence columns, so the snapshot payload is
 * preserved. The caller gates on the sync toggle; this function does the I/O.
 */
export async function sendHeartbeat(): Promise<{ lastSeen: string }> {
  const session = requireSession()
  const { deviceId, deviceName } = loadDeviceIdentity()
  const userId = decodeUserId(session.access_token)
  const path =
    '/rest/v1/user_sync_snapshots' +
    `?user_id=eq.${encodeURIComponent(userId)}` +
    `&device_id=eq.${encodeURIComponent(deviceId)}`
  const response = await supabaseFetch(path, {
    method: 'PATCH',
    session,
    headers: {
      Prefer: 'return=representation'
    },
    body: JSON.stringify({
      device_name: deviceName,
      last_seen: new Date().toISOString(),
      version: app.getVersion(),
      platform: process.platform
    })
  })
  if (!response.ok) {
    throw new Error(`heartbeat failed (${response.status})`)
  }
  const rows = (await response.json().catch(() => [])) as Array<{ last_seen?: string }>
  return { lastSeen: rows?.[0]?.last_seen ?? new Date().toISOString() }
}

/**
 * Start the background liveness pings. No-ops if already running. Each tick
 * first checks the SAME opt-in gate cloud sync uses (signed in + sync on) and
 * stays completely silent on any failure — telemetry must never surface a
 * spinner, an error, or a network hang in the UI.
 */
export function startHeartbeat(): void {
  if (heartbeatTimer) return
  const tick = (): void => {
    getSyncStatus()
      .then((status) => {
        if (!status.enabled || !status.signedIn) return
        return sendHeartbeat().catch(() => undefined)
      })
      .catch(() => undefined)
  }
  // First ping shortly after launch (once the core is up to read the toggle),
  // then on the steady interval.
  tick()
  heartbeatTimer = setInterval(tick, HEARTBEAT_INTERVAL_MS)
  heartbeatTimer.unref?.()
}

/** Stop the liveness pings (called on quit; no-op if never started). */
export function stopHeartbeat(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

/* ------------------------------------------------------------------ *
 * Public API (wired to IPC by main/index.ts)
 * ------------------------------------------------------------------ */

export async function getSyncStatus(): Promise<SyncStatus> {
  let signedIn = false
  let email: string | null = null
  try {
    const session = getStoredSession()
    signedIn = Boolean(session?.access_token)
    email = signedIn ? session!.email || null : null
  } catch {
    signedIn = false
  }
  const enabled = signedIn ? await readToggle() : false
  return {
    configured: Boolean(SUPABASE_URL && SUPABASE_ANON_KEY),
    enabled,
    signedIn,
    email
  }
}

export function enableSync(enabled: boolean): Promise<SyncStatus> {
  const generation = ++syncToggleGeneration
  const transition = syncToggleQueue.then(() => applySyncToggle(enabled, generation))
  syncToggleQueue = transition.then(
    () => undefined,
    () => undefined
  )
  return transition
}

async function applySyncToggle(enabled: boolean, generation: number): Promise<SyncStatus> {
  // A newer queued request already owns the desired state. Avoid unnecessary
  // network or settings work for this stale transition.
  if (generation !== syncToggleGeneration) return getSyncStatus()

  const status = await getSyncStatus()
  if (generation !== syncToggleGeneration) return getSyncStatus()
  if (enabled && !status.signedIn) {
    throw new Error('Sign in to Collie before turning on syncing.')
  }

  if (enabled) {
    // Finish the baseline before persisting true, so a failed upload always
    // leaves sync durably off. If the user disabled sync while this upload was
    // running, the newer transition wins and true is never written.
    await uploadSnapshot()
    if (generation !== syncToggleGeneration) return getSyncStatus()
  }

  await writeToggle(enabled)
  return getSyncStatus()
}

/** Upload/replace THIS device's snapshot (upsert on user_id+device_id). */
export async function uploadSnapshot(): Promise<{ uploadedAt: string }> {
  const session = requireSession()
  const { deviceId, deviceName } = loadDeviceIdentity()
  const payload = await gatherSnapshot()

  // One row per device: the table has a UNIQUE (user_id, device_id)
  // constraint and we upsert on it (merge-duplicates), so re-uploading
  // replaces this device's snapshot — never accumulates copies.
  const response = await supabaseFetch(
    '/rest/v1/user_sync_snapshots?on_conflict=user_id,device_id',
    {
      method: 'POST',
      session,
      headers: {
        Prefer: 'resolution=merge-duplicates,return=representation'
      },
      body: JSON.stringify({
        user_id: decodeUserId(session.access_token),
        device_id: deviceId,
        device_name: deviceName,
        payload
      })
    }
  )
  if (!response.ok) {
    throw new Error(`Backup didn't go through (${response.status}). Please try again.`)
  }
  const rows = (await response.json().catch(() => [])) as Array<{ created_at?: string }>
  return { uploadedAt: rows?.[0]?.created_at ?? new Date().toISOString() }
}

/** List the account's device snapshots (this device flagged). */
export async function listSnapshots(): Promise<SyncSnapshotSummary[]> {
  const session = requireSession()
  const { deviceId } = loadDeviceIdentity()
  const response = await supabaseFetch(
    '/rest/v1/user_sync_snapshots?select=device_id,device_name,created_at&order=created_at.desc&limit=50',
    { method: 'GET', session }
  )
  if (!response.ok) {
    throw new Error(`Couldn't reach your backups (${response.status}). Please try again.`)
  }
  const rows = (await response.json().catch(() => [])) as Array<{
    device_id?: string
    device_name?: string
    created_at?: string
  }>
  return (rows ?? [])
    .filter((row) => typeof row.device_id === 'string')
    .map((row) => ({
      deviceId: String(row.device_id),
      deviceName: typeof row.device_name === 'string' ? row.device_name : 'Unknown device',
      createdAt: typeof row.created_at === 'string' ? row.created_at : null,
      isThisDevice: row.device_id === deviceId
    }))
}

/** Fetch one device's full snapshot and restore it locally. */
export async function restoreFromDevice(deviceId: string): Promise<void> {
  if (typeof deviceId !== 'string' || !deviceId.trim()) {
    throw new Error('Pick a device to restore from.')
  }
  const session = requireSession()
  const url =
    '/rest/v1/user_sync_snapshots' +
    `?select=payload&device_id=eq.${encodeURIComponent(deviceId)}` +
    '&order=created_at.desc&limit=1'
  const response = await supabaseFetch(url, { method: 'GET', session })
  if (!response.ok) {
    throw new Error(`Couldn't reach your backups (${response.status}). Please try again.`)
  }
  const rows = (await response.json().catch(() => [])) as Array<{
    payload?: unknown
  }>
  const payload = rows?.[0]?.payload
  if (!payload) throw new Error("That device doesn't have a backup to restore.")
  await restoreSnapshot(payload as SyncPayload)
}

/** Decode the `sub` claim (auth.uid) for insert payloads. Display-only JWT use. */
function decodeUserId(accessToken: string): string {
  try {
    const parts = accessToken.split('.')
    if (parts.length === 3) {
      const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')) as {
        sub?: unknown
      }
      if (typeof claims.sub === 'string' && claims.sub) return claims.sub
    }
  } catch {
    // Fall through to the explicit error.
  }
  throw new Error('Your session looks unusual — sign in again.')
}

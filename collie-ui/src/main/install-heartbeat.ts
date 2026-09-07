import { randomUUID } from 'crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { app } from 'electron'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from '../shared/account-config'

export const HEARTBEAT_INTERVAL_MS = 4 * 60_000
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
let timer: ReturnType<typeof setInterval> | null = null
let inFlight = false
let metricsInFlight = false

/** Independent of account identity; never upload names, content, or tokens. */
function installId(): string {
  const directory = app.getPath('userData')
  const file = join(directory, 'install-id')
  try {
    const id = readFileSync(file, 'utf8').trim()
    if (UUID.test(id)) return id
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
  }
  const id = randomUUID()
  mkdirSync(directory, { recursive: true })
  // Persist before sending: a write failure must not create a new row every tick.
  writeFileSync(file, id, { encoding: 'utf8', mode: 0o600 })
  return id
}

export async function sendHeartbeat(): Promise<void> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return
  const response = await fetch(`${SUPABASE_URL.replace(/\/+$/, '')}/rest/v1/rpc/record_install_heartbeat`, {
    method: 'POST',
    headers: {
      apikey: SUPABASE_ANON_KEY,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      p_install_id: installId(),
      p_version: app.getVersion(),
      p_platform: process.platform
    }),
    signal: AbortSignal.timeout(15_000)
  })
  if (!response.ok) throw new Error(`Install heartbeat failed (${response.status})`)
}

/** Reject unexpected core fields instead of forwarding telemetry records. */
export async function sendMetrics(readMetrics: () => Promise<unknown>): Promise<void> {
  if (!app.isPackaged || !SUPABASE_URL || !SUPABASE_ANON_KEY) return
  const value = await readMetrics()
  if (!value || typeof value !== 'object') return
  const snapshot = value as Record<string, unknown>
  if (typeof snapshot.source_id !== 'string' || !UUID.test(snapshot.source_id) ||
      !Array.isArray(snapshot.days) || snapshot.days.length > 35) return
  const today = new Date().toISOString().slice(0, 10)
  const cutoff = new Date(Date.now() - 34 * 86400_000).toISOString().slice(0, 10)
  const days: { day: string; runs: number; interactive_runs: number; tool_calls: number }[] = []
  for (const entry of snapshot.days) {
    if (!entry || typeof entry !== 'object') return
    const { day, runs, interactive_runs, tool_calls } = entry as Record<string, unknown>
    if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day) ||
        [runs, interactive_runs, tool_calls].some(n =>
          typeof n !== 'number' || !Number.isSafeInteger(n) || n < 0 || n > 10_000_000) ||
        (interactive_runs as number) > (runs as number)) return
    // The core and HTTP call may straddle midnight. Expired rows must not
    // poison the whole retry batch; future clock-skewed rows stay local.
    if (day < cutoff || day > today) continue
    days.push({ day, runs: runs as number, interactive_runs: interactive_runs as number,
      tool_calls: tool_calls as number })
  }
  if (!days.length) return
  const response = await fetch(`${SUPABASE_URL.replace(/\/+$/, '')}/rest/v1/rpc/record_install_metrics`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ p_install_id: installId(), p_source_id: snapshot.source_id, p_days: days }),
    signal: AbortSignal.timeout(15_000)
  })
  if (!response.ok) throw new Error(`Install metrics failed (${response.status})`)
}

export function flushMetrics(readMetrics: () => Promise<unknown>): void {
  if (metricsInFlight) return
  metricsInFlight = true
  void sendMetrics(readMetrics).catch(() => undefined).finally(() => { metricsInFlight = false })
}

export function startHeartbeat(readMetrics?: () => Promise<unknown>): void {
  // Development runs should not inflate installation counts.
  if (timer || !app.isPackaged || !SUPABASE_URL || !SUPABASE_ANON_KEY) return
  const tick = (): void => {
    if (!inFlight) {
      inFlight = true
      void sendHeartbeat().catch(() => undefined).finally(() => { inFlight = false })
    }
    // Presence must remain independent of core readiness, errors, and latency.
    if (readMetrics) flushMetrics(readMetrics)
  }
  tick()
  timer = setInterval(tick, HEARTBEAT_INTERVAL_MS)
  timer.unref?.()
}

export function stopHeartbeat(): void {
  if (timer) clearInterval(timer)
  timer = null
}

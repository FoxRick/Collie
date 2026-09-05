import { randomUUID } from 'crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { app } from 'electron'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from '../shared/account-config'

export const HEARTBEAT_INTERVAL_MS = 4 * 60_000
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
let timer: ReturnType<typeof setInterval> | null = null
let inFlight = false

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

export function startHeartbeat(): void {
  // Development runs should not inflate installation counts.
  if (timer || !app.isPackaged || !SUPABASE_URL || !SUPABASE_ANON_KEY) return
  const tick = (): void => {
    if (inFlight) return
    inFlight = true
    void sendHeartbeat().catch(() => undefined).finally(() => { inFlight = false })
  }
  tick()
  timer = setInterval(tick, HEARTBEAT_INTERVAL_MS)
  timer.unref?.()
}

export function stopHeartbeat(): void {
  if (timer) clearInterval(timer)
  timer = null
}

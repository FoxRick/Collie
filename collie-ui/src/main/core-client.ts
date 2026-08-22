/**
 * Main-process core client.
 *
 * The ONLY channel over which decrypted stored secrets travel: after the
 * core reports ready, the main process decrypts the stored secrets once
 * (secrets.ts loadSecrets) and pushes them over its own WebSocket
 * connection. The renderer never receives these values — it only learns
 * how many stored secrets exist (for boot decisions), never their content.
 *
 * User-entered keys (typed in Settings) still flow renderer -> main ->
 * core via the existing saveSecret + renderer WS path; those are fresh
 * user input, not stored long-lived secrets.
 */
import { coreState } from './python'
import { loadSecrets } from './secrets'

const CONNECT_TIMEOUT_MS = 5000
const COMMAND_TIMEOUT_MS = 10_000

let pushInFlight = false

function openCoreSocket(timeoutMs = CONNECT_TIMEOUT_MS): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const { port, token } = coreState()
    let settled = false
    const ws = new WebSocket(`ws://127.0.0.1:${port}`, [`collie-${token}`])
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      try {
        ws.close()
      } catch {
        // already closed
      }
      reject(new Error('Core connection timed out'))
    }, timeoutMs)
    ws.onopen = () => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(ws)
    }
    ws.onerror = () => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(new Error('Could not connect to the core'))
    }
  })
}

function command(
  ws: WebSocket,
  type: string,
  payload: Record<string, unknown>
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    let settled = false
    const onMessage = (event: MessageEvent): void => {
      if (settled) return
      let frame: { type?: string; id?: string; message?: string; detail?: string; data?: unknown } | null = null
      try {
        frame = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (!frame || frame.id !== id) return
      settled = true
      clearTimeout(timer)
      ws.removeEventListener('message', onMessage)
      if (frame.type === 'ok') resolve(frame.data)
      else reject(new Error(frame.message ?? frame.detail ?? `${type} failed`))
    }
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      ws.removeEventListener('message', onMessage)
      reject(new Error(`${type} timed out`))
    }, COMMAND_TIMEOUT_MS)
    ws.addEventListener('message', onMessage)
    ws.send(JSON.stringify({ type, id, ...payload }))
  })
}

/**
 * Decrypt every stored secret once and push it to the core over the
 * main process's own connection. Called once per core spawn, right after
 * readiness (see python.ts onCoreReady). A failed push degrades to
 * "re-enter keys in Settings" — it must never crash the app on boot.
 */
export async function pushStoredSecretsToCore(): Promise<void> {
  if (pushInFlight) return
  pushInFlight = true
  let ws: WebSocket | null = null
  try {
    const secrets = loadSecrets()
    const entries = Object.entries(secrets)
    if (entries.length === 0) return
    ws = await openCoreSocket()
    for (const [name, key] of entries) {
      if (name.startsWith('messenger:')) {
        const [, messenger, secretKey] = name.split(':')
        if (messenger && secretKey) {
          await command(ws, 'set_messenger_secret', { messenger, key: secretKey, value: key })
        }
      } else {
        await command(ws, 'set_api_key', { provider: name, key })
      }
    }
  } catch (error) {
    console.error('[core-client] secret push failed', error)
  } finally {
    if (ws) {
      try {
        ws.close()
      } catch {
        // already closed
      }
    }
    pushInFlight = false
  }
}

/**
 * One-off main-process command to the core over a fresh socket. Used by the
 * account cloud-sync module (gather/restore/toggle) — the same authenticated
 * WebSocket the shell already uses for secret pushes; the renderer is never
 * involved and never sees these payloads.
 */
export async function commandWithCore(
  type: string,
  payload: Record<string, unknown>
): Promise<unknown> {
  const ws = await openCoreSocket()
  try {
    return await command(ws, type, payload)
  } finally {
    try {
      ws.close()
    } catch {
      // already closed
    }
  }
}

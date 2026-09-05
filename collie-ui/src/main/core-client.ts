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
 *
 * #122: the renderer no longer speaks to the core directly. The per-boot
 * token (ipcToken, from python.ts) lives ONLY here. This module owns ONE
 * persistent, auto-reconnecting WebSocket to the core and brokers every
 * renderer command over Electron IPC:
 *   - coreSend(type, id, payload): routes a command frame to the core and
 *     resolves with the matching 'ok'/'error' reply for that id.
 *   - onCoreEvent(listener): receive core-pushed events (ready, thinking,
 *     delta, message, card, approval_requested, ...) so main can forward
 *     them to the renderer.
 * The token is never exposed to the renderer through any of these.
 */
import { coreState, ipcToken } from './python'
import { loadSecrets } from './secrets'

const CONNECT_TIMEOUT_MS = 5000
const COMMAND_TIMEOUT_MS = 10_000

// Reconnect backoff for the broker's persistent socket (matches the renderer
// transport's old constants so the UX is unchanged).
const RECONNECT_BASE_DELAY_MS = 1200
const RECONNECT_MAX_DELAY_MS = 30_000
const RECONNECT_JITTER_RATIO = 0.2
const CONNECTION_INTERRUPTED_MESSAGE =
  "Collie lost the connection before confirming that action. Check its result before trying again."

let pushInFlight = false

/** A core-pushed event frame (any CollieEvent type, loosely typed here). */
export type CoreEvent = Record<string, unknown>
/** The core command frame the renderer hands over the IPC bridge. */
export type CoreCommandFrame = { type: string; id: string; [key: string]: unknown }

function openCoreSocket(timeoutMs = CONNECT_TIMEOUT_MS): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const { port } = coreState()
    let settled = false
    const ws = new WebSocket(`ws://127.0.0.1:${port}`, [`collie-${ipcToken}`])
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

/**
 * Persistent, auto-reconnecting, single-socket broker for the core. One
 * authenticated socket (with the per-boot ipcToken, which stays in main)
 * serves every renderer command and every core-pushed event. Replies with
 * an 'ok'/'error' id matching a request resolve that request's promise;
 * everything else (thinking/delta/message/card/approval, etc.) is fanned
 * out to the onCoreEvent subscribers so main can forward it to the renderer.
 */
class CoreBroker {
  private socket: WebSocket | null = null
  private connecting: Promise<WebSocket> | null = null
  private closed = false
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectAttempts = 0
  private readonly pending = new Map<
    string,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >()
  private readonly eventListeners = new Set<(event: CoreEvent) => void>()

  onEvent(listener: (event: CoreEvent) => void): () => void {
    this.eventListeners.add(listener)
    // Try to bring the socket up so pushed events flow as soon as possible.
    this.ensureConnected().catch(() => undefined)
    return () => this.eventListeners.delete(listener)
  }

  private emit(event: CoreEvent): void {
    for (const listener of this.eventListeners) listener(event)
  }

  private ensureConnected(): Promise<WebSocket> {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      return Promise.resolve(this.socket)
    }
    if (this.connecting) return this.connecting

    const { port } = coreState()
    this.connecting = new Promise<WebSocket>((resolve, reject) => {
      let settled = false
      const ws = new WebSocket(`ws://127.0.0.1:${port}`, [`collie-${ipcToken}`])
      const timer = setTimeout(() => {
        if (settled) return
        settled = true
        this.connecting = null
        try {
          ws.close()
        } catch {
          // already closed
        }
        reject(new Error('Core connection timed out'))
      }, CONNECT_TIMEOUT_MS)

      ws.onopen = () => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        this.connecting = null
        this.socket = ws
        this.reconnectAttempts = 0
        resolve(ws)
        this.emit({ type: 'connection_opened' })
      }
      ws.onmessage = (event) => this.route(event)
      ws.onclose = () => {
        if (this.socket === ws) this.socket = null
        if (this.connecting) return
        this.failPending(new Error(CONNECTION_INTERRUPTED_MESSAGE))
        this.scheduleReconnect()
      }
      ws.onerror = () => {
        // Reject the in-flight connect promptly (don't wait for the timeout)
        // and let onclose drive the reconnect backoff.
        if (settled) return
        settled = true
        clearTimeout(timer)
        this.connecting = null
        try {
          ws.close()
        } catch {
          // already closed
        }
        reject(new Error('Could not connect to the core'))
      }
    })
    return this.connecting
  }

  private route(event: MessageEvent): void {
    let frame: CoreEvent | null = null
    try {
      frame = JSON.parse(String(event.data)) as CoreEvent
    } catch {
      return
    }
    if (!frame) return
    const id = typeof frame.id === 'string' ? frame.id : undefined
    const type = frame.type
    if (id && (type === 'ok' || type === 'error')) {
      const promise = this.pending.get(id)
      if (promise) {
        this.pending.delete(id)
        if (type === 'ok') promise.resolve(frame.data)
        else {
          const detail =
            typeof frame.message === 'string'
              ? frame.message
              : typeof frame.detail === 'string'
                ? frame.detail
                : 'Core command failed'
          promise.reject(new Error(detail))
        }
      }
      // Command replies are consumed by the caller — never fanned out, even
      // when no caller is waiting (mirrors the old renderer transport).
      return
    }
    this.emit(frame)
  }

  /** Send a command frame and resolve with its 'ok'/'error' reply. */
  async send(type: string, id: string, payload: Record<string, unknown>): Promise<unknown> {
    const ws = await this.ensureConnected()
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      try {
        ws.send(JSON.stringify({ type, id, ...payload }))
      } catch (error) {
        this.pending.delete(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  private failPending(error: Error): void {
    const requests = [...this.pending.values()]
    this.pending.clear()
    for (const request of requests) request.reject(error)
  }

  private scheduleReconnect(): void {
    if (this.closed || this.retryTimer) return
    const exponentialDelay = Math.min(
      RECONNECT_MAX_DELAY_MS,
      RECONNECT_BASE_DELAY_MS * 2 ** this.reconnectAttempts
    )
    this.reconnectAttempts += 1
    const jitter = 1 - RECONNECT_JITTER_RATIO + Math.random() * RECONNECT_JITTER_RATIO * 2
    const delayMs = Math.min(RECONNECT_MAX_DELAY_MS, Math.round(exponentialDelay * jitter))
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      this.ensureConnected().catch(() => undefined)
    }, delayMs)
  }

  stop(): void {
    this.closed = true
    if (this.retryTimer) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    this.failPending(new Error('Core connection closed'))
    if (this.socket) {
      try {
        this.socket.close()
      } catch {
        // already closed
      }
      this.socket = null
    }
  }
}

const coreBroker = new CoreBroker()

/** Send one renderer command to the core and await its reply. */
export function coreSend(frame: CoreCommandFrame): Promise<unknown> {
  const { type, id, ...payload } = frame
  if (typeof type !== 'string' || typeof id !== 'string') {
    return Promise.reject(new Error('Malformed core command frame'))
  }
  return coreBroker.send(type, id, payload)
}

/** Subscribe to core-pushed events (returns an unsubscribe). */
export function onCoreEvent(listener: (event: CoreEvent) => void): () => void {
  return coreBroker.onEvent(listener)
}

/** Cleanly tear the broker down (app quit / core shutdown). */
export function stopCoreBroker(): void {
  coreBroker.stop()
}

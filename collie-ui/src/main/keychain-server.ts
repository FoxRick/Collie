/**
 * Main-process OS keychain bridge for connected-service credentials.
 *
 * The Python core owns connector OAuth token persistence, but on macOS/Linux
 * it cannot call the OS keychain directly. This server exposes the Electron
 * shell's `safeStorage` (DPAPI on Windows, Keychain on macOS, libsecret /
 * gnome-keyring on Linux) to the core over a loopback HTTP endpoint:
 *
 *   POST /encrypt  {data: base64(utf8)}
 *   POST /decrypt  {data: base64(ciphertext)}
 *   -> {data: base64(...)}
 *
 * Security:
 * - Bound to 127.0.0.1 only.
 * - Requires `Authorization: Bearer <token>`; the token is a per-boot random
 *   value handed to the core out-of-band via env vars at spawn (python.ts),
 *   the same pattern as the authenticated core IPC WebSocket.
 * - The endpoint only starts when `secureStorageAvailable()` is true. On a
 *   Linux box with no real keyring backend (Electron's `basic_text` fallback
 *   ships a hardcoded password) it refuses to start, so the core never gets a
 *   bridge address and the connector catalog honestly gates those routes to
 *   coming-soon rather than persisting a token in plaintext.
 *
 * The bridge is process-local and cheap; connector credentials travel it only
 * at OAuth time and on connector use (load), never per message.
 */
import { safeStorage } from 'electron'
import { createServer, IncomingMessage, ServerResponse } from 'http'
import { randomBytes } from 'crypto'
import { secureStorageAvailable } from './secrets'

export interface KeychainAddress {
  port: number
  token: string
}

let server: ReturnType<typeof createServer> | null = null
let token = ''
let address: KeychainAddress | null = null

/** The bridge address for the core, or null when secure storage is unavailable. */
export function keychainAddress(): KeychainAddress | null {
  return address
}

function writeJson(res: ServerResponse, code: number, body: unknown): void {
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

/**
 * Start the loopback keychain server. Resolves to the bridge address once it
 * is listening, or null when secure storage is unavailable (so the core stays
 * gated). Safe to call once per boot; repeated calls return the same address.
 */
export async function startKeychainServer(): Promise<KeychainAddress | null> {
  if (address) return address
  if (!secureStorageAvailable()) {
    address = null
    return null
  }
  token = randomBytes(32).toString('hex')
  const serverInstance = createServer((req: IncomingMessage, res: ServerResponse) => {
    if (req.method !== 'POST' || (req.url !== '/encrypt' && req.url !== '/decrypt')) {
      writeJson(res, 404, { error: 'not found' })
      return
    }
    if (req.headers.authorization !== `Bearer ${token}`) {
      writeJson(res, 401, { error: 'unauthorized' })
      return
    }
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (chunk: string) => {
      body += chunk
    })
    req.on('end', () => {
      try {
        const parsed = JSON.parse(body) as { data?: string }
        if (typeof parsed.data !== 'string') {
          writeJson(res, 400, { error: 'missing data' })
          return
        }
        const buf = Buffer.from(parsed.data, 'base64')
        if (req.url === '/encrypt') {
          const ciphertext = safeStorage.encryptString(buf.toString('utf8'))
          writeJson(res, 200, { data: ciphertext.toString('base64') })
        } else {
          const plaintext = safeStorage.decryptString(buf)
          writeJson(res, 200, { data: Buffer.from(plaintext, 'utf8').toString('base64') })
        }
      } catch (error) {
        writeJson(res, 500, { error: String(error) })
      }
    })
  })

  const port = await new Promise<number>((resolve, reject) => {
    serverInstance.once('error', reject)
    serverInstance.listen(0, '127.0.0.1', () => {
      const addr = serverInstance.address()
      if (addr && typeof addr === 'object') resolve(addr.port)
      else reject(new Error('keychain server bound to no port'))
    })
  })
  server = serverInstance
  address = { port, token }
  return address
}

export function stopKeychainServer(): void {
  if (server) {
    try {
      server.close()
    } catch {
      // already closed
    }
  }
  server = null
  address = null
  token = ''
}

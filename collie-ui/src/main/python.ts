import { ChildProcess, spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { existsSync } from 'fs'
import { join, resolve } from 'path'
import { inspectDevPythonEnvironment } from './python-environment'
import { resetSecretsConsumption } from './secrets'
import { keychainAddress } from './keychain-server'
import {
  coreExitError,
  LineBuffer,
  parseCoreProtocolLine,
  RestartBudget
} from './core-supervision'

const IPC_PORT = Number(process.env.COLLIE_IPC_PORT || 3818)

// Per-boot secret handed to the core out-of-band; the renderer presents it
// as the WebSocket subprotocol so no other local process can drive the core.
const ipcToken = randomBytes(32).toString('hex')

let child: ChildProcess | null = null
let state: 'stopped' | 'starting' | 'running' | 'failed' = 'stopped'
let lastError = ''
let readyPort: number | null = null
let respawnTimer: NodeJS.Timeout | null = null
let healthyTimer: NodeJS.Timeout | null = null

const coreReadyListeners = new Set<() => void>()

/** Subscribe to the first 'ready' of each core spawn (fires again on respawn). */
export function onCoreReady(listener: () => void): () => void {
  coreReadyListeners.add(listener)
  return () => {
    coreReadyListeners.delete(listener)
  }
}

const MAX_ABNORMAL_EXITS = 3
const RESPAWN_DELAY_MS = 3000
const HEALTHY_WINDOW_MS = 5 * 60 * 1000
const restartBudget = new RestartBudget(MAX_ABNORMAL_EXITS, HEALTHY_WINDOW_MS)

export function coreState(): {
  state: string
  port: number
  token: string
  error: string
} {
  return { state, port: readyPort ?? IPC_PORT, token: ipcToken, error: lastError }
}

function bundledPythonCandidates(): string[] {
  const res = process.resourcesPath
  return [
    join(res, 'collie-core', 'python', 'python.exe'),
    join(res, 'collie-core', 'python', 'bin', 'python3')
  ]
}

function bundledMcpRuntime(isDev: boolean): string {
  return isDev
    ? resolve(__dirname, '../../..', 'collie-ui', '.electron-bundle', 'mcp-runtime')
    : join(process.resourcesPath, 'mcp-runtime')
}

export function findPython(isDev: boolean): string | null {
  if (isDev) {
    return inspectDevPythonEnvironment(resolve(__dirname, '../../..')).python
  }
  return bundledPythonCandidates().find((p) => existsSync(p)) ?? null
}

export function coreRoot(isDev: boolean): string {
  return isDev
    ? resolve(__dirname, '../../..', 'collie-core')
    : join(process.resourcesPath, 'collie-core')
}

export async function spawnCore(isDev: boolean): Promise<void> {
  const python = findPython(isDev)
  if (!python) {
    state = 'failed'
    lastError = isDev
      ? inspectDevPythonEnvironment(resolve(__dirname, '../../..')).error
      : 'Python core not found next to the app.'
    console.error(lastError)
    return
  }

  // A fresh core process gets one chance to collect the stored secrets.
  resetSecretsConsumption()

  const cwd = coreRoot(isDev)

  // Environment hygiene: never leak dev-machine Python paths into the core,
  // and put the bundled Node runtime first on PATH so Node MCP servers work
  // in the packaged app.
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1' }
  delete env.PYTHONPATH
  delete env.PYTHONHOME
  delete env.VIRTUAL_ENV
  const nodeDir = join(bundledMcpRuntime(isDev), 'node')
  const nodeExecutable = join(
    nodeDir,
    process.platform === 'win32' ? 'node.exe' : join('bin', 'node')
  )
  if (existsSync(nodeExecutable)) {
    const nodePathDir = process.platform === 'win32' ? nodeDir : join(nodeDir, 'bin')
    env.PATH = `${nodePathDir}${process.platform === 'win32' ? ';' : ':'}${env.PATH ?? ''}`
  }

  state = 'starting'
  readyPort = null
  if (healthyTimer) {
    clearTimeout(healthyTimer)
    healthyTimer = null
  }
  // Hand the OS keychain bridge to the core when one is available so the
  // connector catalog can enable OAuth routes on macOS/Linux. Two env vars
  // face a localhost endpoint guarded by the per-boot bearer token; absent
  // these the core keeps the routes honestly gated to coming-soon.
  const keychain = keychainAddress()
  const spawnedChild = spawn(python, ['-m', 'collie_core.runtime', '--port', String(IPC_PORT)], {
    cwd,
    env: {
      ...env,
      COLLIE_IPC_PORT: String(IPC_PORT),
      COLLIE_IPC_TOKEN: ipcToken,
      COLLIE_MCP_RUNTIME_ROOT: bundledMcpRuntime(isDev),
      ...(keychain
        ? {
            COLLIE_KEYCHAIN_PORT: String(keychain.port),
            COLLIE_KEYCHAIN_TOKEN: keychain.token
          }
        : {})
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  })
  child = spawnedChild

  const stdoutLines = new LineBuffer()
  const stderrLines = new LineBuffer()
  let finished = false
  let readySeen = false
  let processError = ''

  const handleLine = (line: string, source: 'stdout' | 'stderr'): void => {
    const trimmed = line.trim()
    if (!trimmed) return
    console.log(`[core] ${trimmed}`)
    const message = parseCoreProtocolLine(trimmed)
    if (message.kind === 'fatal' && state !== 'stopped') {
      processError = message.error
      state = 'failed'
      lastError = message.error
      return
    }
    if (source !== 'stdout' || state !== 'starting' || message.kind !== 'ready') return
    if (message.port !== null) readyPort = message.port
    state = 'running'
    if (readySeen) return
    readySeen = true
    restartBudget.markReady(Date.now())
    healthyTimer = setTimeout(() => {
      healthyTimer = null
      if (child === spawnedChild && state === 'running') {
        restartBudget.decayAfterSustainedHealth(Date.now())
      }
    }, HEALTHY_WINDOW_MS)
    for (const listener of coreReadyListeners) listener()
  }

  const flushOutput = (): void => {
    for (const line of stdoutLines.flush()) handleLine(line, 'stdout')
    for (const line of stderrLines.flush()) handleLine(line, 'stderr')
  }

  const finish = (code: number | null, error = ''): void => {
    if (finished) return
    finished = true
    flushOutput()
    if (healthyTimer) {
      clearTimeout(healthyTimer)
      healthyTimer = null
    }
    const intentional = state === 'stopped'
    if (child === spawnedChild) child = null
    const shouldRespawn = restartBudget.recordExit(code, intentional, Date.now())
    if (intentional) return
    if (error) processError = error
    state = 'failed'
    lastError = coreExitError(code, processError)
    if (shouldRespawn && !respawnTimer) {
      respawnTimer = setTimeout(() => {
        respawnTimer = null
        if (state === 'failed') void spawnCore(isDev)
      }, RESPAWN_DELAY_MS)
    }
  }

  // A failed spawn (missing DLL, bad python path) raises 'error' — without a
  // listener it would crash the whole Electron main process.
  spawnedChild.on('error', (error) => {
    console.error('[core] spawn failed', error)
    finish(null, error.message || 'Core failed to start.')
  })

  spawnedChild.stdout?.on('data', (data: Buffer) => {
    for (const line of stdoutLines.push(data)) {
      handleLine(line, 'stdout')
    }
  })
  spawnedChild.stderr?.on('data', (data: Buffer) => {
    for (const line of stderrLines.push(data)) {
      handleLine(line, 'stderr')
    }
  })

  spawnedChild.on('exit', (code) => {
    console.log(`[core] exited with code ${code}`)
    finish(code)
  })
}

export function stopCore(): void {
  state = 'stopped'
  if (respawnTimer) {
    clearTimeout(respawnTimer)
    respawnTimer = null
  }
  if (healthyTimer) {
    clearTimeout(healthyTimer)
    healthyTimer = null
  }
  if (child) {
    try {
      child.kill()
    } catch {
      // already gone
    }
    child = null
  }
}

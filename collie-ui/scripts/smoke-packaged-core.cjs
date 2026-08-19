const { existsSync, readdirSync } = require('fs')
const { spawn } = require('child_process')
const { randomBytes } = require('crypto')
const net = require('net')
const { resolve } = require('path')
const { createInterface } = require('readline')
const { findPortabilityLeaks } = require('./packaging-portability.cjs')

const uiRoot = resolve(__dirname, '..')
const repositoryRoot = resolve(uiRoot, '..')
// Auto-detect the electron-builder unpacked output (win-unpacked on Windows,
// linux-unpacked on Linux, mac/mac-arm64 on macOS) so the same smoke runs on
// every platform. COLLIE_PACKAGED_RESOURCES overrides for odd layouts.
function findMacBundleResources() {
  // macOS: electron-builder emits dist/mac[--arch]/<App>.app/Contents/Resources
  // — a bundle layout, NOT a flat dist/<dir>/resources like win/linux-unpacked.
  const macDirs = ['mac', 'mac-arm64', 'mac-x64', 'mac-universal'].map((dir) =>
    resolve(uiRoot, 'dist', dir)
  )
  for (const dir of macDirs) {
    if (!existsSync(dir)) continue
    let entries
    try {
      entries = readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (!entry.isDirectory() || !entry.name.endsWith('.app')) continue
      const candidate = resolve(dir, entry.name, 'Contents', 'Resources')
      if (existsSync(candidate)) return candidate
    }
  }
  return undefined
}
const defaultUnpackedResources =
  ['win-unpacked', 'linux-unpacked', 'mac', 'mac-arm64', 'mac-x64', 'mac-universal']
    .map((dir) => resolve(uiRoot, 'dist', dir, 'resources'))
    .find((candidate) => existsSync(candidate)) ?? findMacBundleResources()
const resources = process.env.COLLIE_PACKAGED_RESOURCES
  ? resolve(process.env.COLLIE_PACKAGED_RESOURCES)
  : (defaultUnpackedResources ?? resolve(uiRoot, 'dist', 'win-unpacked', 'resources'))
const python =
  process.platform === 'win32'
    ? resolve(resources, 'collie-core', 'python', 'python.exe')
    : resolve(resources, 'collie-core', 'python', 'bin', 'python3')
const core = resolve(resources, 'collie-core')
const smokeHome = resolve(repositoryRoot, '.pytest-tmp', `packaged-smoke-${process.pid}`)
const token = randomBytes(32).toString('hex')

function redact(value) {
  return String(value || '').replaceAll(token, '[REDACTED]')
}

function delay(ms) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms))
}

function freshPort() {
  return new Promise((resolvePort, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close((error) => {
        if (error) reject(error)
        else if (!port) reject(new Error('The OS did not assign a smoke-test port.'))
        else resolvePort(port)
      })
    })
  })
}

function assertPortableResources() {
  if (!existsSync(python)) {
    throw new Error(`Packaged Python runtime is missing: ${python}`)
  }
  const leaks = findPortabilityLeaks(core, repositoryRoot)
  if (!leaks.length) return
  const summary = leaks
    .slice(0, 20)
    .map(({ path, reason }) => `  - ${path} (${reason})`)
    .join('\n')
  throw new Error(`Packaged core contains non-portable development paths:\n${summary}`)
}

function waitForReady(child, diagnostics, requestedPort) {
  return new Promise((resolveReady, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error('Packaged core did not emit COLLIE_READY within 30 seconds.'))
    }, 30_000)
    const lines = createInterface({ input: child.stdout })

    function finish(error, port) {
      clearTimeout(timeout)
      lines.close()
      if (error) reject(error)
      else resolveReady(port)
    }

    lines.on('line', (line) => {
      diagnostics.stdout.push(redact(line))
      if (line.startsWith('COLLIE_FATAL')) {
        finish(new Error(`Packaged core reported ${redact(line)}`))
        return
      }
      if (!line.startsWith('COLLIE_READY')) return
      try {
        const payload = JSON.parse(line.slice('COLLIE_READY'.length).trim())
        if (payload.port !== requestedPort) {
          throw new Error('COLLIE_READY did not report the requested fresh port.')
        }
        finish(null, payload.port)
      } catch (error) {
        finish(error)
      }
    })
    child.once('error', (error) => finish(error))
    child.once('exit', (code) => {
      finish(new Error(`Packaged core exited before readiness (code ${code}).`))
    })
  })
}

function expectAnonymousRejection(url) {
  return new Promise((resolveRejection, reject) => {
    const socket = new WebSocket(url)
    let opened = false
    const timeout = setTimeout(() => {
      socket.close()
      reject(new Error('Anonymous WebSocket handshake did not settle.'))
    }, 5_000)

    function acceptedRejection() {
      clearTimeout(timeout)
      if (!opened) resolveRejection()
    }

    socket.addEventListener('open', () => {
      opened = true
      clearTimeout(timeout)
      socket.close()
      reject(new Error('Packaged core accepted an unauthenticated WebSocket.'))
    }, { once: true })
    socket.addEventListener('error', acceptedRejection, { once: true })
    socket.addEventListener('close', acceptedRejection, { once: true })
  })
}

function authenticatedPing(url) {
  return new Promise((resolvePing, reject) => {
    const socket = new WebSocket(url, [`collie-${token}`])
    const requestId = 'packaged-smoke-ping'
    const timeout = setTimeout(() => {
      socket.close()
      reject(new Error('Authenticated packaged-core ping timed out.'))
    }, 8_000)

    function fail(error) {
      clearTimeout(timeout)
      socket.close()
      reject(error)
    }

    socket.addEventListener('open', () => {
      if (socket.protocol !== `collie-${token}`) {
        fail(new Error('Packaged core did not negotiate the authenticated subprotocol.'))
        return
      }
      socket.send(JSON.stringify({ type: 'ping', id: requestId }))
    }, { once: true })
    socket.addEventListener('message', (event) => {
      let frame
      try {
        frame = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (frame.id !== requestId) return
      clearTimeout(timeout)
      socket.close()
      if (frame.type === 'ok' && frame.data?.pong === true) resolvePing()
      else reject(new Error('Packaged core returned an invalid ping response.'))
    })
    socket.addEventListener('error', () => {
      fail(new Error('Authenticated packaged-core WebSocket failed.'))
    }, { once: true })
  })
}

async function stopChild(child) {
  if (child.exitCode !== null) return
  const exited = new Promise((resolveExit) => child.once('exit', resolveExit))
  child.kill()
  await Promise.race([exited, delay(3_000)])
}

async function main() {
  assertPortableResources()
  const requestedPort = await freshPort()
  const diagnostics = { stdout: [], stderr: [] }
  const child = spawn(
    python,
    ['-m', 'collie_core.runtime', '--port', String(requestedPort)],
    {
      cwd: core,
      env: {
        ...process.env,
        COLLIE_HOME: smokeHome,
        COLLIE_IPC_TOKEN: token,
        COLLIE_MCP_RUNTIME_ROOT: resolve(resources, 'mcp-runtime'),
        PYTHONDONTWRITEBYTECODE: '1'
      },
      windowsHide: true
    }
  )
  child.stderr.on('data', (chunk) => diagnostics.stderr.push(redact(chunk)))

  try {
    const port = await waitForReady(child, diagnostics, requestedPort)
    const url = `ws://127.0.0.1:${port}`
    await expectAnonymousRejection(url)
    await authenticatedPing(url)
    console.log(
      `Packaged core smoke OK: ready on ephemeral port ${port}; ` +
      'anonymous rejected; authenticated ping passed'
    )
  } catch (error) {
    const details = [...diagnostics.stdout, ...diagnostics.stderr].filter(Boolean).join('\n')
    if (details) console.error(redact(details))
    throw error
  } finally {
    await stopChild(child)
  }
}

main().catch((error) => {
  console.error(redact(error.stack || error))
  process.exit(1)
})

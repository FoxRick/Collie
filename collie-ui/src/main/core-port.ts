import { createServer } from 'net'

const LOOPBACK = '127.0.0.1'

/** Ask the OS for an unused loopback port. */
export function freeLoopbackPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.once('error', reject)
    probe.listen(0, LOOPBACK, () => {
      const address = probe.address()
      const port = typeof address === 'object' && address ? address.port : 0
      probe.close((error) => (error ? reject(error) : resolve(port)))
    })
  })
}

export function loopbackPortAvailable(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const probe = createServer()
    probe.once('error', () => resolve(false))
    probe.listen(port, LOOPBACK, () => {
      probe.close(() => resolve(true))
    })
  })
}

/**
 * Keep the configured port while it is free, so an install has a stable
 * address, and fall back to an ephemeral one when something already owns it —
 * a core left over from a crashed session, or an unrelated process. Nothing
 * downstream hardcodes the port: the core reports the one it actually bound on
 * its READY line, and the broker connects to that.
 */
export async function resolveCorePort(preferred: number): Promise<number> {
  if (await loopbackPortAvailable(preferred)) return preferred
  return freeLoopbackPort()
}

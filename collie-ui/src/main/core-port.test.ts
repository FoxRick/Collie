import { createServer, type Server } from 'net'
import { describe, expect, it } from 'vitest'
import { freeLoopbackPort, loopbackPortAvailable, resolveCorePort } from './core-port'

async function holdPort(): Promise<{ port: number; server: Server }> {
  const server = createServer()
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  return { port: typeof address === 'object' && address ? address.port : 0, server }
}

function release(server: Server): Promise<void> {
  return new Promise((resolve) => server.close(() => resolve()))
}

describe('core port resolution', () => {
  it('reports a free port as available', async () => {
    const port = await freeLoopbackPort()
    expect(await loopbackPortAvailable(port)).toBe(true)
  })

  it('keeps the configured port when nothing owns it', async () => {
    const port = await freeLoopbackPort()
    expect(await resolveCorePort(port)).toBe(port)
  })

  it('falls back to another port when the configured one is taken', async () => {
    const held = await holdPort()
    try {
      expect(await loopbackPortAvailable(held.port)).toBe(false)
      const resolved = await resolveCorePort(held.port)
      expect(resolved).not.toBe(held.port)
      expect(await loopbackPortAvailable(resolved)).toBe(true)
    } finally {
      await release(held.server)
    }
  })
})

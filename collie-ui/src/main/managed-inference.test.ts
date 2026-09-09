import { createServer, type Server } from 'http'
import { afterEach, expect, it, vi } from 'vitest'
const state = vi.hoisted(() => {
  process.env.COLLIE_INFERENCE_URL = 'https://collie.test'
  return { signedIn: true }
})
vi.mock('./account-auth', () => ({
  getAccountState: async () => ({ signedIn: state.signedIn }),
  getStoredSession: () => state.signedIn ? { access_token: 'account-test-token' } : null
}))
import { forwardManagedInference, managedStatus } from './managed-inference'
const realFetch = globalThis.fetch
let server: Server | undefined
afterEach(async () => {
  vi.unstubAllGlobals()
  state.signedIn = true
  if (server) {
    server.closeAllConnections()
    await new Promise<void>(resolve => server!.close(() => resolve()))
    server = undefined
  }
})
async function bridge(body: string): Promise<Response> {
  server = createServer((req,res) => { void forwardManagedInference(req,res) })
  await new Promise<void>(resolve => server!.listen(0,'127.0.0.1',resolve))
  const address = server.address() as { port: number }
  return realFetch('http://127.0.0.1:'+address.port,{
    method:'POST', headers:{Authorization:'Bearer local-test-token'},body
  })
}
it('reads allowance with account token, without making an inference request',async()=>{
  const fetcher=vi.fn(async()=>new Response(JSON.stringify({available:true,remaining:10,limit:20})))
  vi.stubGlobal('fetch',fetcher)
  expect(await managedStatus()).toMatchObject({available:true,signedIn:true,remaining:10})
  expect(fetcher).toHaveBeenCalledWith('https://collie.test/v1/me/entitlements', expect.any(Object))
})
it('signed-out users cannot forward inference',async()=>{
  state.signedIn=false
  const fetcher=vi.fn()
  vi.stubGlobal('fetch',fetcher)
  expect((await bridge('{}')).status).toBe(401)
  expect(fetcher).not.toHaveBeenCalled()
})
it('streaming replaces loopback auth and creates an idempotency key',async()=>{
  const fetcher=vi.fn(async()=>new Response('data: [DONE]\n\n'))
  vi.stubGlobal('fetch',fetcher)
  expect(await (await bridge('{"model":"collie-auto"}')).text()).toContain('[DONE]')
  const [url,options]=fetcher.mock.calls[0] as unknown as [string,RequestInit]
  expect(url).toBe('https://collie.test/v1/chat/completions')
  expect(options.headers).toMatchObject({Authorization:'Bearer account-test-token'})
  expect(new Headers(options.headers).get('Idempotency-Key')).toMatch(/^[a-f0-9-]{36}$/)
  expect(options.redirect).toBe('error')
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('oversized bodies cannot reach the hosted endpoint',async()=>{
  const fetcher=vi.fn()
  vi.stubGlobal('fetch',fetcher)
  expect((await bridge('x'.repeat(129*1024))).status).toBe(413)
  expect(fetcher).not.toHaveBeenCalled()
})
it('network failure is sanitized and never retried',async()=>{
  const fetcher=vi.fn(async()=>{throw new Error('private diagnostics')})
  vi.stubGlobal('fetch',fetcher)
  const res=await bridge('{}')
  expect(res.status).toBe(503)
  expect(await res.text()).not.toContain('private')
  expect(fetcher).toHaveBeenCalledTimes(1)
})

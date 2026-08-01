import { afterEach, describe, expect, it, vi } from 'vitest'
import { CollieClient } from './ipc'

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  send = vi.fn()

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this)
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

describe('CollieClient connection startup', () => {
  afterEach(() => {
    FakeWebSocket.instances = []
    vi.unstubAllGlobals()
  })

  it('reuses an in-flight socket and flushes queued commands once', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    const reply = client.command<{ configured: boolean }>('get_status')

    client.connect()
    client.connect()

    expect(FakeWebSocket.instances).toHaveLength(1)
    const socket = FakeWebSocket.instances[0]
    socket.open()
    expect(socket.send).toHaveBeenCalledTimes(1)

    const request = JSON.parse(socket.send.mock.calls[0][0] as string)
    socket.onmessage?.({
      data: JSON.stringify({
        id: request.id,
        type: 'ok',
        data: { configured: false }
      })
    })
    await expect(reply).resolves.toEqual({ configured: false })
    client.close()
  })

  it('sends Change plan with the generated authenticated envelope id intact', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    const reply = client.changePlan('conversation-1', 'run-1')
    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    const request = JSON.parse(socket.send.mock.calls[0][0] as string)
    expect(request).toMatchObject({
      type: 'change_plan',
      id: 'c1',
      conversation_id: 'conversation-1',
      run_id: 'run-1'
    })
    socket.onmessage?.({
      data: JSON.stringify({
        id: request.id,
        type: 'ok',
        data: {
          requested: true,
          conversation_id: 'conversation-1',
          run_id: 'run-1',
          plan_id: 'plan-1',
          plan_version: 1,
          execution_mode: 'plan',
          status: 'pending_safe_boundary'
        }
      })
    })
    await expect(reply).resolves.toMatchObject({ requested: true, status: 'pending_safe_boundary' })
    client.close()
  })
})

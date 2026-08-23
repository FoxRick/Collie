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
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('reuses an in-flight socket and sends a connected command once', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)

    client.connect()
    client.connect()

    expect(FakeWebSocket.instances).toHaveLength(1)
    const socket = FakeWebSocket.instances[0]
    socket.open()
    const reply = client.command<{ configured: boolean }>('get_status')
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

  it('fails closed while disconnected and never replays the command after reconnect', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)

    const reply = client.command('toggle_automation', {
      automation_id: 'automation-1',
      enabled: true
    })
    await expect(reply).rejects.toEqual(
      expect.objectContaining({
        name: 'CollieConnectionError',
        code: 'CORE_RESTARTING',
        message: "Collie's engine is restarting. Try again in a moment."
      })
    )

    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    expect(socket.send).not.toHaveBeenCalled()
    client.close()
  })

  it('rejects in-flight commands and clears their timers when the socket closes', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()

    const reply = client.chat(null, 'Do this once')
    expect(socket.send).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(1)
    socket.close()

    await expect(reply).rejects.toEqual(
      expect.objectContaining({
        name: 'CollieConnectionError',
        code: 'CONNECTION_INTERRUPTED',
        message:
          'Collie lost the connection before confirming that action. Check its result before trying again.'
      })
    )
    // Only the reconnect timer remains; the 120-second command timer is gone.
    expect(vi.getTimerCount()).toBe(1)

    client.connect()
    const replacement = FakeWebSocket.instances[1]
    replacement.open()
    expect(replacement.send).not.toHaveBeenCalled()
    client.close()
    expect(vi.getTimerCount()).toBe(0)
    vi.useRealTimers()
  })

  it('cleans up timed-out commands without leaking their late reply to listeners', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    const listener = vi.fn()
    client.on(listener)
    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()

    const reply = client.command('get_status', {}, 50)
    const request = JSON.parse(socket.send.mock.calls[0][0] as string)
    const rejected = expect(reply).rejects.toThrow('Collie took too long to answer.')
    await vi.advanceTimersByTimeAsync(50)
    await rejected

    socket.onmessage?.({
      data: JSON.stringify({ id: request.id, type: 'error', message: 'late reply' })
    })
    expect(listener).not.toHaveBeenCalledWith(
      expect.objectContaining({ id: request.id, type: 'error' })
    )
    client.close()
  })

  it('backs reconnects off exponentially, caps the delay, and resets after open', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321, null, () => 0.5)
    client.connect()
    FakeWebSocket.instances[0].open()
    FakeWebSocket.instances[0].close()

    const delays = [1200, 2400, 4800, 9600, 19_200, 30_000, 30_000]
    for (const [index, delay] of delays.entries()) {
      const before = FakeWebSocket.instances.length
      await vi.advanceTimersByTimeAsync(delay - 1)
      expect(FakeWebSocket.instances).toHaveLength(before)
      await vi.advanceTimersByTimeAsync(1)
      expect(FakeWebSocket.instances).toHaveLength(before + 1)
      if (index < delays.length - 1) FakeWebSocket.instances.at(-1)!.close()
    }

    const recovered = FakeWebSocket.instances.at(-1)!
    recovered.open()
    recovered.close()
    const beforeReset = FakeWebSocket.instances.length
    await vi.advanceTimersByTimeAsync(1199)
    expect(FakeWebSocket.instances).toHaveLength(beforeReset)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(beforeReset + 1)
    client.close()
  })

  it('applies deterministic jitter to the reconnect delay', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321, null, () => 0)
    client.connect()
    FakeWebSocket.instances[0].open()
    FakeWebSocket.instances[0].close()

    await vi.advanceTimersByTimeAsync(959)
    expect(FakeWebSocket.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(2)
    client.close()
  })

  it('sends Change plan with the generated authenticated envelope id intact', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    const reply = client.changePlan('conversation-1', 'run-1')
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

  it('sends execute mode and the explicit file-access scope with chat', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    const reply = client.chat(
      null,
      'Organize these files',
      [],
      'execute',
      'C:\\Selected',
      { mode: 'chosen_folders', roots: ['C:\\First', 'C:\\Second'] }
    )
    const request = JSON.parse(socket.send.mock.calls[0][0] as string)
    expect(request).toMatchObject({
      type: 'chat',
      execution_mode: 'execute',
      project_path: 'C:\\Selected',
      file_access_scope: {
        mode: 'chosen_folders',
        roots: ['C:\\First', 'C:\\Second']
      }
    })
    socket.onmessage?.({
      data: JSON.stringify({
        id: request.id,
        type: 'ok',
        data: { conversation_id: 'conversation-1' }
      })
    })
    await expect(reply).resolves.toEqual({ conversation_id: 'conversation-1' })
    client.close()
  })

  it('keeps General Chat narrow when no selected folder exists', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const client = new CollieClient(4321)
    client.connect()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    const reply = client.chat(null, 'Hello')
    const request = JSON.parse(socket.send.mock.calls[0][0] as string)
    expect(request.execution_mode).toBe('execute')
    expect(request).toMatchObject({
      file_access_scope: { mode: 'selected_folder' }
    })
    expect(request).not.toHaveProperty('project_path')
    socket.onmessage?.({
      data: JSON.stringify({
        id: request.id,
        type: 'ok',
        data: { conversation_id: 'conversation-1' }
      })
    })
    await expect(reply).resolves.toEqual({ conversation_id: 'conversation-1' })
    client.close()
  })

  it('gives getSubagentActivity a short default timeout for 2 s polls', async () => {
    const client = new CollieClient(4321)
    const commandSpy = vi
      .spyOn(client, 'command')
      .mockResolvedValue({ active_agents: [], recent_agents: [] })
    void client.getSubagentActivity()
    expect(commandSpy).toHaveBeenCalledWith('get_subagent_activity', {}, 5_000)
    commandSpy.mockRestore()
  })
})

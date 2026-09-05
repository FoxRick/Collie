import { afterEach, describe, expect, it, vi } from 'vitest'
import { CollieClient } from './ipc'

interface BridgeMock {
  coreSend: ReturnType<typeof vi.fn>
  onCoreEvent: ReturnType<typeof vi.fn>
  listener: ((event: unknown) => void) | null
  unsubscribe: ReturnType<typeof vi.fn>
}

/** Install a fake preload bridge (window.collie) and expose its handlers. */
function installBridge(): BridgeMock {
  const bridge: BridgeMock = {
    coreSend: vi.fn(async () => ({})),
    onCoreEvent: vi.fn(),
    listener: null,
    unsubscribe: vi.fn()
  }
  bridge.onCoreEvent.mockImplementation((listener: (event: unknown) => void) => {
    bridge.listener = listener
    return bridge.unsubscribe
  })
  vi.stubGlobal('window', {
    collie: { coreSend: bridge.coreSend, onCoreEvent: bridge.onCoreEvent }
  })
  return bridge
}

describe('CollieClient bridge transport (#122)', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('fails closed when the bridge is unavailable', async () => {
    // No window.collie bridge installed — commands cannot be relayed to the
    // core, so the client fails closed (CORE_RESTARTING) rather than hang.
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
  })

  it('connects idempotently, subscribes once, and relays a command frame', async () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)

    client.connect()
    client.connect()

    expect(bridge.onCoreEvent).toHaveBeenCalledTimes(1)
    // `connected` only flips once the broker reports a real core socket.
    expect(client.connected).toBe(false)

    bridge.coreSend.mockResolvedValueOnce({ configured: false })
    const reply = client.command<{ configured: boolean }>('get_status')

    expect(bridge.coreSend).toHaveBeenCalledTimes(1)
    const frame = bridge.coreSend.mock.calls[0][0] as Record<string, unknown>
    expect(frame).toMatchObject({ type: 'get_status', id: 'c1' })
    await expect(reply).resolves.toEqual({ configured: false })

    client.close()
    expect(client.connected).toBe(false)
    expect(bridge.unsubscribe).toHaveBeenCalled()
  })

  it('emits connection_opened to listeners only when the broker reports the core socket open', () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)
    const listener = vi.fn()
    client.on(listener)

    client.connect()
    // Not emitted at connect() time — the broker must actually reach the core.
    expect(listener).not.toHaveBeenCalledWith({ type: 'connection_opened' })
    expect(client.connected).toBe(false)

    // The broker's core socket opens.
    bridge.listener?.({ type: 'connection_opened' })
    expect(listener).toHaveBeenCalledWith({ type: 'connection_opened' })
    expect(client.connected).toBe(true)
    client.close()
  })

  it('fans core-pushed events out to subscribed listeners', () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)
    const listener = vi.fn()
    client.on(listener)
    client.connect()

    bridge.listener?.({ type: 'thinking', state: 'working', phrase: 'going', pet_animation: 'walking' })
    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'thinking', phrase: 'going' })
    )

    client.close()
  })

  it('rejects in-flight commands when the client is closed', async () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)
    client.connect()

    let settle!: (value: unknown) => void
    bridge.coreSend.mockImplementation(
      () =>
        new Promise((resolve) => {
          settle = resolve
        })
    )
    const reply = client.chat(null, 'Do this once')

    client.close()
    await expect(reply).rejects.toEqual(
      expect.objectContaining({
        name: 'CollieConnectionError',
        code: 'CONNECTION_CLOSED',
        message: "Collie's engine connection closed."
      })
    )
    // The late core reply must not resolve the already-rejected command.
    settle({ conversation_id: 'conversation-1' })
  })

  it('cleans up timed-out commands without leaking their late reply to listeners', async () => {
    const bridge = installBridge()
    vi.useFakeTimers()
    const client = new CollieClient(4321)
    const listener = vi.fn()
    client.on(listener)
    client.connect()

    // Hold the bridge reply open so the caller's timeout fires first.
    bridge.coreSend.mockImplementation(() => new Promise(() => undefined))
    const reply = client.command('get_status', {}, 50)
    const rejected = expect(reply).rejects.toThrow('Collie took too long to answer.')
    await vi.advanceTimersByTimeAsync(50)
    await rejected

    // A late reply that arrives over the event channel is never fanned out.
    bridge.listener?.({
      id: 'c1',
      type: 'error',
      message: 'late reply'
    })
    expect(listener).not.toHaveBeenCalledWith(
      expect.objectContaining({ id: 'c1', type: 'error' })
    )
    client.close()
    vi.useRealTimers()
  })

  it('sends Change plan with the generated authenticated envelope id intact', async () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)
    client.connect()

    bridge.coreSend.mockResolvedValueOnce({
      requested: true,
      conversation_id: 'conversation-1',
      run_id: 'run-1',
      plan_id: 'plan-1',
      plan_version: 1,
      execution_mode: 'plan',
      status: 'pending_safe_boundary'
    })
    const reply = client.changePlan('conversation-1', 'run-1')
    const frame = bridge.coreSend.mock.calls[0][0] as Record<string, unknown>
    expect(frame).toMatchObject({
      type: 'change_plan',
      id: 'c1',
      conversation_id: 'conversation-1',
      run_id: 'run-1'
    })
    await expect(reply).resolves.toMatchObject({ requested: true, status: 'pending_safe_boundary' })
    client.close()
  })

  it('sends execute mode and the explicit file-access scope with chat', async () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)
    client.connect()

    bridge.coreSend.mockResolvedValueOnce({ conversation_id: 'conversation-1' })
    const reply = client.chat(
      null,
      'Organize these files',
      [],
      'execute',
      'C:\\Selected',
      { mode: 'chosen_folders', roots: ['C:\\First', 'C:\\Second'] }
    )
    const frame = bridge.coreSend.mock.calls[0][0] as Record<string, unknown>
    expect(frame).toMatchObject({
      type: 'chat',
      execution_mode: 'execute',
      project_path: 'C:\\Selected',
      file_access_scope: {
        mode: 'chosen_folders',
        roots: ['C:\\First', 'C:\\Second']
      }
    })
    await expect(reply).resolves.toEqual({ conversation_id: 'conversation-1' })
    client.close()
  })

  it('keeps General Chat narrow when no selected folder exists', async () => {
    const bridge = installBridge()
    const client = new CollieClient(4321)
    client.connect()

    bridge.coreSend.mockResolvedValueOnce({ conversation_id: 'conversation-1' })
    const reply = client.chat(null, 'Hello')
    const frame = bridge.coreSend.mock.calls[0][0] as Record<string, unknown>
    expect(frame.execution_mode).toBe('execute')
    expect(frame).toMatchObject({
      file_access_scope: { mode: 'selected_folder' }
    })
    // The wire (JSON.stringify, as main's broker does) drops the undefined
    // project_path — General Chat stays narrow.
    const wire = JSON.parse(JSON.stringify(frame))
    expect(wire).not.toHaveProperty('project_path')
    await expect(reply).resolves.toEqual({ conversation_id: 'conversation-1' })
    client.close()
  })

  it('gives getSubagentActivity a short default timeout for 2 s polls', async () => {
    installBridge()
    const client = new CollieClient(4321)
    const commandSpy = vi
      .spyOn(client, 'command')
      .mockResolvedValue({ active_agents: [], recent_agents: [] })
    void client.getSubagentActivity()
    expect(commandSpy).toHaveBeenCalledWith('get_subagent_activity', {}, 5_000)
    commandSpy.mockRestore()
  })

  it('applies the endpoint port without a token argument', () => {
    installBridge()
    const client = new CollieClient(3818)
    client.applyEndpoint(4321)
    // applyEndpoint is a no-op for the bridge (main owns the port), but must
    // accept a bare port number without a token argument.
    expect(client.connected).toBe(false)
  })
})

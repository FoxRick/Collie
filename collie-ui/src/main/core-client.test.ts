import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./python', () => ({
  coreState: vi.fn(() => ({ state: 'running', port: 3818, token: '', error: '' })),
  ipcToken: 'test-token'
}))

const loadSecretsMock = vi.fn()
vi.mock('./secrets', () => ({
  loadSecrets: (...args: unknown[]) => loadSecretsMock(...args)
}))

import { pushStoredSecretsToCore } from './core-client'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  sent: string[] = []
  closed = false
  messageListeners: Array<(event: { data: string }) => void> = []

  constructor(_url: string, _protocols?: string[]) {
    FakeWebSocket.instances.push(this)
    // Simulate the connection succeeding on the next tick.
    queueMicrotask(() => {
      this.onopen?.()
    })
  }

  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  addEventListener(type: string, listener: (event: { data: string }) => void): void {
    if (type === 'message') this.messageListeners.push(listener)
  }

  removeEventListener(): void {
    // noop
  }

  send(data: string): void {
    this.sent.push(data)
    // Auto-ack every command so the push completes.
    const frame = JSON.parse(data) as { id?: string }
    queueMicrotask(() => {
      for (const listener of this.messageListeners) {
        listener({ data: JSON.stringify({ type: 'ok', id: frame.id, data: {} }) })
      }
    })
  }

  close(): void {
    this.closed = true
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  ;(globalThis as { WebSocket: unknown }).WebSocket = FakeWebSocket
})

describe('pushStoredSecretsToCore', () => {
  it('pushes provider keys as set_api_key frames and closes the socket', async () => {
    loadSecretsMock.mockReturnValue({ deepseek: 'sk-ds-1', openai: 'sk-openai-1' })
    await pushStoredSecretsToCore()
    expect(FakeWebSocket.instances).toHaveLength(1)
    const ws = FakeWebSocket.instances[0]
    const frames = ws.sent.map((raw) => JSON.parse(raw))
    expect(frames.map((f) => f.type)).toEqual(['set_api_key', 'set_api_key'])
    expect(frames[0]).toMatchObject({ provider: 'deepseek', key: 'sk-ds-1' })
    expect(frames[1]).toMatchObject({ provider: 'openai', key: 'sk-openai-1' })
    expect(ws.closed).toBe(true)
  })

  it('routes messenger: prefixed entries to set_messenger_secret', async () => {
    loadSecretsMock.mockReturnValue({ 'messenger:telegram:token': 'bot-123:abc' })
    await pushStoredSecretsToCore()
    const ws = FakeWebSocket.instances[0]
    const frames = ws.sent.map((raw) => JSON.parse(raw))
    expect(frames).toHaveLength(1)
    expect(frames[0]).toMatchObject({
      type: 'set_messenger_secret',
      messenger: 'telegram',
      key: 'token',
      value: 'bot-123:abc'
    })
  })

  it('opens no socket when there are no stored secrets', async () => {
    loadSecretsMock.mockReturnValue({})
    await pushStoredSecretsToCore()
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('skips a second push while one is in flight (one-shot guard)', async () => {
    loadSecretsMock.mockReturnValue({ deepseek: 'sk-ds-1' })
    const first = pushStoredSecretsToCore()
    await pushStoredSecretsToCore() // should no-op
    await first
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('never throws when the core connection fails — degrades gracefully', async () => {
    loadSecretsMock.mockReturnValue({ deepseek: 'sk-ds-1' })
    const Original = FakeWebSocket
    class FailingWebSocket extends Original {
      override onopen: (() => void) | null = null
      constructor(url: string, protocols?: string[]) {
        super(url, protocols)
        queueMicrotask(() => {
          this.onerror?.()
        })
      }
    }
    ;(globalThis as { WebSocket: unknown }).WebSocket = FailingWebSocket
    await expect(pushStoredSecretsToCore()).resolves.toBeUndefined()
  })
})

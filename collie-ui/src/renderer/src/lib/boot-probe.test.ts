import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  BootProbeController,
  type BootProbeDependencies,
  type BootProbeState
} from './boot-probe'

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
} {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function dependencies(overrides: Partial<BootProbeDependencies> = {}): {
  deps: BootProbeDependencies
  states: BootProbeState[]
} {
  const states: BootProbeState[] = []
  return {
    states,
    deps: {
      getStatus: vi.fn().mockResolvedValue({ configured: true }),
      injectSecrets: vi.fn().mockResolvedValue(0),
      configure: vi.fn().mockResolvedValue({ configured: true }),
      wakeMessengers: vi.fn().mockResolvedValue(undefined),
      isConnected: () => true,
      applyState: (state) => states.push(state),
      ...overrides
    }
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('BootProbeController', () => {
  it('single-flights initial boot and rapid ready events, injecting and configuring once', async () => {
    const firstStatus = deferred<{ configured: boolean }>()
    const getStatus = vi
      .fn<() => Promise<{ configured: boolean }>>()
      .mockImplementationOnce(() => firstStatus.promise)
      .mockResolvedValue({ configured: false })
    const injectSecrets = vi.fn().mockResolvedValue(1)
    const configure = vi.fn().mockResolvedValue({ configured: true })
    const { deps, states } = dependencies({ getStatus, injectSecrets, configure })
    const controller = new BootProbeController(deps)

    const initial = controller.requestProbe()
    const firstReady = controller.requestProbe(true)
    const secondReady = controller.requestProbe(true)
    expect(firstReady).toBe(initial)
    expect(secondReady).toBe(initial)

    firstStatus.resolve({ configured: true })
    await initial

    expect(getStatus).toHaveBeenCalledTimes(2)
    expect(injectSecrets).toHaveBeenCalledOnce()
    expect(injectSecrets).toHaveBeenCalledWith(2)
    expect(configure).toHaveBeenCalledOnce()
    expect(states).toEqual([{ screen: 'app' }])
  })

  it('injects secrets and configures at most once within one generation', async () => {
    const injectSecrets = vi.fn().mockResolvedValue(1)
    const configure = vi.fn().mockResolvedValue({ configured: true })
    const { deps } = dependencies({
      getStatus: vi.fn().mockResolvedValue({ configured: false }),
      injectSecrets,
      configure
    })
    const controller = new BootProbeController(deps)

    await controller.requestProbe()
    await controller.requestProbe()

    expect(injectSecrets).toHaveBeenCalledOnce()
    expect(configure).toHaveBeenCalledOnce()
  })

  it('invalidates stale generation results and applies only the latest state', async () => {
    const staleStatus = deferred<{ configured: boolean }>()
    const getStatus = vi
      .fn<() => Promise<{ configured: boolean }>>()
      .mockImplementationOnce(() => staleStatus.promise)
      .mockResolvedValue({ configured: false })
    const { deps, states } = dependencies({
      getStatus,
      injectSecrets: vi.fn().mockResolvedValue(0)
    })
    const controller = new BootProbeController(deps)

    const active = controller.requestProbe()
    controller.requestProbe(true)
    staleStatus.resolve({ configured: true })
    await active

    expect(states).toEqual([{ screen: 'welcome' }])
  })

  it('ignores pending completions after disposal', async () => {
    const status = deferred<{ configured: boolean }>()
    const { deps, states } = dependencies({ getStatus: () => status.promise })
    const controller = new BootProbeController(deps)

    const active = controller.requestProbe()
    controller.dispose()
    status.resolve({ configured: true })
    await active

    expect(states).toEqual([])
  })

  it('cancels retry timers on disposal', async () => {
    vi.useFakeTimers()
    const { deps, states } = dependencies({
      getStatus: vi.fn().mockRejectedValue(new Error('not ready'))
    })
    const controller = new BootProbeController(deps, { maximumAttempts: 2 })

    const active = controller.requestProbe()
    await Promise.resolve()
    expect(vi.getTimerCount()).toBe(1)
    controller.dispose()
    expect(vi.getTimerCount()).toBe(0)
    await active
    expect(states).toEqual([])
  })
})
